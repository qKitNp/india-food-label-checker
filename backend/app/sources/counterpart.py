"""Find the EU/US version of an Indian product with Exa, and extract its ingredients.

The model must quote the ingredients verbatim, and we discard any quote that doesn't
appear in the page text Exa returned (grounding check). Official brand sites rank first.
"""

import asyncio
import re
from urllib.parse import urlparse

from exa_py import Exa
from pydantic import BaseModel

from app import additive_codes, llm
from app.config import Settings
from app.models import Counterpart, Product, Region

REGION_QUERY = {"EU": "UK OR Europe", "US": "United States"}
RETAILERS = ("amazon.", "bigbasket.", "blinkit.", "zeptonow.", "jiomart.", "flipkart.")

PROMPT = """Below is the text of a web page. We are looking for the {region} version of:
  brand: {brand}
  product: {name}

Return JSON:
- same_product: true only if the page is about this exact product (same variant, e.g. Coke Zero
  is not Diet Coke or regular Coke) sold in {region}. False for Indian versions.
- country: the country the page's product is sold in.
- product_name: as named on the page.
- ingredients_quote: the ingredients list copied EXACTLY, character for character, from the page.
  Empty string if the page has no ingredients list.

PAGE ({url}):
{text}"""


class _Extraction(BaseModel):
    same_product: bool
    country: str
    product_name: str
    ingredients_quote: str


def _words(s: str) -> str:
    """Lowercase words and numbers only, so a bulleted list on the page matches the same
    list written with commas. Every word must still appear, in the same order."""
    return " ".join(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", s.lower()))


def grounded(quote: str, page_text: str) -> bool:
    quote = _words(quote)
    return len(quote) >= 20 and f" {quote} " in f" {_words(page_text)} "


def _rank(url: str, brand: str) -> int:
    host = urlparse(url).netloc.lower()
    slug = re.sub(r"[^a-z0-9]", "", brand.lower())
    if slug and slug in re.sub(r"[^a-z0-9.]", "", host).replace(".", ""):
        return 0
    if any(r in host for r in RETAILERS):
        return 2
    return 1


async def _check(settings: Settings, product: Product, region: Region, result,
                 usage: list[dict] | None = None) -> Counterpart | None:
    if not result.text:
        return None
    ex = await llm.complete_json(settings, _Extraction, PROMPT.format(
        region=region, brand=product.brand, name=product.name, url=result.url, text=result.text,
    ), usage=usage)
    if not ex.same_product or "india" in ex.country.lower() or not grounded(ex.ingredients_quote, result.text):
        return None
    theirs = additive_codes.find_in_text(ex.ingredients_quote)
    return Counterpart(
        region=region, country=ex.country, product_name=ex.product_name,
        ingredients_quote=ex.ingredients_quote, source_url=result.url, additives=theirs,
        only_in_india=[a for a in product.additives if a not in theirs],
        only_in_counterpart=[a for a in theirs if a not in product.additives],
    )


async def find(exa: Exa, settings: Settings, product: Product, region: Region,
             usage: list[dict] | None = None) -> Counterpart | None:
    if not (product.brand and product.name):
        return None
    usage = usage if usage is not None else []
    query = f"{product.brand} {product.name} ingredients {REGION_QUERY[region]}"
    response = await asyncio.to_thread(
        exa.search, query, num_results=8, contents={"text": {"max_characters": 12000}},
    )
    results = sorted(response.results, key=lambda r: _rank(r.url, product.brand))[:3]
    checked = await asyncio.gather(
        *(_check(settings, product, region, r, usage=usage) for r in results), return_exceptions=True,
    )
    return next((c for c in checked if isinstance(c, Counterpart)), None)
