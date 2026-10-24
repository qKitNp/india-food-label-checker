"""Research a substance's regulatory status in India, the EU and the US, grounded in
official sources, and a background queue that researches additive codes as scans find them.

For each region: search official domains with Exa, have the LLM state the status with a
verbatim supporting quote, and keep it only if the quote appears on the page. Anything
not grounded stays "unknown". Used by scripts/research_substances.py for the seed list
and at runtime by ResearchQueue, so the knowledge base grows as people scan products.
"""

import asyncio
import datetime as dt
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from exa_py import Exa
from pydantic import BaseModel

from app import llm
from app.config import Settings
from app.sources.counterpart import grounded

log = logging.getLogger(__name__)

REGIONS = {
    "EU": {
        "label": "the European Union",
        "domains": ["eur-lex.europa.eu", "efsa.europa.eu", "food.ec.europa.eu", "ec.europa.eu", "europarl.europa.eu"],
        "queries": ["{name} {codes} food additive EU authorisation ban",
                    "{codes} {name} Commission Regulation amending Annex II Regulation (EC) No 1333/2008"],
    },
    "US": {
        "label": "the United States",
        "domains": ["fda.gov", "ecfr.gov", "federalregister.gov"],
        "queries": ["{name} {codes} FDA food additive status",
                    "{name} FDA revoke authorization food use"],
    },
    "IN": {
        "label": "India",
        "domains": ["fssai.gov.in", "pib.gov.in", "egazette.gov.in", "indiacode.nic.in"],
        "queries": ["{name} FSSAI food additive permitted prohibited",
                    "{name} Food Safety and Standards (Food Products Standards and Food Additives) Regulations"],
    },
}

STATUS_PROMPT = """You check food regulations. Based ONLY on the page below, what is the status of
"{name}" ({codes}) as a food ingredient or additive in {region}?

- status: "banned" (not permitted in food, or authorisation withdrawn), "restricted" (permitted
  only in some foods, at limits, or with a mandatory warning), "allowed" (permitted generally),
  or "not_stated" if this page doesn't say. Don't use outside knowledge.
- since: year the ban or restriction took effect, if the page says; else null.
- reason: one plain sentence on why, only as the page states it; else null.
- quote: the exact sentence(s) from the page that support the status, copied character for
  character. Empty if not_stated.

PAGE ({url}):
{text}"""

FACT_PROMPT = """Write one short sentence for a general reader about this food regulation,
using ONLY what the quote supports. Mention the substance and {region}. Under 30 words.
No hedging words like "reportedly", no advice, no exclamation marks.
If the status is "restricted", the substance IS allowed: say it is allowed with limits, then
give the limit (e.g. "allowed, but not in meat products" or "allowed up to 10 g per kg").
Never use "banned", "prohibited" or "not allowed" for a restricted substance.
A quote often covers just one food category (e.g. from an amending regulation), so don't say
"only" or imply that category is the only use unless the quote says so explicitly; say
"for example, ..." or "in X, ..." instead.

Substance: {name} ({codes})
Status in {region}: {status}{since}
Reason given by the source: {reason}
Quote from the source ({url}): "{quote}"
"""


class Finding(BaseModel):
    status: Literal["banned", "restricted", "allowed", "not_stated"]
    since: str | None = None
    reason: str | None = None
    quote: str = ""


class Fact(BaseModel):
    fact: str


async def research_region(exa: Exa, settings: Settings, sub: dict, region: str) -> dict:
    cfg = REGIONS[region]
    codes = " ".join(sub["codes"])
    for query in cfg["queries"]:  # a second phrasing when the first finds nothing grounded
        try:
            res = await asyncio.to_thread(
                exa.search, query.format(name=sub["name"], codes=codes).strip(), num_results=5,
                include_domains=cfg["domains"], contents={"text": {"max_characters": 20000}},
            )
        except Exception as e:
            log.warning("%s/%s search failed: %s", sub["id"], region, e)
            continue
        # exclude_sources: pages found to be out of date
        for r in [r for r in res.results if r.text and r.url not in sub.get("exclude_sources", [])][:3]:
            try:
                f = await llm.complete_json(settings, Finding, STATUS_PROMPT.format(
                    name=sub["name"], codes=codes or "no E-number", region=cfg["label"], url=r.url, text=r.text))
            except llm.LLMError as e:
                log.warning("%s/%s extraction failed: %s", sub["id"], region, e)
                continue
            if f.status != "not_stated" and grounded(f.quote, r.text):
                return {"status": f.status, "since": f.since, "reason": f.reason,
                        "quote": " ".join(f.quote.split()), "source_url": r.url}
    return {"status": "unknown"}


