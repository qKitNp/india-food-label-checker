import asyncio
import hashlib
import datetime as dt
import json
import logging
import re
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from exa_py import Exa
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, model_validator

from app import additive_codes, agent, ask, barcodes, llm, sanity, verdict, voice, voice_llm
from app.catalog import Embedder
from app.config import get_settings
from app.live import FRAME_TTL, MAX_SESSIONS, STALE_AFTER, LiveResult, LiveStore, SESSION_RE
from app.research import ResearchQueue
from app.rules import substances, tips
from app.jobs import IdentifyResponse, Job, JobStore
from app.limits import Limiter, client_ip, limit
from app.models import Product, ProductSummary, Region, Submission, Verdict
from app.services import ProductService
from app.sources import counterpart, label, off
from app.store import make_store

# One JSON line per finished scan (full agent trace), picked up by journald on the server.
_scan_log = logging.getLogger("scans")
if not _scan_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("scan %(message)s"))
    _scan_log.addHandler(_handler)
    _scan_log.setLevel(logging.INFO)
    _scan_log.propagate = False

_live_log = logging.getLogger("live_lookup")
if not _live_log.handlers:
    _live_handler = logging.StreamHandler()
    _live_handler.setFormatter(logging.Formatter("live %(message)s"))
    _live_log.addHandler(_live_handler)
    _live_log.setLevel(logging.INFO)
    _live_log.propagate = False

log = logging.getLogger(__name__)


def log_llm_usage(path: str, usage: list[dict]) -> None:
    """One JSON line per request that spent LLM tokens (tokens, cache hits, cost)."""
    if usage:
        log.info("llm usage path=%s %s", path, json.dumps(llm.usage_totals(usage), ensure_ascii=False))

# Below this similarity, a spoken product name is treated as not found. Correct matches
# scored ~0.77-0.81 in testing; this cut-off hasn't been tuned against unrelated products yet.
VOICE_MATCH_THRESHOLD = 0.6

# Below this image similarity, a live camera frame is treated as unknown and starts a
# background identify job instead of answering. Not yet tuned; image self-matches
# score 1.0, look-alike variants scored well below this in the catalog tests.
LIVE_IMAGE_THRESHOLD = 0.88

# Bolna drops a tool call after 10 s (trigger_api in bolna-ai/bolna) and the agent
# then improvises an answer, so every live lookup replies within this budget.
LIVE_BUDGET = 8.0
# How long a session's background lookup stays "the product we're talking about":
# asking "did you find it?" answers from it instead of re-reading the camera.
LIVE_RESEARCH_TTL = 300.0

# The agent keeps calling the wait tool while a lookup runs, so it tells the caller
# the result without being asked. Each wait is up to LIVE_BUDGET; this caps the loop.
LIVE_MAX_WAITS = 6
LIVE_KEEP_WAITING = "Say the answer, then call wait_for_lookup straight away. Do not wait for the caller to speak."

# What Bolna says when the fast path misses. Spoken verbatim by the agent. Never ask
# callers to read barcode digits aloud: long numbers are easy to mishear.
LIVE_UNKNOWN_SAY = "That one's new to me, so I'm looking it up now. It usually takes 15 to 30 seconds."
LIVE_STILL_SAY = "Still looking up {what}. About {secs} more seconds."
LIVE_SLOW_SAY = "{what} is taking longer than usual. I'll keep at it, ask me again in a minute."
LIVE_NOT_FOUND_SAY = ("I couldn't find reliable ingredients for that one online. "
                      "You can photograph its back label in the app.")
LIVE_IDLE_SAY = "I'm not looking anything up right now. Point your camera at a pack and ask me about it."
LIVE_NO_FRAME_SAY = "I can't see your camera yet. Keep the live view on and ask again."
LIVE_STALE_SAY = "I lost your camera feed. Keep pointing at the pack and ask again."
# Usually the phone has drifted off the pack (people look at the screen for the answer).
LIVE_NOT_FOOD_SAY = "I can't see a food pack in the camera right now. Point it at the front of the pack and ask again."


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    llm.export_keys(settings)
    http = httpx.AsyncClient(headers={"User-Agent": settings.off_user_agent}, timeout=15)
    app.state.http = http
    store = make_store(settings)
    embedder = Embedder(settings) if settings.embeddings_enabled else None
    if embedder:
        await embedder.load()  # ~5-30 s; better at startup than on the first scan
    app.state.products = ProductService(store, http, embedder, settings.image_weight)
    await app.state.products.load_index()
    app.state.exa = Exa(settings.exa_api_key) if settings.exa_api_key else None
    app.state.jobs = JobStore(on_finish=store.save_scan_log)
    app.state.live = LiveStore()
    app.state.transcripts = voice_llm.Transcripts()
    app.state.limiter = Limiter()
    app.state.asker = ask.Asker(settings, app.state.exa, http)
    await load_substances(store)

    async def save_substance(sub: dict) -> None:
        await store.save_substance(sub)
        substances.upsert(sub)

    app.state.research = ResearchQueue(settings, app.state.exa, substances.is_known, save_substance,
                                       daily_cap=settings.research_daily_cap)
    yield
    await http.aclose()
    for closeable in (store, embedder):
        if hasattr(closeable, "aclose"):
            await closeable.aclose()


