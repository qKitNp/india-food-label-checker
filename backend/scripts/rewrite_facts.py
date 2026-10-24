"""Rewrite the plain-language facts for restricted statuses in the Appwrite `substances` table
(and the seed file), e.g. after changing FACT_PROMPT in app/research.py. Restart the API
afterwards so it reloads the substances.

    uv run python -m scripts.rewrite_facts [--status restricted|banned|all]
"""

import argparse
import asyncio
import json
from pathlib import Path

from app import llm
from app.config import get_settings
from app.research import write_fact
from app.store import make_store

SEED_OUT = Path(__file__).resolve().parent.parent / "app" / "rules" / "substances.json"


async def rewrite(subs: list[dict], statuses: set[str], settings) -> int:
    cells = [(s, r, c) for s in subs for r, c in s.get("regions", {}).items() if c.get("status") in statuses]
    for _, _, c in cells:
        c.pop("fact", None)
    sem = asyncio.Semaphore(8)

    async def one(s, r, c):
        async with sem:
            await write_fact(settings, s, r, c)
    await asyncio.gather(*(one(*cell) for cell in cells))
    return len(cells)


async def main(statuses: set[str]) -> None:
    settings = get_settings()
    llm.export_keys(settings)
    store = make_store(settings)

    subs = await store.list_substances()
    n = await rewrite(subs, statuses, settings)
    for s in subs:
        await store.save_substance(s)
    print(f"Appwrite: rewrote {n} facts across {len(subs)} substances")

    seed = json.loads(SEED_OUT.read_text())
    n = await rewrite(seed, statuses, settings)
    SEED_OUT.write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n")
    print(f"Seed file: rewrote {n} facts")

    for s in subs:
        for r, c in s.get("regions", {}).items():
            if c.get("status") in statuses and c.get("fact"):
                print(f"  [{r}] {s['name']}: {c['fact']}")
    if hasattr(store, "aclose"):
        await store.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", default="restricted", choices=["restricted", "banned", "all"])
    args = parser.parse_args()
    asyncio.run(main({"restricted", "banned"} if args.status == "all" else {args.status}))
