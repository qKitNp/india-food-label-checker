"""Extract product data from photos of the pack with a vision model. The result is shown
to the user for confirmation before it counts as verified."""

from pydantic import BaseModel, Field

from app import additive_codes, llm
from app.config import Settings
from app.models import Nutrition, Product

PROMPT = """You are reading photos of an Indian packaged food product.
Extract ONLY what is printed on the pack. Never fill in from memory of the brand.

- ingredients_text: copy the ingredients list verbatim, including INS/E numbers.
- additives: every additive code in the ingredients, written as printed (e.g. "INS 338", "E951", "150d").
- nutrition: per 100 g or per 100 ml values. If only per-serving values are printed, leave them null.
- claims: marketing claims printed anywhere on the pack, verbatim (e.g. "Zero Sugar", "No Added Sugar").
- is_liquid: true if nutrition is given per 100 ml.
- legible: false if the ingredients or nutrition table can't be read reliably.
Use null for anything not visible."""


class _Extraction(BaseModel):
    name: str | None = None
    brand: str | None = None
    ingredients_text: str | None = None
    additives: list[str] = Field(default_factory=list)
    nutrition: Nutrition = Field(default_factory=Nutrition)
    claims: list[str] = Field(default_factory=list)
    is_liquid: bool | None = None
    legible: bool = True


class LabelResult(BaseModel):
    product: Product
    legible: bool
    usage: list[dict] = Field(default_factory=list)  # one llm.usage_record per LLM call


async def extract(settings: Settings, images: list[tuple[bytes, str]],
                  barcode: str | None = None, usage: list[dict] | None = None) -> LabelResult:
    usage = usage if usage is not None else []
    ex = await llm.complete_json(settings, _Extraction, PROMPT, images, usage=usage)

    additives = [c for c in (additive_codes.normalise(a) for a in ex.additives) if c]
    for code in additive_codes.find_in_text(ex.ingredients_text or ""):
        if code not in additives:
            additives.append(code)

    product = Product(
        barcode=barcode, name=ex.name, brand=ex.brand, ingredients_text=ex.ingredients_text,
        additives=additives, nutrition=ex.nutrition, claims=ex.claims, is_liquid=ex.is_liquid,
        source="label", verified=False,
    )
    return LabelResult(product=product, legible=ex.legible, usage=list(usage))