async def load_substances(store) -> None:
    """Researched substances live in Appwrite so runtime research persists. Seed entries
    missing from the store are copied in; everything stored is loaded into memory."""
    stored = await store.list_substances()
    stored_ids = {s["id"] for s in stored}
    for sub in substances.seed():
        if sub["id"] not in stored_ids:
            await store.save_substance(sub)
            stored.append(sub)
    for sub in stored:
        substances.upsert(sub)
    logging.getLogger(__name__).info("Loaded %d substances", len(stored))


def with_research(request: Request, v: Verdict) -> Verdict:
    """Queue research for additive codes we don't know yet, and say which are in progress."""
    started = set(request.app.state.research.enqueue([o.code for o in v.other_additives]))
    for o in v.other_additives:
        o.researching = o.code in started
    return v


app = FastAPI(title="Better FSSAI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(llm.LLMError)
async def llm_error(request: Request, exc: llm.LLMError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(llm.InvalidImage)
async def invalid_image(request: Request, exc: llm.InvalidImage):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Bolna calls the /voice tools with whatever its tool template produced; a
    # broken template only ever shows up here, so log what it actually sent.
    if request.url.path.startswith("/voice/"):
        body = (await request.body())[:500]
        _live_log.warning("422 %s body=%r errors=%s", request.url.path, body, exc.errors())
    return await request_validation_exception_handler(request, exc)


def products(request: Request) -> ProductService:
    return request.app.state.products


ProductsDep = Annotated[ProductService, Depends(products)]


async def lookup(svc: ProductService, key: str) -> Product | None:
    try:
        return await svc.lookup(key)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Open Food Facts lookup failed: {e}") from e


MAX_IMAGES = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # the app sends ~0.2-0.4 MB after shrinking on the phone


async def read_images(images: list[UploadFile]) -> list[tuple[bytes, str]]:
    """Read and check every upload before any work starts."""
    if not images:
        raise HTTPException(400, "Send at least one image")
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"Send at most {MAX_IMAGES} images")
    files = []
    for f in images:
        data = await f.read(MAX_IMAGE_BYTES + 1)  # never read more than the limit into memory
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, f"Each image must be under {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        llm.check_image(data)  # InvalidImage -> 400
        files.append((data, f.content_type or "image/jpeg"))
    return files


@app.get("/tips")
async def get_tips() -> list[dict]:
    """Label-reading tips, each checked against the official source it links to."""
    return tips.tips()


@app.get("/health")
async def health(svc: ProductsDep):
    return {"ok": True, "indexed_products": len(svc.index)}


def summarise(product: Product) -> ProductSummary:
    if not product.ingredients_text:
        status = "needs_ingredients"
    elif sanity.check(product) and not product.verified:
        status = "data_looks_wrong"
    elif product.verified:
        status = "checked"
    else:
        status = "ok"
    return ProductSummary(
        key=product.key, barcode=product.barcode, brand=product.brand, name=product.name, source=product.source,
        verified=product.verified, status=status, needs_help=status in ("needs_ingredients", "data_looks_wrong"),
        has_image=bool(product.image_file_id or product.image_url or (product.source == "off" and product.barcode)),
        image_version=image_version(product),
    )


def image_version(product: Product) -> str | None:
    """Short tag for the current photo; the page adds it to the image URL."""
    ref = product.image_file_id or product.image_url
    return hashlib.sha1(ref.encode()).hexdigest()[:10] if ref else None


@app.get("/products")
async def list_products(svc: ProductsDep, needs_help: bool = False) -> list[ProductSummary]:
    """Every product in the catalog, those needing a shopper's help first. `needs_help=true`
    lists only products missing an ingredient list or whose data contradicts itself."""
    items = [summarise(p) for p in await svc.store.list_products()]
    if needs_help:
        items = [i for i in items if i.needs_help]
    return sorted(items, key=lambda i: (not i.needs_help, (i.brand or "").lower(), (i.name or "").lower()))


THUMB_WIDTH = 480
_thumbs: OrderedDict[str, bytes] = OrderedDict()  # small in-memory cache; Cloudflare caches too


@app.get("/products/{key}/image", response_model=None)
async def product_image(key: str, svc: ProductsDep) -> Response:
    """The product's pack photo: a shopper's scan (resized here, since the Appwrite free plan
    blocks server-side transformations) or Open Food Facts' front photo."""
    product = await svc.store.get_product(key)
    if product is None:
        raise HTTPException(404, "Product not found")
    # Photos can be replaced (a wrong match corrected), so browsers check back hourly; the
    # page also versions the URL, so a new photo shows at once.
    cache = {"Cache-Control": "public, max-age=3600"}
    if product.image_file_id:
        thumb = _thumbs.get(product.image_file_id)
        if thumb is None:
            original = await svc.store.get_image(product.image_file_id)
            if original is None:
                raise HTTPException(404, "No photo")
            thumb = await asyncio.to_thread(llm.thumbnail, original, THUMB_WIDTH)
            _thumbs[product.image_file_id] = thumb
            if len(_thumbs) > 300:
                _thumbs.popitem(last=False)
        return Response(thumb, media_type="image/webp", headers=cache)
    if not product.image_url and product.source == "off" and product.barcode:
        # Saved before we kept Open Food Facts photo links: fetch it once and store it.
        try:
            fresh = await off.fetch(svc.http, product.barcode)
        except httpx.HTTPError:
            fresh = None
        if fresh and fresh.image_url:
            product = await svc.save(product.model_copy(update={"image_url": fresh.image_url}))
    if product.image_url:
        return RedirectResponse(product.image_url, status_code=302, headers=cache)
    raise HTTPException(404, "No photo")


@app.get("/products/{key}")
async def get_product(key: str, request: Request, svc: ProductsDep) -> Verdict:
    """Look up by barcode (or by the key returned for products found without one)."""
    product = await lookup(svc, key)
    if product is None:
        raise HTTPException(404, "Product not found. Scan the back label instead.")
    return with_research(request, verdict.build(product, await svc.store.get_counterpart(key, "EU")))


class JobStarted(BaseModel):
    job_id: str
    status: str
    poll_url: str


@app.post("/identify", status_code=202, dependencies=[Depends(limit("scan"))])
async def identify(
    request: Request, svc: ProductsDep,
    images: Annotated[list[UploadFile], File(description="Photos of the front of the pack")],
) -> JobStarted:
    """Front of pack -> our catalog, or an agent web search for the ingredients.
    Runs in the background (it can take over a minute); poll GET /jobs/{job_id}."""
    files = await read_images(images)  # rejects bad uploads now, not inside the job

    async def work(steps: list[agent.Step]) -> IdentifyResponse:
        result = await agent.find_from_front(request.app.state.settings, svc, request.app.state.exa, files, steps)
        v = None
        if result.product:
            v = with_research(request, verdict.build(
                result.product, await svc.store.get_counterpart(result.product.key, "EU")))
        return IdentifyResponse(result=result, verdict=v)

    job = request.app.state.jobs.start(work)
    return JobStarted(job_id=job.id, status=job.status, poll_url=f"/jobs/{job.id}")


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> Job:
    """Progress (steps so far) and, once done, the result of a background scan."""
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job (finished jobs are kept for an hour)")
    return job


@app.post("/labels/extract", dependencies=[Depends(limit("scan"))])
async def extract_label(
    request: Request,
    images: Annotated[list[UploadFile], File(description="Photos of the back of the pack")],
    barcode: Annotated[str | None, Form()] = None,
) -> label.LabelResult:
    """Read the label. Nothing is saved: the user reviews and POSTs to /products/confirm."""
    read = await label.extract(request.app.state.settings, await read_images(images), barcode)
    log_llm_usage("/labels/extract", read.usage)
    return read


@app.post("/products/confirm", dependencies=[Depends(limit("scan"))])
async def confirm_product(product: Product, request: Request, svc: ProductsDep) -> Verdict:
    """Save a user-confirmed product (from a label scan) as verified."""
    if not product.barcode:
        raise HTTPException(400, "A barcode is needed to save a product")
    # The user may have corrected the ingredients, so derive additive codes from the confirmed text.
    additives = additive_codes.find_in_text(product.ingredients_text or "")
    product = product.model_copy(update={"verified": True, "source": "label", "additives": additives})
    await svc.save(product)
    return with_research(request, verdict.build(product, await svc.store.get_counterpart(product.key, "EU")))


class SubmissionResult(BaseModel):
    submission: Submission
    verdict: Verdict | None = None  # the updated report, when a contribution was applied


@app.post("/products/{key}/submissions", status_code=201, dependencies=[Depends(limit("scan"))])
async def submit(
    key: str, request: Request, svc: ProductsDep,
    images: Annotated[list[UploadFile], File(description="Photos of the pack: ingredients, and the barcode in one")],
    kind: Annotated[Literal["contribute", "challenge"], Form()],
    note: Annotated[str | None, Form(max_length=1000)] = None,
) -> SubmissionResult:
    """A shopper adds a missing ingredient list ("contribute", applied at once) or disputes ours
    ("challenge", kept for review). The barcode must be readable in one of the photos and
    match the product, so photos of a different product are turned away."""
    product = await svc.store.get_product(key)
    if product is None:
        raise HTTPException(404, "Product not found")
    if kind == "contribute" and product.ingredients_text:
        raise HTTPException(409, "This product already has an ingredient list. You can challenge it instead.")
    files = await read_images(images)

    found = [code for data, _ in files for code in await asyncio.to_thread(barcodes.read, data)]
    if not found:
        raise HTTPException(422, "We couldn't find a barcode in your photos. Include the barcode, in focus, in one of them.")
    if product.barcode and not barcodes.matches(found, product.barcode):
        raise HTTPException(422, f"The barcode in your photo ({found[0]}) doesn't match this product ({product.barcode}).")
    barcode = product.barcode or found[0]

    read = await label.extract(request.app.state.settings, files, barcode)
    log_llm_usage(f"/products/{key}/submissions", read.usage)
    proposed = read.product
    if not proposed.ingredients_text:
        raise HTTPException(422, "We couldn't read an ingredient list in your photos. Try a sharper, closer photo.")

    current = set(verdict.build(product).product.additives)
    image_ids = [await svc.store.save_submission_image(llm.shrink(data)) for data, _ in files]
    submission = Submission(
        id=uuid.uuid4().hex, product_key=product.key, kind=kind,
        status="applied" if kind == "contribute" else "pending_review",
        barcode=barcode, note=note, current_ingredients=product.ingredients_text, proposed=proposed,
        added_codes=[c for c in proposed.additives if c not in current],
        removed_codes=[c for c in current if c not in proposed.additives],
        image_file_ids=image_ids, created_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    )
    await svc.store.save_submission(submission)

    if kind == "challenge":
        return SubmissionResult(submission=submission)
    # Contribution: the product had no ingredient list, so use the one read from the photo.
    nutrition = product.nutrition.model_copy(
        update={k: v for k, v in proposed.nutrition.model_dump().items() if v is not None})
    updated = product.model_copy(update={
        "ingredients_text": proposed.ingredients_text, "additives": proposed.additives, "nutrition": nutrition,
        "claims": proposed.claims or product.claims, "source": "label", "verified": False, "evidence": None,
        "is_liquid": proposed.is_liquid if proposed.is_liquid is not None else product.is_liquid,
    })
    updated = await svc.save(updated)
    v = with_research(request, verdict.build(updated, await svc.store.get_counterpart(updated.key, "EU")))
    return SubmissionResult(submission=submission, verdict=v)


@app.get("/products/{key}/counterpart", dependencies=[Depends(limit("compare"))])
async def get_counterpart(key: str, request: Request, svc: ProductsDep, region: Region = "EU") -> Verdict:
    if region == "IN":
        raise HTTPException(400, "Pick EU or US")
    product = await lookup(svc, key)
    if product is None:
        raise HTTPException(404, "Product not found")
    found = await svc.store.get_counterpart(key, region)
    if found is None:
        if request.app.state.exa is None:
            raise HTTPException(503, "EXA_API_KEY is not configured")
        usage: list[dict] = []
        found = await counterpart.find(request.app.state.exa, request.app.state.settings, product, region,
                                       usage=usage)
        log_llm_usage(f"/products/{key}/counterpart", usage)
        if found:
            await svc.store.save_counterpart(key, found)
    return with_research(request, verdict.build(product, found))


class VoiceLookup(BaseModel):
    barcode: str | None = None
    product_name: str | None = None

    @model_validator(mode="after")
    def one_of(self):
        if not (self.barcode or self.product_name):
            raise ValueError("Send barcode or product_name")
        return self


def is_tool_call(request: Request) -> bool:
    """Bolna sends the tool's api_token as the Authorization header. Without TOOL_TOKEN
    set (local runs, tests) every caller counts as the agent."""
    token = request.app.state.settings.tool_token
    return not token or request.headers.get("authorization", "") == f"Bearer {token}"


def require_tool(request: Request) -> None:
    """Endpoints only the voice agent calls: everyone else would bypass the rate limits."""
    if not is_tool_call(request):
        raise HTTPException(401, "voice agent tools only")


@app.post("/voice/lookup", dependencies=[Depends(require_tool)])
async def voice_lookup(body: VoiceLookup, svc: ProductsDep) -> dict:
    """Tool endpoint for the Bolna agent. Catalog and Open Food Facts only, so it answers fast."""
    product = None
    if body.barcode and (digits := "".join(ch for ch in body.barcode if ch.isdigit())):
        product = await lookup(svc, digits)
    elif body.product_name:
        matches = await svc.similar(body.product_name, k=1)
        if matches and matches[0].score >= VOICE_MATCH_THRESHOLD:
            product = await svc.store.get_product(matches[0].key)
            # Embeddings rank something first for any input, even a misheard "classic".
            # Every distinctive word the caller said must be in the product's own name.
            heard = agent.Identification(product_name=body.product_name)
            if product and not agent.mentions_product(f"{product.brand or ''} {product.name or ''}", heard):
                _live_log.info("voice lookup %r rejected match %s", body.product_name, product.key)
                product = None
    if product is None:
        return {"found": False,
                "answer": "I couldn't find that product by name. Point your camera at it and ask me about it instead."}
    v = verdict.build(product, await svc.store.get_counterpart(product.key, "EU"))
    said = voice.describe(v)
    return {"found": True, "answer": said.answer, "more": said.more}


class VoiceAsk(BaseModel):
    question: str
    product: str | None = None  # the pack being discussed, if any


@app.post("/voice/ask", dependencies=[Depends(require_tool)])
async def voice_ask(body: VoiceAsk, request: Request) -> dict:
    """Tool endpoint: a general question about food labels, ingredients or additives,
    answered in a few spoken sentences from regulators and public-health bodies."""
    question = body.question.strip()[:300]
    if not question:
        return {"answer": "What would you like to know about the label?"}
    answer = await request.app.state.asker.answer(question, (body.product or "").strip()[:100] or None)
    _live_log.info("voice ask %r -> %d chars", question[:80], len(answer))
    return {"answer": answer}


@app.post("/live/frame", dependencies=[Depends(limit("frame"))])
async def post_live_frame(
    request: Request,
    session_id: Annotated[str, Form()],
    image: Annotated[UploadFile, File(description="Latest camera frame")],
) -> dict:
    """Keep the freshest camera frame per live-voice session, in memory only.

    The browser posts about one frame per second. Frames expire after
    `FRAME_TTL` seconds of silence; nothing is stored in Appwrite.
    """
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "session_id must be 8-64 chars of a-z, A-Z, 0-9, '-' or '_'")
    files = await read_images([image])
    data, media_type = files[0]
    request.app.state.live.put(session_id, data, media_type)
    return {"ok": True, "session_id": session_id}


