from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from exa_py import Exa
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from app import agent, llm, verdict
from app.catalog import Embedder
from app.config import get_settings
from app.models import Product, Region, Verdict
from app.services import ProductService
from app.sources import counterpart, label
from app.store import make_store

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
    yield
    await http.aclose()
    for closeable in (store, embedder):
        if hasattr(closeable, "aclose"):
            await closeable.aclose()


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


async def read_images(images: list[UploadFile]) -> list[tuple[bytes, str]]:
    if not images:
        raise HTTPException(400, "Send at least one image")
    return [(await f.read(), f.content_type or "image/jpeg") for f in images]


@app.get("/health")
async def health(svc: ProductsDep):
    return {"ok": True, "indexed_products": len(svc.index)}


@app.get("/products/{key}")
async def get_product(key: str, svc: ProductsDep) -> Verdict:
    """Look up by barcode (or by the key returned for products found without one)."""
    product = await lookup(svc, key)
    if product is None:
        raise HTTPException(404, "Product not found. Scan the back label instead.")
    return verdict.build(product, await svc.store.get_counterpart(key, "EU"))


class IdentifyResponse(BaseModel):
    result: agent.AgentResult
    verdict: Verdict | None = None


@app.post("/identify")
async def identify(
    request: Request, svc: ProductsDep,
    images: Annotated[list[UploadFile], File(description="Photos of the front of the pack")],
) -> IdentifyResponse:
    """Front of pack -> our catalog, or an agent web search for the ingredients."""
    result = await agent.find_from_front(request.app.state.settings, svc, request.app.state.exa,
                                         await read_images(images))
    v = None
    if result.product:
        v = verdict.build(result.product, await svc.store.get_counterpart(result.product.key, "EU"))
    return IdentifyResponse(result=result, verdict=v)


@app.post("/labels/extract")
async def extract_label(
    request: Request,
    images: Annotated[list[UploadFile], File(description="Photos of the back of the pack")],
    barcode: Annotated[str | None, Form()] = None,
) -> label.LabelResult:
    """Read the label. Nothing is saved: the user reviews and POSTs to /products/confirm."""
    return await label.extract(request.app.state.settings, await read_images(images), barcode)


@app.post("/products/confirm")
async def confirm_product(product: Product, svc: ProductsDep) -> Verdict:
    """Save a user-confirmed product (from a label scan) as verified."""
    if not product.barcode:
        raise HTTPException(400, "A barcode is needed to save a product")
    product = product.model_copy(update={"verified": True, "source": "label"})
    await svc.save(product)
    return verdict.build(product, await svc.store.get_counterpart(product.key, "EU"))


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
    return verdict.build(product, found)


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
