from urllib.parse import urlparse

from app import additive_codes, research, sanity
from app.models import Counterpart, OtherAdditive, Product, Verdict
from app.rules import claims, substances

UNRELIABLE = "This product's online data looks unreliable. Scan the back label to check it."


def suspect(v: Verdict) -> bool:
    return v.headline == [UNRELIABLE]


def build(product: Product, counterpart: Counterpart | None = None) -> Verdict:
    # Re-derive codes from the ingredients so records saved by an older parser stay complete.
    found = additive_codes.find_in_text(product.ingredients_text or "")
    if missing := [c for c in found if c not in product.additives]:
        product = product.model_copy(update={"additives": [*product.additives, *missing]})
    contradictions = sanity.check(product)
    claim_findings = claims.evaluate(product)
    additive_findings = substances.evaluate(product)

    headline: list[str] = []
    if contradictions and not product.verified:
        # Suspect data: don't accuse the product of anything, ask for the label instead.
        headline.append(UNRELIABLE)
    else:
        headline += [c.message for c in claim_findings if c.passes is False or (c.passes and c.actual)]
        headline += [a.message for a in additive_findings if a.flagged]
        if counterpart and (counterpart.only_in_india or counterpart.only_in_counterpart):
            parts = []
            if counterpart.only_in_india:
                parts.append(f"the Indian pack has {', '.join(counterpart.only_in_india)}")
            if counterpart.only_in_counterpart:
                parts.append(f"the {counterpart.country} pack has {', '.join(counterpart.only_in_counterpart)}")
            headline.append(f"Compared with the {counterpart.country} version: {'; '.join(parts)}.")
        if not headline:
            headline.append("Nothing flagged against EU benchmarks.")
        if product.source == "web" and product.evidence:
            site = urlparse(product.evidence.source_url).netloc.removeprefix("www.")
            headline.append(f"Ingredients are from {site}, not read from your pack.")

    matched = {a.matched for a in additive_findings} | {a.code for a in additive_findings}
    others = [OtherAdditive(code=c, name=research.name_for(c)) for c in product.additives if c not in matched]

    return Verdict(
        product=product, data_issues=contradictions + sanity.missing(product),
        claims=claim_findings, additives=additive_findings, counterpart=counterpart, headline=headline,
        other_additives=others,
    )


def spoken(v: Verdict) -> str:
    """Short plain-text summary for the voice agent."""
    brand, product = v.product.brand or "", v.product.name or ""
    name = product if brand and product.lower().startswith(brand.lower()) else f"{brand} {product}".strip()
    name = name or "This product"
    if suspect(v):
        return (f"I found {name}, but I don't have reliable data for it yet. "
                "You can scan its back label in the app to check it.")
    return f"{name}. " + " ".join(v.headline[:3])