class LiveLookup(BaseModel):
    session_id: str
    # "page": the live page asked (typed or mic) and follows background jobs itself.
    source: Literal["voice", "page"] = "voice"


class LiveLookupResponse(BaseModel):
    answer: str  # spoken verbatim by the agent, found or not
    found: bool
    reason: Literal["barcode", "catalog_image", "name", "researched", "researching", "slow", "not_found",
                    "idle", "no_frame", "stale", "unknown", "not_food"]
    product_key: str | None = None
    job_id: str | None = None
    poll_url: str | None = None
    snap_at: float | None = None  # the frozen frame this answers, see GET /live/snap
    next_step: str | None = None  # instruction for the voice agent while a lookup runs
    more: list[str] = []  # checked facts the agent may use for follow-up questions


# Rough time left by job stage, for "give me about N more seconds".
_SECONDS_LEFT = {"queued": 25, "reading": 25, "matching": 20, "searching": 15, "checking": 5}


@app.post("/voice/live-wait", dependencies=[Depends(require_tool)])
async def voice_live_wait(body: LiveLookup, request: Request, svc: ProductsDep) -> LiveLookupResponse:
    """Tool endpoint the agent calls in a loop while a lookup runs: waits on the
    session's background lookup, never reads the camera. Lets the agent announce
    the result as soon as it's ready instead of waiting to be asked."""
    return await voice_live_lookup(body, request, svc, wait_only=True)


