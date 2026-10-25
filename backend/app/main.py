import asyncio
import datetime as dt
import json
import logging
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from exa_py import Exa
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, model_validator

from app import additive_codes, agent, barcodes, llm, sanity, verdict
from app.catalog import Embedder
from app.config import get_settings
from app.live import FRAME_TTL, MAX_SESSIONS, STALE_AFTER, LiveResult, LiveStore, SESSION_RE
from app.research import ResearchQueue
from app.rules import substances, tips
from app.jobs import IdentifyResponse, Job, JobStore
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

# What Bolna says when the fast path misses. Spoken verbatim by the agent.
LIVE_UNKNOWN_SAY = ("Oh, I haven't seen this one yet. It'll take me a minute to look up. "
                    "Or show me the barcode and I'll tell you right away.")
LIVE_NO_FRAME_SAY = "I can't see your camera yet. Keep the live view on and ask again."
LIVE_STALE_SAY = "I lost your camera feed. Keep pointing at the pack and ask again."


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
    )


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
    cache = {"Cache-Control": "public, max-age=86400"}
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


@app.post("/identify", status_code=202)
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


@app.post("/labels/extract")
async def extract_label(
    request: Request,
    images: Annotated[list[UploadFile], File(description="Photos of the back of the pack")],
    barcode: Annotated[str | None, Form()] = None,
) -> label.LabelResult:
    """Read the label. Nothing is saved: the user reviews and POSTs to /products/confirm."""
    read = await label.extract(request.app.state.settings, await read_images(images), barcode)
    log_llm_usage("/labels/extract", read.usage)
    return read


@app.post("/products/confirm")
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


@app.post("/products/{key}/submissions", status_code=201)
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


@app.get("/products/{key}/counterpart")
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


@app.post("/voice/lookup")
async def voice_lookup(body: VoiceLookup, svc: ProductsDep) -> dict:
    """Tool endpoint for the Bolna agent. Catalog and Open Food Facts only, so it answers fast."""
    product = None
    if body.barcode and (digits := "".join(ch for ch in body.barcode if ch.isdigit())):
        product = await lookup(svc, digits)
    elif body.product_name:
        matches = await svc.similar(body.product_name, k=1)
        if matches and matches[0].score >= VOICE_MATCH_THRESHOLD:
            product = await svc.store.get_product(matches[0].key)
    if product is None:
        return {"found": False,
                "answer": "I couldn't find that product. Try reading the barcode digits, or scan it in the app."}
    v = verdict.build(product, await svc.store.get_counterpart(product.key, "EU"))
    return {"found": True, "answer": verdict.spoken(v)}


@app.post("/live/frame")
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


class LiveLookupResponse(BaseModel):
    found: bool
    reason: Literal["barcode", "catalog_image", "no_frame", "stale", "unknown"]
    answer: str  # spoken verbatim by the agent when found; suggested line when not
    product_key: str | None = None
    job_id: str | None = None
    poll_url: str | None = None


@app.post("/voice/live-lookup")
async def voice_live_lookup(body: LiveLookup, request: Request, svc: ProductsDep) -> LiveLookupResponse:
    """Tool endpoint for the live camera + voice mode.

    Uses the session's freshest frame: barcode read then pack-image vector search
    (both local, both fast). On a miss it starts the full identify job in the
    background and tells the agent what to say while it runs. Every answer is
    also stored for GET /live/result, so the web page can show cards for what
    the voice call says out loud.
    """
    import time as _time

    if not SESSION_RE.match(body.session_id):
        raise HTTPException(400, "session_id must be 8-64 chars of a-z, A-Z, 0-9, '-' or '_'")
    live: LiveStore = request.app.state.live
    _log = logging.getLogger("live_lookup")

    def note(resp: LiveLookupResponse, extra: str = "") -> LiveLookupResponse:
        live.note_result(body.session_id, LiveResult(
            answer=resp.answer, reason=resp.reason, product_key=resp.product_key,
            job_id=resp.job_id, poll_url=resp.poll_url, at=_time.time()))
        _log.info("session=%s reason=%s %s", body.session_id[-6:], resp.reason, extra)
        return resp

    frame = live.get(body.session_id)
    if frame is None:
        return note(LiveLookupResponse(found=False, reason="no_frame", answer=LIVE_NO_FRAME_SAY))
    stale = _time.time() - frame.received_at > STALE_AFTER

    found_codes = await asyncio.to_thread(barcodes.read, frame.data)
    if found_codes:
        product = await lookup(svc, found_codes[0])
        if product is not None:
            v = verdict.build(product, await svc.store.get_counterpart(product.key, "EU"))
            return note(LiveLookupResponse(found=True, reason="barcode", answer=verdict.spoken(v),
                                           product_key=product.key),
                        f"barcode={found_codes[0]}")
        return note(LiveLookupResponse(found=False, reason="unknown", answer=LIVE_UNKNOWN_SAY),
                    f"barcode={found_codes[0]} not_in_catalog")
    top = -1.0

    image_vector = await svc.embed_image(frame.data) if svc.embedder else None
    if image_vector is not None:
        try:
            matches = await svc.similar(image_vector=image_vector, k=1)
        except Exception:
            matches = []
        if matches:
            top = matches[0].score
        if matches and matches[0].score >= LIVE_IMAGE_THRESHOLD:
            product = await svc.store.get_product(matches[0].key)
            if product is not None:
                v = verdict.build(product, await svc.store.get_counterpart(product.key, "EU"))
                return note(LiveLookupResponse(found=True, reason="catalog_image", answer=verdict.spoken(v),
                                              product_key=product.key),
                            f"image_score={top:.3f}")

    async def work(steps: list[agent.Step]) -> IdentifyResponse:
        result = await agent.find_from_front(request.app.state.settings, svc, request.app.state.exa,
                                             [(frame.data, frame.media_type)], steps)
        v = None
        if result.product:
            v = with_research(request, verdict.build(
                result.product, await svc.store.get_counterpart(result.product.key, "EU")))
        return IdentifyResponse(result=result, verdict=v)

    job = request.app.state.jobs.start(work)
    if stale:
        return note(LiveLookupResponse(found=False, reason="stale", answer=LIVE_STALE_SAY,
                                       job_id=job.id, poll_url=f"/jobs/{job.id}"),
                    f"image_top={top:.3f}")
    return note(LiveLookupResponse(found=False, reason="unknown", answer=LIVE_UNKNOWN_SAY,
                                   job_id=job.id, poll_url=f"/jobs/{job.id}"),
                f"image_top={top:.3f}")


class LiveResultResponse(BaseModel):
    found: bool
    answer: str | None = None
    reason: str | None = None
    product_key: str | None = None
    job_id: str | None = None
    poll_url: str | None = None
    at: float | None = None


@app.get("/live/result")
async def live_result(request: Request, session_id: str) -> LiveResultResponse:
    """Last tool answer for a session, so the web page can show cards for what
    the Bolna voice call says out loud (the page can't hear the call)."""
    if not SESSION_RE.match(session_id):
        raise HTTPException(400, "session_id must be 8-64 chars of a-z, A-Z, 0-9, '-' or '_'")
    res = request.app.state.live.last_result(session_id)
    if res is None:
        return LiveResultResponse(found=False)
    return LiveResultResponse(found=True, answer=res.answer, reason=res.reason,
                              product_key=res.product_key, job_id=res.job_id,
                              poll_url=res.poll_url, at=res.at)


class LiveSessionRequest(BaseModel):
    user_data: dict = {}


@app.post("/live/session")
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
    return r.json()
