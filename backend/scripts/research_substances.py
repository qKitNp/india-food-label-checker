"""Research the seed substances in app/rules/substances_seed.json (see app/research.py for
how statuses are grounded) and write app/rules/substances.json, which seeds the Appwrite
`substances` table on startup. Codes found in scans are researched at runtime instead.

    uv run python -m scripts.research_substances [--only id1,id2] [--facts]
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from exa_py import Exa

from app import llm
from app.config import get_settings
from app.research import REGIONS, research_substance, write_fact

RULES = Path(__file__).resolve().parent.parent / "app" / "rules"
SEED = RULES / "substances_seed.json"
OUT = RULES / "substances.json"


def report(data: list[dict]) -> None:
    grounded = sum(1 for s in data for r in s.get("regions", {}).values() if r.get("status") != "unknown")
    print(f"{len(data)} substances, {grounded}/{len(data) * len(REGIONS)} region statuses grounded in a source")
    for s in data:
        cells = " | ".join(f"{k}:{v.get('status', '-'):10}" for k, v in s.get("regions", {}).items())
        print(f"  {s['id']:26} {cells}")


async def research(only: set[str] | None) -> None:
    settings = get_settings()
    llm.export_keys(settings)
    exa = Exa(settings.exa_api_key)
    seed = json.loads(SEED.read_text())
    existing = {s["id"]: s for s in json.loads(OUT.read_text())} if OUT.exists() else {}
    sem = asyncio.Semaphore(3)

    async def one(sub: dict) -> dict:
        if only and sub["id"] not in only:
            return {**sub, "origin": "seed", "regions": existing.get(sub["id"], {}).get("regions", {})}
        async with sem:
            return {**sub, "origin": "seed", "regions": await research_substance(exa, settings, sub)}

    data = await asyncio.gather(*(one(s) for s in seed))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    report(data)


async def facts(only: set[str] | None) -> None:
    """Re-write the plain-language facts without re-researching."""
    settings = get_settings()
    llm.export_keys(settings)
    data = json.loads(OUT.read_text())
    await asyncio.gather(*(write_fact(settings, s, r, c) for s in data if not only or s["id"] in only
                           for r, c in s.get("regions", {}).items()))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(sum(1 for s in data for c in s.get("regions", {}).values() if c.get("fact")), "facts")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated substance ids")
    parser.add_argument("--facts", action="store_true", help="only (re)write facts for researched statuses")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    asyncio.run(facts(only) if args.facts else research(only))