@app.post("/voice/live-lookup")
async def voice_live_lookup(body: LiveLookup, request: Request, svc: ProductsDep,
                            wait_only: bool = False) -> LiveLookupResponse:
    """Tool endpoint for the live camera + voice mode.

    If this session already has a background lookup, answer from it: the caller
    asking "did you find it?" means that product, whatever the camera shows now.
    Otherwise the fast path: ZXing barcode read, pack-image vector search, one
    vision read of the pack (~5 s), then catalog and Open Food Facts name search.
    Anything still unknown starts the full identify job in the background, which
    we wait on for whatever is left of LIVE_BUDGET. Every answer is also stored
    for GET /live/result, so the page can show cards for what the call says.
    """
    if not is_tool_call(request):
        # The live page asking (typed or mic), not the agent: rate limited per visitor.
        request.app.state.limiter.check("live_lookup", client_ip(request))
        body.source = "page"
    import time as _time

    if not SESSION_RE.match(body.session_id):
        raise HTTPException(400, "session_id must be 8-64 chars of a-z, A-Z, 0-9, '-' or '_'")
    deadline = _time.monotonic() + LIVE_BUDGET
    live: LiveStore = request.app.state.live
    jobs = request.app.state.jobs
    snap_at: float | None = None
    seen = ""  # what the vision read saw, for "still looking up Gatorade Orange"

    def note(resp: LiveLookupResponse, extra: str = "") -> LiveLookupResponse:
        resp.snap_at = snap_at
        live.note_result(body.session_id, LiveResult(
            answer=resp.answer, reason=resp.reason, product_key=resp.product_key,
            job_id=resp.job_id, poll_url=resp.poll_url, at=_time.time(), snap_at=snap_at))
        _live_log.info("session=%s reason=%s %s", body.session_id[-6:], resp.reason, extra)
        return resp

    async def answer_for(product: Product, reason: str, extra: str = "", prefix: str = "") -> LiveLookupResponse:
        v = verdict.build(product, await svc.store.get_counterpart(product.key, "EU"))
        said = voice.describe(v)
        return note(LiveLookupResponse(found=True, reason=reason, answer=prefix + said.answer, more=said.more,
                                       product_key=product.key), extra)

    async def settle(job, label: str = "", waits: int = 0) -> LiveLookupResponse:
        """Wait on the background job until the budget runs out, then say where it is."""
        while job.status in ("queued", "running") and _time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        extra = f"job={job.id[:8]} stage={job.stage} waits={waits}"
        if job.status in ("queued", "running"):
            if waits >= LIVE_MAX_WAITS:  # stop the agent's wait loop; the job carries on
                return note(LiveLookupResponse(found=False, reason="slow", answer=LIVE_SLOW_SAY.format(
                    what=label or "This one"), job_id=job.id, poll_url=f"/jobs/{job.id}"), extra)
            secs = _SECONDS_LEFT.get(job.stage, 15)
            return note(LiveLookupResponse(found=False, reason="researching", next_step=LIVE_KEEP_WAITING,
                                           answer=LIVE_STILL_SAY.format(what=label or "that one", secs=secs),
                                           job_id=job.id, poll_url=f"/jobs/{job.id}"), extra)
        live.clear_research(body.session_id)
        outcome = job.result.result if job.result else None
        if outcome and outcome.product:
            return await answer_for(outcome.product, "researched", extra, prefix="Found it. ")
        if outcome and outcome.status == "not_food":
            return note(LiveLookupResponse(found=False, reason="not_food", answer=LIVE_NOT_FOOD_SAY), extra)
        return note(LiveLookupResponse(found=False, reason="not_found", answer=LIVE_NOT_FOUND_SAY), extra)

    # Follow up on this session's background lookup instead of starting another.
    if (pending := live.research(body.session_id, LIVE_RESEARCH_TTL)) is not None:
        snap_at = pending.snap_at
        job = jobs.get(pending.job_id)
        if job is None:
            live.clear_research(body.session_id)
        elif job.status in ("queued", "running") or body.source == "voice":
            waits = live.count_wait(body.session_id) if wait_only else 0
            return await settle(job, pending.label, waits)
        else:  # the page already showed this job's result
            live.clear_research(body.session_id)
    if wait_only:
        return note(LiveLookupResponse(found=False, reason="idle", answer=LIVE_IDLE_SAY))

    frame = live.get(body.session_id)
    if frame is None:
        return note(LiveLookupResponse(found=False, reason="no_frame", answer=LIVE_NO_FRAME_SAY))
    stale = _time.time() - frame.received_at > STALE_AFTER
    # Freeze this exact frame: the page shows it in the chat while we work on it.
    snap_at = live.start_lookup(body.session_id, frame).at
    settings = request.app.state.settings

    async def fast_path() -> LiveLookupResponse | None:
        # The vision read (~3-5 s) runs alongside the barcode read and the CPU-bound
        # image embedding (a few seconds on 2 vCPUs); one in sequence blew the budget.
        vision = asyncio.create_task(agent.identify(settings, [(frame.data, frame.media_type)]))
        try:
            return await matched(vision)
        finally:
            vision.cancel()

    async def matched(vision: asyncio.Task) -> LiveLookupResponse | None:
        found_codes = await asyncio.to_thread(barcodes.read, frame.data)
        if found_codes:
            if (product := await lookup(svc, found_codes[0])) is not None:
                return await answer_for(product, "barcode", f"barcode={found_codes[0]}")
            _live_log.info("session=%s barcode %s not in catalog", body.session_id[-6:], found_codes[0])

        image_vector = await svc.embed_image(frame.data) if svc.embedder else None
        if image_vector is not None:
            try:
                matches = await svc.similar(image_vector=image_vector, k=1)
            except Exception:
                matches = []
            if matches:
                top = matches[0].score
                if top >= LIVE_IMAGE_THRESHOLD and (
                        (product := await svc.store.get_product(matches[0].key)) is not None):
                    return await answer_for(product, "catalog_image", f"image_score={top:.3f}")
                _live_log.info("session=%s image_top=%.3f", body.session_id[-6:], top)

        # No barcode, no pack-photo match: read the pack with vision, then search by
        # name - our catalog first, then Open Food Facts' millions of products.
        try:
            ident = await vision
        except Exception as e:  # vision trouble: fall through to the background job
            _live_log.warning("live vision read failed: %s", e)
            return None
        nonlocal seen
        brand, name = ident.brand or "", ident.product_name or ""
        # The read often repeats the brand in the name ("Thums Up" / "Thums Up").
        seen = name if brand and name.lower().startswith(brand.lower()) else f"{brand} {name}".strip()
        if not ident.is_packaged_food:
            return note(LiveLookupResponse(found=False, reason="not_food", answer=LIVE_NOT_FOOD_SAY))
        if digits := re.sub(r"\D", "", ident.barcode or ""):
            if (product := await lookup(svc, digits)) is not None:
                return await answer_for(product, "barcode", f"vision_barcode={digits}")
        query = " ".join(x for x in (ident.brand, ident.product_name) if x)
        if not query:
            return None
        try:
            name_matches = await svc.similar(query, k=1)
        except Exception:
            name_matches = []
        if name_matches and name_matches[0].score >= VOICE_MATCH_THRESHOLD:
            # Sibling variants embed close ("Chilli Chatka" vs "Masala Munch" ~0.7),
            # so the variant words must match too, as for Open Food Facts below.
            product = await svc.store.get_product(name_matches[0].key)
            if product is not None and agent.mentions_product(f"{product.brand or ''} {product.name or ''}", ident):
                return await answer_for(product, "name",
                                        f"query={query!r} name_score={name_matches[0].score:.3f}")
        try:
            candidates = await off.search_by_name(svc.http, query)
        except Exception as e:
            _live_log.warning("OFF name search failed: %s", e)
            candidates = []
        for cand in candidates:
            if cand.ingredients_text and agent.mentions_product(
                    f"{cand.brand or ''} {cand.name or ''}", ident):
                return await answer_for(await svc.save(cand), "name", f"query={query!r} off={cand.barcode}")
        _live_log.info("session=%s not matched: query=%r", body.session_id[-6:], query)
        return None

    try:
        # Leave a little of the budget to start the job and reply.
        if (resp := await asyncio.wait_for(fast_path(), deadline - _time.monotonic() - 0.5)) is not None:
            return resp
    except TimeoutError:
        _live_log.info("session=%s fast path over budget", body.session_id[-6:])

    async def work(steps: list[agent.Step]) -> IdentifyResponse:
        result = await agent.find_from_front(request.app.state.settings, svc, request.app.state.exa,
                                             [(frame.data, frame.media_type)], steps)
        v = None
        if result.product:
            v = with_research(request, verdict.build(
                result.product, await svc.store.get_counterpart(result.product.key, "EU")))
        return IdentifyResponse(result=result, verdict=v)

    job = jobs.start(work)
    live.set_research(body.session_id, job.id, snap_at, seen)
    if stale:
        return note(LiveLookupResponse(found=False, reason="stale", answer=LIVE_STALE_SAY,
                                       job_id=job.id, poll_url=f"/jobs/{job.id}"))
    resp = await settle(job, seen)
    if resp.reason == "researching":  # first word on a new product: set expectations
        resp.reason, resp.answer = "unknown", LIVE_UNKNOWN_SAY
        live.note_result(body.session_id, LiveResult(
            answer=resp.answer, reason=resp.reason, product_key=None, job_id=job.id,
            poll_url=resp.poll_url, at=_time.time(), snap_at=snap_at))
    return resp


