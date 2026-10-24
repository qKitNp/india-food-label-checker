"""Find a product's ingredients from a photo of the front of the pack.

1. A vision call identifies brand, product and variant (and the barcode, if visible).
2. If a barcode is visible, the normal barcode lookup handles it.
3. Otherwise an agent loop with tools: check our catalog (vector search), and if the
   exact variant isn't there, search the web and submit ingredients. Submissions are
   only accepted if the ingredients quote appears verbatim on a page the agent read,
   and nutrition numbers that aren't on that page are dropped.
"""

import asyncio
import difflib
import json
import logging
import re
import time
from typing import Literal

import litellm
import numpy as np
from exa_py import Exa
from pydantic import BaseModel, Field

from app import additive_codes, llm
from app.config import Settings
from app.models import Evidence, Nutrition, Product
from app.services import ProductService
from app.sources.counterpart import grounded

log = logging.getLogger(__name__)

IDENTIFY_PROMPT = """This is a photo of a packaged food product, probably sold in India.
Identify it from what is visible. Do not guess details you can't see.
- brand: e.g. "Kurkure", "Coca-Cola"
- product_name: product including the variant/flavour exactly as printed, e.g. "Masala Munch", "Zero Sugar".
  Leave out promotional text that isn't part of the name: "50 g extra", "new", "₹10 off", "value pack", pack size.
- net_quantity: e.g. "90 g", "300 ml", if visible
- barcode: the digits under the barcode, only if fully readable
- is_packaged_food: false if this isn't a packaged food or drink"""


class Identification(BaseModel):
    is_packaged_food: bool = True
    brand: str | None = None
    product_name: str | None = None
    net_quantity: str | None = None
    barcode: str | None = None


SYSTEM = """You find the ingredients of an Indian packaged food product for a food-safety app.
Wrong data is worse than no data: users act on it, and brands may dispute it.

Process:
1. Look at the catalog candidates. If one is the EXACT same product and variant
   (Coke Zero is not Diet Coke or regular Coke; Masala Munch is not Chilli Chatka), call
   use_catalog_product right away. Catalog entries were already checked when saved, whatever
   their source or "verified" flag, so don't search the web again for a product you matched.
   Names may differ in wording ("Kurkure Namkeen Masala Munch" = "Kurkure Masala Munch").
   Candidates show name_similarity and pack_image_similarity (the user's photo vs our stored
   pack photo). Packs of one brand look alike across variants, so a high image score alone
   never proves the variant: the variant name on the pack must match.
2. Only if no candidate is the same product, web_search for the Indian version's ingredients. Good sources, best first:
   the brand's official Indian site, then Indian retailers that show the back of pack
   (bigbasket, blinkit, amazon.in, jiomart, zepto). Read promising pages with read_page.
3. submit_product with ingredients_quote copied EXACTLY from a page you read, plus any
   nutrition values per 100 g / 100 ml shown on that page. Never use figures from memory.
4. If you can't find the exact variant's ingredients after a few searches, call give_up.

You have a limited number of steps. Always respond with a tool call."""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_catalog",
        "description": "Semantic search over products already in our database.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_catalog_product",
        "description": "Show a catalog product's stored details.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "use_catalog_product",
        "description": "Final answer: this catalog product is the exact same product and variant.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "reason": {"type": "string"}},
                       "required": ["key", "reason"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Returns URLs, titles and the start of each page.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_page",
        "description": "Read the text of a page returned by web_search.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "submit_product",
        "description": "Final answer from the web. ingredients_quote must be copied exactly from source_url.",
        "parameters": {"type": "object", "properties": {
            "brand": {"type": "string"},
            "name": {"type": "string", "description": "Product and variant"},
            "source_url": {"type": "string"},
            "ingredients_quote": {"type": "string"},
            "is_liquid": {"type": "boolean"},
            "energy_kcal": {"type": "number"}, "carbs_g": {"type": "number"}, "sugars_g": {"type": "number"},
            "added_sugars_g": {"type": "number"}, "fat_g": {"type": "number"},
            "saturated_fat_g": {"type": "number"}, "protein_g": {"type": "number"},
            "sodium_mg": {"type": "number"},
            "claims": {"type": "array", "items": {"type": "string"}, "description": "Marketing claims on the page"},
        }, "required": ["brand", "name", "source_url", "ingredients_quote"]}}},
    {"type": "function", "function": {
        "name": "give_up",
        "description": "Final answer: the exact product's ingredients couldn't be found reliably.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
]

NUTRIENTS = tuple(Nutrition.model_fields)


class Step(BaseModel):
    tool: str
    args: dict
    result: str
    ms: int | None = None  # how long the step took, for observability


class AgentResult(BaseModel):
    status: Literal["barcode", "catalog", "web", "not_found", "not_food"]
    identification: Identification
    product: Product | None = None
    reason: str = ""
    trace: list[Step] = Field(default_factory=list)


# Words that describe the product type, not the variant; retailer pages often drop them.
GENERIC_WORDS = {
    "flavour", "flavor", "flavoured", "flavored", "instant", "ready", "eat", "namkeen", "snack", "snacks",
    "crunchy", "crispy", "pack", "new", "extra", "free", "the", "and", "with", "drink", "noodles", "chips",
}


def mentions_product(page: str, ident: Identification) -> bool:
    """The page must name the brand and the distinctive words of the variant seen on the pack,
    allowing spelling drift ("Chatka" / "Chataka"), so the agent can't submit a different
    variant's page. Generic words ("instant", "flavour") aren't required."""
    words = set(re.findall(r"[a-z]+", page[:4000].lower()))
    wanted = [w for w in re.findall(r"[a-z]{3,}", f"{ident.brand or ''} {ident.product_name or ''}".lower())
              if w not in GENERIC_WORDS]
    return all(w in words or difflib.get_close_matches(w, words, n=1, cutoff=0.8) for w in wanted)


def number_on_page(value: float, text: str) -> bool:
    """True if the number appears on the page (e.g. 535, 535.0, 0.3, 0,3)."""
    forms = {f"{value:g}", f"{value:.1f}", f"{value:.2f}"}
    forms |= {f.replace(".", ",") for f in forms}
    return any(re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d]|[.,]\d)", text) for f in forms)


