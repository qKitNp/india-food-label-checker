import logging
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from exa_py import Exa
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from app import additive_codes, agent, llm, verdict
from app.catalog import Embedder
from app.config import get_settings
from app.research import ResearchQueue
from app.rules import substances, tips
from app.jobs import IdentifyResponse, Job, JobStore
from app.models import Product, Region, Verdict
from app.services import ProductService
from app.sources import counterpart, label
from app.store import make_store

# One JSON line per finished scan (full agent trace), picked up by journald on the server.
_scan_log = logging.getLogger("scans")
if not _scan_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("scan %(message)s"))
    _scan_log.addHandler(_handler)
    _scan_log.setLevel(logging.INFO)
    _scan_log.propagate = False

# Below this similarity, a spoken product name is treated as not found. Correct matches
# scored ~0.77-0.81 in testing; this cut-off hasn't been tuned against unrelated products yet.
VOICE_MATCH_THRESHOLD = 0.6


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    llm.export_keys(settings)
    http = httpx.AsyncClient(headers={"User-Agent": settings.off_user_agent}, timeout=15)
    store = make_store(settings)
    embedder = Embedder(settings) if settings.embeddings_enabled else None
    if embedder:
        await embedder.load()  # ~5-30 s; better at startup than on the first scan
    app.state.products = ProductService(store, http, embedder, settings.image_weight)
    await app.state.products.load_index()
    app.state.exa = Exa(settings.exa_api_key) if settings.exa_api_key else None
    app.state.jobs = JobStore(on_finish=store.save_scan_log)
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
    return await label.extract(request.app.state.settings, await read_images(images), barcode)


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
        found = await counterpart.find(request.app.state.exa, request.app.state.settings, product, region)
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