class LiveResultResponse(BaseModel):
    found: bool
    answer: str | None = None
    reason: str | None = None
    product_key: str | None = None
    job_id: str | None = None
    poll_url: str | None = None
    at: float | None = None
    snap_at: float | None = None  # the snap this answer is for
    looking: bool = False  # a lookup is running on the latest snap
    latest_snap_at: float | None = None


@app.get("/live/result")
async def live_result(request: Request, session_id: str) -> LiveResultResponse:
    """Last tool answer for a session, so the web page can show cards for what
    the Bolna voice call says out loud (the page can't hear the call)."""
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "session_id must be 8-64 chars of a-z, A-Z, 0-9, '-' or '_'")
    live: LiveStore = request.app.state.live
    snap = live.snap(session_id)
    progress = {"looking": session_id in live.looking, "latest_snap_at": snap.at if snap else None}
    res = live.last_result(session_id)
    if res is None:
        return LiveResultResponse(found=False, **progress)
    return LiveResultResponse(found=True, answer=res.answer, reason=res.reason,
                              product_key=res.product_key, job_id=res.job_id,
                              poll_url=res.poll_url, at=res.at, snap_at=res.snap_at, **progress)


@app.get("/live/snap")
async def live_snap(request: Request, session_id: str) -> Response:
    """The frame the latest lookup worked on (in memory only, gone with the session)."""
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "session_id must be 8-64 chars of a-z, A-Z, 0-9, '-' or '_'")
    snap = request.app.state.live.snap(session_id)
    if snap is None:
        raise HTTPException(404, "No snap for this session")
    return Response(snap.data, media_type=snap.media_type, headers={"Cache-Control": "private, max-age=300"})


