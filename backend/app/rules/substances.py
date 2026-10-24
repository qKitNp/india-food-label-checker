"""Substances to watch (additives, illegal dyes, trans fats, contaminants) and their status in
India, the EU and the US, researched from official sources by scripts/research_substances.py.

Every status other than "unknown" carries a verbatim quote that was checked against its
source page. Products are matched by additive code or by name in the ingredients text.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.models import AdditiveFinding, Product, RegionStatus

_FILE = Path(__file__).with_name("substances.json")
REGIONS = ("IN", "EU", "US")
REGION_NAMES = {"IN": "India", "EU": "the EU", "US": "the US"}


@lru_cache
def table() -> list[dict]:
    return json.loads(_FILE.read_text()) if _FILE.exists() else []


def _name_pattern(names: list[str]) -> re.Pattern | None:
    if not names:
        return None
    return re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(n.lower()) for n in names) + r")(?![a-z])")


def _status(sub: dict, region: str) -> RegionStatus:
    return RegionStatus(**(sub.get("regions", {}).get(region) or {"status": "unknown"}))


def _message(name: str, status: dict[str, RegionStatus]) -> str:
    banned = [r for r in REGIONS if status[r].status == "banned"]
    if not banned:
        return f"{name}: not banned in India, the EU or the US by the sources we checked."
    parts = []
    for r in banned:
        since = f" (from {status[r].since})" if status[r].since else ""
        parts.append(f"{REGION_NAMES[r]}{since}")
    return f"{name}: banned in {', '.join(parts)}."


def evaluate(product: Product) -> list[AdditiveFinding]:
    text = (product.ingredients_text or "").lower()
    findings = []
    for sub in table():
        matched = next((c for c in sub["codes"] if c in product.additives), None)
        if matched is None and (pattern := _name_pattern(sub["synonyms"])) and (m := pattern.search(text)):
            matched = m.group(0)
        if matched is None:
            continue
        status = {r: _status(sub, r) for r in REGIONS}
        findings.append(AdditiveFinding(
            id=sub["id"], code=sub["codes"][0] if sub["codes"] else "", name=sub["name"],
            category=sub["category"], matched=matched, status=status,
            flagged=any(s.status == "banned" for s in status.values()),
            message=_message(sub["name"], status),
        ))
    return findings


def reviewed_codes() -> set[str]:
    return {c for sub in table() for c in sub["codes"]}