async def write_fact(settings: Settings, sub: dict, region: str, cell: dict) -> None:
    """Add a plain-language `fact` to a grounded banned/restricted status."""
    if cell.get("status") not in ("banned", "restricted") or not cell.get("quote"):
        return
    try:
        f = await llm.complete_json(settings, Fact, FACT_PROMPT.format(
            name=sub["name"], codes=" ".join(sub["codes"]) or "no E-number", region=REGIONS[region]["label"],
            status=cell["status"], since=f" since {cell['since']}" if cell.get("since") else "",
            reason=cell.get("reason") or "not stated", url=cell["source_url"], quote=cell["quote"]))
        cell["fact"] = " ".join(f.fact.split())
    except llm.LLMError as e:
        log.warning("%s/%s fact failed: %s", sub["id"], region, e)


async def research_substance(exa: Exa, settings: Settings, sub: dict) -> dict:
    """All regions for one substance, with facts. Returns {region: status cell}."""
    cells = await asyncio.gather(*(research_region(exa, settings, sub, r) for r in REGIONS))
    regions = dict(zip(REGIONS, cells))
    await asyncio.gather(*(write_fact(settings, sub, r, c) for r, c in regions.items()))
    return regions


# --- Additive names (Open Food Facts taxonomy, ODbL) -------------------------------------

MAX_INS = 1521


@lru_cache
def additive_names() -> dict[str, dict]:
    data = json.loads((Path(__file__).parent / "rules" / "additive_names.json").read_text())
    data.pop("_source", None)
    return data


def name_for(code: str) -> str | None:
    entry = additive_names().get(code) or additive_names().get(re.sub(r"[a-z]+$", "", code))
    return entry["name"] if entry else None


def is_researchable(code: str) -> bool:
    m = re.fullmatch(r"E(\d{3,4})[a-z]?", code)
    return bool(m) and int(m.group(1)) <= MAX_INS and name_for(code) is not None


# --- Runtime queue -----------------------------------------------------------------------


class ResearchQueue:
    """Researches unknown additive codes in the background as scans find them. Each code is
    researched once; results are saved through `save` and become known to every later scan."""

    def __init__(self, settings: Settings, exa: Exa | None, is_known, save,
                 concurrency: int = 2, daily_cap: int = 100) -> None:
        self.settings = settings
        self.exa = exa
        self.is_known = is_known  # code -> bool
        self.save = save  # async (substance dict) -> None
        self.daily_cap = daily_cap
        self.pending: set[str] = set()
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task] = set()
        self._day = dt.date.today()
        self._started_today = 0

    def enqueue(self, codes: list[str]) -> list[str]:
        """Start research for codes we don't know yet. Returns the codes now being researched."""
        if self.exa is None:
            return []
        if dt.date.today() != self._day:
            self._day, self._started_today = dt.date.today(), 0
        for code in codes:
            if code in self.pending or self.is_known(code) or not is_researchable(code):
                continue
            if self._started_today >= self.daily_cap:
                log.warning("Research daily cap reached; not researching %s", code)
                break
            self._started_today += 1
            self.pending.add(code)
            task = asyncio.create_task(self._run(code))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return [c for c in dict.fromkeys(codes) if c in self.pending]

    async def _run(self, code: str) -> None:
        name = name_for(code)
        entry = additive_names().get(code, {})
        sub = {
            "id": f"auto-{code.lower()}", "name": name, "codes": [code], "synonyms": [name.lower()],
            "category": (entry.get("functions") or ["additive"])[0], "origin": "auto",
        }
        try:
            async with self._slots:
                sub["regions"] = await research_substance(self.exa, self.settings, sub)
            sub["researched_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            await self.save(sub)
            grounded_count = sum(1 for c in sub["regions"].values() if c["status"] != "unknown")
            log.info("Researched %s (%s): %d/3 regions grounded", code, name, grounded_count)
        except Exception:
            log.exception("Research for %s failed", code)
        finally:
            self.pending.discard(code)
