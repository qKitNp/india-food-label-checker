"""Numeric legal limits we can check from the label's own numbers.

Only limits on things a label quantifies can be checked. Labels in India give additive codes
but not amounts, so additive usage limits can't be checked; the verdict says so instead of
implying everything is fine. Results are informational: nothing here is flagged.
"""

from app.models import LimitCheck, Product
from app.rules import substances

TRANS_FAT_EU_LIMIT = 2.0  # g of trans fat per 100 g of fat (non-animal trans fat)


def _source(substance_id: str, region: str) -> str | None:
    for sub in substances.table():
        if sub["id"] == substance_id:
            cell = sub.get("regions", {}).get(region) or {}
            return cell.get("source_url") if cell.get("status") != "unknown" else None
    return None


def evaluate(product: Product) -> list[LimitCheck]:
    checks = []
    n = product.nutrition
    source = _source("trans-fat", "EU")
    if source and n.trans_fat_g is not None and n.fat_g:
        per_100g_fat = round(n.trans_fat_g / n.fat_g * 100, 2)
        within = per_100g_fat <= TRANS_FAT_EU_LIMIT
        checks.append(LimitCheck(
            name="Trans fat", region="EU", actual=per_100g_fat, limit=TRANS_FAT_EU_LIMIT, unit="g per 100 g of fat",
            result="within" if within else "above",
            message=(f"Trans fat is {per_100g_fat:g} g per 100 g of fat: "
                     f"{'within' if within else 'above'} the EU limit of {TRANS_FAT_EU_LIMIT:g} g. "
                     "The EU limit excludes trans fat that occurs naturally in animal fat."),
            source_url=source,
        ))
    return checks


def additive_note(limited: int) -> str | None:
    """Plain statement that additive usage limits can't be checked from a label."""
    if not limited:
        return None
    what = "it is" if limited == 1 else "each is"
    return (f"{limited} {'additive has' if limited == 1 else 'additives have'} usage limits, but labels don't say "
            f"how much {what} used, so we can't tell whether this product is within them.")
