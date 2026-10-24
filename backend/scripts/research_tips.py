"""Check each label-reading tip in app/rules/tips_seed.json against an official source.

For every tip: search the listed official domains with Exa, ask the LLM whether the page
supports the tip's underlying claim and for a verbatim supporting quote, and keep the tip
only if that quote appears on the page. Writes app/rules/tips.json.

    uv run python -m scripts.research_tips
"""

import asyncio
import json
import logging
from pathlib import Path

from exa_py import Exa
from pydantic import BaseModel

from app import llm
from app.config import get_settings
from app.sources.counterpart import grounded

RULES = Path(__file__).resolve().parent.parent / "app" / "rules"
SEED = RULES / "tips_seed.json"
OUT = RULES / "tips.json"

PROMPT = """Does the page below support this statement? Use ONLY the page.

Statement: {claim}

- supported: true only if the page clearly states this (wording may differ).
- quote: the exact sentence(s) from the page that support it, copied character for character.
  Empty if not supported.

PAGE ({url}):
{text}"""


class Check(BaseModel):
    supported: bool
    quote: str = ""


async def verify(exa: Exa, settings, sem: asyncio.Semaphore, tip: dict) -> dict | None:
    async with sem:
        for query in tip["queries"]:
            try:
                res = await asyncio.to_thread(exa.search, query, num_results=5, include_domains=tip["domains"],
                                              contents={"text": {"max_characters": 30000}})
            except Exception as e:
                logging.warning("%s search failed: %s", tip["id"], e)
                continue
            for r in [r for r in res.results if r.text][:4]:
                try:
                    c = await llm.complete_json(settings, Check, PROMPT.format(claim=tip["claim"], url=r.url, text=r.text))
                except llm.LLMError as e:
                    logging.warning("%s check failed: %s", tip["id"], e)
                    continue
                if c.supported and grounded(c.quote, r.text):
                    return {"id": tip["id"], "title": tip["title"], "text": tip["text"],
                            "quote": " ".join(c.quote.split()), "source_url": r.url}
    return None


async def main() -> None:
    settings = get_settings()
    llm.export_keys(settings)
    exa = Exa(settings.exa_api_key)
    tips = json.loads(SEED.read_text())
    sem = asyncio.Semaphore(6)
    results = await asyncio.gather(*(verify(exa, settings, sem, t) for t in tips))
    kept = [r for r in results if r]
    OUT.write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(kept)}/{len(tips)} tips verified against a source")
    for tip, r in zip(tips, results):
        print(f"  {'OK  ' if r else 'DROP'} {tip['id']:18} {r['source_url'][:70] if r else ''}")
        if r:
            print(f"       quote: {r['quote'][:150]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