class _Run:
    def __init__(self, settings: Settings, products: ProductService, exa: Exa | None,
                 image_vector: np.ndarray | None = None, steps: list[Step] | None = None) -> None:
        self.settings = settings
        self.products = products
        self.exa = exa
        self.image_vector = image_vector  # the scanned photo, for catalog search
        self.steps = steps if steps is not None else []  # appended live, so callers can show progress
        self.pages: dict[str, str] = {}
        self.final: AgentResult | None = None

    async def call(self, name: str, args: dict, ident: Identification) -> str:
        match name:
            case "search_catalog":
                return await self.candidates(args["query"])
            case "get_catalog_product":
                p = await self.products.store.get_product(args["key"])
                if not p:
                    return "No product with that key."
                return p.model_dump_json(include={"brand", "name", "barcode", "ingredients_text"})
            case "use_catalog_product":
                p = await self.products.store.get_product(args["key"])
                if not p:
                    return "No product with that key. Pick a key from the candidates."
                self.final = AgentResult(status="catalog", identification=ident, product=p, reason=args.get("reason", ""))
                return "Done."
            case "web_search":
                if self.exa is None:
                    return "Web search is not configured. Call give_up."
                response = await asyncio.to_thread(
                    self.exa.search, args["query"], num_results=6, user_location="IN",
                    contents={"text": {"max_characters": 15000}},
                )
                out = []
                for r in response.results:
                    if r.text:
                        self.pages[r.url] = r.text
                    out.append({"url": r.url, "title": r.title, "start": (r.text or "")[:400]})
                return json.dumps(out)
            case "read_page":
                text = self.pages.get(args["url"])
                return text[:12000] if text else "Unknown URL. Only pages returned by web_search can be read."
            case "submit_product":
                return self.submit(args, ident)
            case "give_up":
                self.final = AgentResult(status="not_found", identification=ident, reason=args.get("reason", ""))
                return "Done."
        return f"Unknown tool {name}."

    def submit(self, args: dict, ident: Identification) -> str:
        page = self.pages.get(args.get("source_url", ""))
        if page is None:
            return "Rejected: source_url must be a page from web_search that you read."
        if not mentions_product(page, ident):
            return (f"Rejected: that page doesn't name {ident.brand} {ident.product_name} as seen on the pack. "
                    "It may be a different variant. Find a page for the exact product.")
        quote = args.get("ingredients_quote", "")
        if not grounded(quote, page):
            return "Rejected: ingredients_quote is not on that page word for word. Copy it exactly."
        values = {k: args[k] for k in NUTRIENTS if isinstance(args.get(k), (int, float))}
        kept = {k: v for k, v in values.items() if number_on_page(v, page)}
        product = Product(
            barcode=ident.barcode, brand=args.get("brand"), name=args.get("name"),
            ingredients_text=quote, additives=additive_codes.find_in_text(quote),
            nutrition=Nutrition(**kept), claims=args.get("claims") or [],
            is_liquid=args.get("is_liquid"), source="web", verified=False,
            evidence=Evidence(source_url=args["source_url"], ingredients_quote=quote),
        )
        self.final = AgentResult(status="web", identification=ident, product=product)
        dropped = sorted(set(values) - set(kept))
        return f"Accepted. Dropped values not found on the page: {dropped}" if dropped else "Accepted."

    async def candidates(self, query: str, use_photo: bool = False) -> str:
        """Top catalog matches with enough detail to confirm the variant without another step.
        With use_photo, the scanned pack image is part of the search."""
        out = []
        matches = await self.products.similar(query, self.image_vector if use_photo else None, k=3)
        for m in matches:
            p = await self.products.store.get_product(m.key)
            out.append({"key": m.key, "brand": p and p.brand, "name": p and p.name,
                        "barcode": p and p.barcode, "similarity": round(m.score, 3),
                        "name_similarity": None if m.name_score is None else round(m.name_score, 3),
                        "pack_image_similarity": None if m.image_score is None else round(m.image_score, 3),
                        "ingredients_start": p and (p.ingredients_text or "")[:150]})
        return json.dumps(out)

    async def loop(self, ident: Identification) -> AgentResult:
        query = " ".join(x for x in (ident.brand, ident.product_name) if x)
        trace = self.steps
        started = time.perf_counter()
        candidates = await self.candidates(query, use_photo=True)
        trace.append(Step(tool="search_catalog", args={"query": query, "with_photo": self.image_vector is not None},
                          result=candidates, ms=_ms(started)))
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Product seen on the front of the pack: {ident.model_dump_json()}\n"
                                        f"Catalog candidates: {candidates}"},
        ]
        for turn in range(self.settings.agent_max_steps):
            if turn == self.settings.agent_max_steps - 3:
                messages.append({"role": "user", "content": "Only 3 steps left. If a page you read has the exact "
                                 "product's ingredients, submit_product now; otherwise call give_up."})
            response = await litellm.acompletion(
                model=self.settings.llm_model, fallbacks=self.settings.llm_fallbacks,
                messages=messages, tools=TOOLS, tool_choice="auto", timeout=self.settings.llm_timeout,
            )
            message = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                messages.append({"role": "user", "content": "Respond with a tool call."})
                continue
            for tc in message.tool_calls:
                started = time.perf_counter()
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = await self.call(tc.function.name, args, ident)
                except Exception as e:  # report tool errors back to the model instead of failing the run
                    log.warning("Tool %s failed: %s", tc.function.name, e)
                    args, result = {}, f"Tool error: {e}"
                trace.append(Step(tool=tc.function.name, args=args, result=result[:500], ms=_ms(started)))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            if self.final:
                self.final.trace = list(trace)
                return self.final
        return AgentResult(status="not_found", identification=ident, reason="Ran out of steps.", trace=list(trace))


