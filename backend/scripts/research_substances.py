"""Research the regulatory status of each substance in app/rules/substances_seed.json.

For every substance and region (IN, EU, US): search official sources with Exa, have the
LLM state the status with a verbatim supporting quote, and keep it only if the quote
appears on the page (the same grounding check the agent uses). Anything not grounded
stays "unknown". Writes app/rules/substances.json.

    uv run python -m scripts.research_substances [--only id1,id2]
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

from exa_py import Exa
from pydantic import BaseModel

from app import llm
from app.config import get_settings
from app.sources.counterpart import grounded

RULES = Path(__file__).resolve().parent.parent / "app" / "rules"
SEED = RULES / "substances_seed.json"
OUT = RULES / "substances.json"

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

PROMPT = """You check food regulations. Based ONLY on the page below, what is the status of
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

Substance: {name} ({codes})
Status in {region}: {status}{since}
Reason given by the source: {reason}
Quote from the source ({url}): "{quote}"
"""


class Fact(BaseModel):
    fact: str


class Finding(BaseModel):
    status: Literal["banned", "restricted", "allowed", "not_stated"]
    since: str | None = None
    reason: str | None = None
    quote: str = ""


async def research(exa: Exa, settings, sem: asyncio.Semaphore, sub: dict, region: str) -> dict:
    cfg = REGIONS[region]
    codes = " ".join(sub["codes"])
    async with sem:
        for query in cfg["queries"]:  # a second phrasing when the first finds nothing grounded
            try:
                res = await asyncio.to_thread(
                    exa.search, query.format(name=sub["name"], codes=codes).strip(), num_results=5,
                    include_domains=cfg["domains"], contents={"text": {"max_characters": 20000}},
                )
            except Exception as e:
                logging.warning("%s/%s search failed: %s", sub["id"], region, e)
                continue
            # exclude_sources: pages we found to be out of date
            for r in [r for r in res.results if r.text and r.url not in sub.get("exclude_sources", [])][:3]:
                try:
                    f = await llm.complete_json(settings, Finding, PROMPT.format(
                        name=sub["name"], codes=codes or "no E-number", region=cfg["label"], url=r.url, text=r.text))
                except llm.LLMError as e:
                    logging.warning("%s/%s extraction failed: %s", sub["id"], region, e)
                    continue
                if f.status != "not_stated" and grounded(f.quote, r.text):
                    return {"status": f.status, "since": f.since, "reason": f.reason,
                            "quote": " ".join(f.quote.split()), "source_url": r.url}
    return {"status": "unknown"}


async def write_facts(settings, only: set[str] | None) -> None:
    """Add a plain-language `fact` to each grounded status, for the app's loading screen."""
    data = json.loads(OUT.read_text())
    sem = asyncio.Semaphore(8)

    async def one(sub: dict, region: str, cell: dict) -> None:
        async with sem:
            f = await llm.complete_json(settings, Fact, FACT_PROMPT.format(
                name=sub["name"], codes=" ".join(sub["codes"]) or "no E-number", region=REGIONS[region]["label"],
                status=cell["status"], since=f" since {cell['since']}" if cell.get("since") else "",
                reason=cell.get("reason") or "not stated", url=cell["source_url"], quote=cell["quote"]))
            cell["fact"] = " ".join(f.fact.split())

    await asyncio.gather(*(one(sub, region, cell) for sub in data if not only or sub["id"] in only
                           for region, cell in sub.get("regions", {}).items()
                           if cell.get("status") in ("banned", "restricted") and cell.get("quote")))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    facts = [(s["name"], r, c["fact"]) for s in data for r, c in s.get("regions", {}).items() if c.get("fact")]
    print(f"{len(facts)} facts")
    for name, region, fact in facts:
        print(f"  [{region}] {fact}")


async def main(only: set[str] | None) -> None:
    settings = get_settings()
    llm.export_keys(settings)
    exa = Exa(settings.exa_api_key)
    seed = json.loads(SEED.read_text())
    existing = {s["id"]: s for s in json.loads(OUT.read_text())} if OUT.exists() else {}
    sem = asyncio.Semaphore(8)

    targets = [s for s in seed if not only or s["id"] in only]
    tasks = {(s["id"], region): asyncio.create_task(research(exa, settings, sem, s, region))
             for s in targets for region in REGIONS}
    await asyncio.gather(*tasks.values())

    out = []
    for s in seed:
        entry = {**s, "regions": existing.get(s["id"], {}).get("regions", {})}
        for region in REGIONS:
            if (s["id"], region) in tasks:
                entry["regions"][region] = tasks[(s["id"], region)].result()
        out.append(entry)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")

    grounded_count = sum(1 for s in out for r in s["regions"].values() if r.get("status") != "unknown")
    print(f"{len(out)} substances, {grounded_count}/{len(out) * len(REGIONS)} region statuses grounded in a source")
    for s in out:
        cells = " | ".join(f"{k}:{v.get('status', '-'):10}" for k, v in s["regions"].items())
        print(f"  {s['id']:26} {cells}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated substance ids")
    parser.add_argument("--facts", action="store_true", help="only (re)write facts for researched statuses")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    if args.facts:
        settings = get_settings()
        llm.export_keys(settings)
        asyncio.run(write_facts(settings, only))
    else:
        asyncio.run(main(only))