class LiveSessionRequest(BaseModel):
    user_data: dict = {}


@app.post("/live/session", dependencies=[Depends(limit("call"))])
async def live_session(body: LiveSessionRequest, request: Request) -> dict:
    """Mint a Bolna web-call session (thin proxy: the API key stays server-side).

    The Web Call SDK POSTs {user_data} here; we forward it so per-call variables
    (e.g. session_id) reach the agent's prompt, and return Bolna's mint JSON.
    """
    settings = request.app.state.settings
    if not settings.bolna_api_key or not settings.bolna_agent_id:
        raise HTTPException(503, "Live voice calls are not configured")
    try:
        r = await request.app.state.http.post(
            "https://api.bolna.ai/web-call/session",
            headers={"Authorization": f"Bearer {settings.bolna_api_key}"},
            json={"agent_id": settings.bolna_agent_id, "user_data": body.user_data},
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Bolna session mint failed: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"Bolna session mint failed: {r.text[:300]}")
    sid = str(body.user_data.get("session_id", ""))
    if SESSION_RE.match(sid) and voice_llm.WELCOME:
        request.app.state.transcripts.add(sid, "assistant", voice_llm.WELCOME)
    return r.json()


@app.get("/live/captions")
async def live_captions(session_id: str, request: Request, after: int = 0) -> dict:
    """What the call heard and said, exactly: the caller's words as Bolna transcribed
    them and the agent's reply as it streams. `after` is the last `rev` seen."""
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "bad session_id")
    caps = request.app.state.transcripts.since(session_id, after)
    return {"captions": [{"id": c.id, "role": c.role, "text": c.text, "final": c.final, "rev": c.rev}
                         for c in caps]}