async def identify(settings: Settings, images: list[tuple[bytes, str]]) -> Identification:
    return await llm.complete_json(settings, Identification, IDENTIFY_PROMPT, images)


async def find_from_front(settings: Settings, products: ProductService, exa: Exa | None,
                          images: list[tuple[bytes, str]], steps: list[Step] | None = None) -> AgentResult:
    """Pass `steps` to watch progress: each step is appended as soon as it finishes."""
    steps = steps if steps is not None else []
    photo = llm.shrink(images[0][0])
    # Reading the pack (~5 s) and embedding the photo (~3 s on 2 vCPUs) run side by side.
    started = time.perf_counter()
    ident, image_vector = await asyncio.gather(identify(settings, images), _embed_or_none(products, photo))
    steps.append(Step(tool="read_pack", args={"photo_embedded": image_vector is not None},
                      result=ident.model_dump_json(), ms=_ms(started)))
    if not ident.is_packaged_food:
        return AgentResult(status="not_food", identification=ident, trace=list(steps))

    if ident.barcode and (digits := re.sub(r"\D", "", ident.barcode)):
        product = await products.lookup(digits)
        if product:
            return AgentResult(status="barcode", identification=ident, product=product, trace=list(steps))

    run = _Run(settings, products, exa, image_vector, steps)
    try:
        result = await asyncio.wait_for(run.loop(ident), settings.agent_timeout)
    except TimeoutError:
        return AgentResult(status="not_found", identification=ident, reason="Timed out.", trace=list(steps))
    except Exception as e:  # LiteLLM raises provider-specific exception types
        raise llm.LLMError(f"Agent failed: {e}") from e

    # Keep the scan as the product's pack photo if it has none, so the next scan matches by image too.
    if result.product and (result.status == "web" or not result.product.image_file_id):
        result.product = await products.save(result.product, image=photo, image_vector=image_vector)
    return result


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _embed_or_none(products: ProductService, photo: bytes) -> np.ndarray | None:
    try:
        return await products.embed_image(photo)
    except Exception as e:  # image search is a bonus; the name still works without it
        log.warning("Photo embedding failed: %s", e)
        return None
