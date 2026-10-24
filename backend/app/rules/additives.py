import json
from functools import lru_cache
from pathlib import Path

from app.models import AdditiveFinding, AdditiveStatus, Product

_TABLE = Path(__file__).with_name("additives.json")
REGIONS = ("IN", "EU", "US")


@lru_cache
def table() -> dict[str, dict]:
    data = json.loads(_TABLE.read_text())
    data.pop("_readme", None)
    return data


def evaluate(product: Product) -> list[AdditiveFinding]:
    findings = []
    for code in product.additives:
        entry = table().get(code)
        if entry is None:
            continue
        status = {r: AdditiveStatus(**entry[r]) for r in REGIONS}
        banned_in = [r for r in ("EU", "US") if status[r].allowed is False]
        reasons = []
        if banned_in:
            reasons.append(f"not permitted in the {' or '.join(banned_in)}")
        if entry.get("eu_warning"):
            reasons.append("needs a children's hyperactivity warning in the EU")
        message = (
            f"{entry['name']} ({code}): {'; '.join(reasons)}."
            if reasons
            else f"{entry['name']} ({code}): permitted in the EU and US."
        )
        unverified = [r for r in REGIONS if status[r].allowed is not None and not status[r].source_url]
        if reasons and unverified:
            message += f" Status for {', '.join(unverified)} not yet source-checked."
        findings.append(AdditiveFinding(
            code=code, name=entry["name"], status=status, flagged=bool(reasons), message=message,
        ))
    return findings