# The voice agent's LLM (Bolna "custom LLM", OpenAI-compatible). See app/voice_llm.py.
def _llm_auth(request: Request) -> None:
    key = request.app.state.settings.llm_proxy_key
    if not key:
        raise HTTPException(503, "voice LLM proxy is not configured")
    if request.headers.get("authorization", "") != f"Bearer {key}":
        raise HTTPException(401, "bad key")


@app.get("/llm/v1/models")
async def llm_models(request: Request) -> dict:
    _llm_auth(request)
    return {"object": "list", "data": [{"id": request.app.state.settings.voice_llm_name, "object": "model"}]}


@app.post("/llm/v1/chat/completions")
async def llm_chat(request: Request):
    _llm_auth(request)
    try:
        body = await request.json()
    except ValueError as e:
        raise HTTPException(400, "send a JSON chat completion request") from e
    transcripts = request.app.state.transcripts
    sid = voice_llm.session_of(body)
    if sid:
        transcripts.note_user_turns(sid, body.get("messages", []))
    try:
        upstream = await voice_llm.open_stream(request.app.state.http, request.app.state.settings, body)
    except voice_llm.UpstreamError as e:
        _live_log.warning("voice LLM upstream failed: %s", e)
        raise HTTPException(502, "voice LLM unavailable") from e
    _live_log.info("voice LLM turn: session=%s stream=%s tools=%d messages=%d",
                   sid, bool(body.get("stream")), len(body.get("tools") or []), len(body.get("messages") or []))
    if body.get("stream"):
        return StreamingResponse(voice_llm.relay(upstream, transcripts, sid), media_type="text/event-stream")
    try:
        data = json.loads(await upstream.aread())
    finally:
        await upstream.aclose()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    if sid:
        if text := message.get("content"):
            transcripts.add(sid, "assistant", text.strip())
        for tc in message.get("tool_calls") or []:
            voice_llm.tool_line(transcripts, sid, (tc.get("function") or {}).get("name", ""))
    return data
