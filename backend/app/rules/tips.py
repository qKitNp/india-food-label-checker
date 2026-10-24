"""Label-reading tips for the app's loading screen. Each was checked against an official
source by scripts/research_tips.py: kept only if a supporting quote appears on the page."""

import json
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).with_name("tips.json")


@lru_cache
def tips() -> list[dict]:
    if not _FILE.exists():
        return []
    return [{k: t[k] for k in ("id", "title", "text", "source_url")} for t in json.loads(_FILE.read_text())]
