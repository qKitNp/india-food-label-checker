"""Substances to watch (additives, illegal dyes, trans fats, contaminants) and their status in
India, the EU and the US. The seed list is researched by scripts/research_substances.py;
codes found in scans are researched at runtime (app/research.py) and added here.

Every status other than "unknown" carries a verbatim quote that was checked against its
source page. Products are matched by additive code or by name in the ingredients text.
"""

import json
import re
from pathlib import Path

from app import additive_codes
from app.models import AdditiveFinding, Product, RegionStatus

_FILE = Path(__file__).with_name("substances.json")
REGIONS = ("IN", "EU", "US")
REGION_NAMES = {"IN": "India", "EU": "the EU", "US": "the US"}

# In memory, keyed by id: the seed file at import, then Appwrite rows and new research results.
_items: dict[str, dict] = {s["id"]: s for s in json.loads(_FILE.read_text())} if _FILE.exists() else {}


def table() -> list[dict]:
    return list(_items.values())


def upsert(sub: dict) -> None:
    _items[sub["id"]] = sub


def seed() -> list[dict]:
    return json.loads(_FILE.read_text()) if _FILE.exists() else []


def is_known(code: str) -> bool:
    return any(code in s["codes"] for s in table())


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


def codes_in(text: str | None, codes: list[str] | None = None) -> list[str]:
    """Every additive code in an ingredients list: written codes ("E150d", "INS 338") plus
    substances we know by name ("aspartame" -> E951). Used to compare two packs fairly."""
    found = list(dict.fromkeys([*(codes or []), *additive_codes.find_in_text(text or "")]))
    for f in evaluate(Product(source="label", ingredients_text=text, additives=found)):
        if f.code and f.code not in found:
            found.append(f.code)
    return found


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
            origin=sub.get("origin", "seed"), researched_at=sub.get("researched_at"),
        ))
    return findings
