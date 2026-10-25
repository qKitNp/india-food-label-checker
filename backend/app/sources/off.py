"""Open Food Facts lookup by barcode. Data is crowd-sourced (ODbL, attribution required)
and treated as an unverified hint, never as ground truth."""

import re

import httpx

from app import additive_codes
from app.models import Nutrition, Product

API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
SEARCH_API = "https://world.openfoodfacts.org/cgi/search.pl"
FIELDS = "code,product_name,brands,ingredients_text,ingredients_text_en,additives_tags,nutriments,quantity,labels,image_front_url"
_LIQUID = re.compile(r"\d\s*(ml|cl|l|ltr|litre|liter)\b", re.IGNORECASE)


def _num(nutriments: dict, key: str) -> float | None:
    value = nutriments.get(f"{key}_100g")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def parse(p: dict) -> Product:
    n = p.get("nutriments") or {}
    sodium_g = _num(n, "sodium")
    ingredients = p.get("ingredients_text_en") or p.get("ingredients_text")
    additives = [c for c in (additive_codes.normalise(t) for t in p.get("additives_tags") or []) if c]
    for code in additive_codes.find_in_text(ingredients or ""):
        if code not in additives:
            additives.append(code)
    quantity = p.get("quantity") or ""
    return Product(
        barcode=p.get("code"),
        name=p.get("product_name"),
        brand=(p.get("brands") or "").split(",")[0].strip() or None,
        ingredients_text=ingredients,
        additives=additives,
        nutrition=Nutrition(
            energy_kcal=_num(n, "energy-kcal"),
            carbs_g=_num(n, "carbohydrates"),
            sugars_g=_num(n, "sugars"),
            added_sugars_g=_num(n, "added-sugars"),
            fat_g=_num(n, "fat"),
            saturated_fat_g=_num(n, "saturated-fat"),
            trans_fat_g=_num(n, "trans-fat"),
            protein_g=_num(n, "proteins"),
            sodium_mg=None if sodium_g is None else round(sodium_g * 1000, 1),
        ),
        claims=[c.strip() for c in (p.get("labels") or "").split(",") if c.strip()],
        is_liquid=bool(_LIQUID.search(quantity)) if quantity else None,
        source="off",
        verified=False,
        image_url=p.get("image_front_url"),
    )


async def fetch(client: httpx.AsyncClient, barcode: str) -> Product | None:
    r = await client.get(API.format(barcode=barcode), params={"fields": FIELDS})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    if body.get("status") != 1 or not body.get("product"):
        return None
    return parse(body["product"])


async def search_by_name(client: httpx.AsyncClient, query: str, limit: int = 5) -> list[Product]:
    """Name search for front-of-pack frames where no barcode is visible.

    Powers the live voice fast path: millions of crowd-sourced products instead
    of just our small catalog. Callers must check the candidate really is the
    exact variant seen on the pack before answering.
    """
    r = await client.get(SEARCH_API, params={
        "search_terms": query, "search_simple": 1, "action": "process",
        "json": 1, "page_size": limit,
    })
    r.raise_for_status()
    out = []
    for p in (r.json().get("products") or [])[:limit]:
        try:
            out.append(parse(p))
        except Exception:
            continue
    return out
