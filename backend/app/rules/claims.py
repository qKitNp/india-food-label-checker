"""Marketing claims vs the legal cut-offs behind them.

Thresholds are from EU Regulation (EC) 1924/2006, Annex, which we use as the benchmark.
FSSAI's Advertising and Claims Regulations 2018 are believed to use similar numbers but
have NOT been checked yet, hence no Indian thresholds here.
"""

import re
from dataclasses import dataclass

from app.models import ClaimFinding, Product

EU_ANNEX = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006R1924-20141213"


@dataclass(frozen=True)
class ClaimRule:
    key: str
    label: str
    nutrient: str  # attribute of Nutrition
    unit: str
    solid_max: float | None  # per 100 g; None = claim not defined for solids
    liquid_max: float  # per 100 ml
    patterns: tuple[str, ...]


RULES = (
    ClaimRule("energy_free", "zero calorie", "energy_kcal", "kcal", None, 4,
              (r"zero\s*cal", r"\b0\s*cal", r"no\s*calories", r"calorie[\s-]*free", r"energy[\s-]*free",
               r"\bzero\b", r"\bdiet\b")),
    ClaimRule("low_energy", "low calorie", "energy_kcal", "kcal", 40, 20,
              (r"low[\s-]*cal", r"\blite\b", r"\blight\b")),
    ClaimRule("sugar_free", "sugar free", "sugars_g", "g sugar", 0.5, 0.5,
              (r"sugar[\s-]*free", r"zero\s*sugar", r"no\s*sugar", r"\b0\s*g?\s*sugar")),
    ClaimRule("low_sugar", "low sugar", "sugars_g", "g sugar", 5, 2.5, (r"low[\s-]*sugar",)),
    ClaimRule("fat_free", "fat free", "fat_g", "g fat", 0.5, 0.5,
              (r"fat[\s-]*free", r"zero\s*fat", r"no\s*fat", r"\b0\s*%?\s*fat")),
    ClaimRule("low_fat", "low fat", "fat_g", "g fat", 3, 1.5, (r"low[\s-]*fat",)),
)


def detect(product: Product) -> list[ClaimRule]:
    text = " | ".join([*product.claims, product.name or ""]).lower()
    return [r for r in RULES if any(re.search(p, text) for p in r.patterns)]


def evaluate(product: Product) -> list[ClaimFinding]:
    findings = []
    per = "100 ml" if product.is_liquid else "100 g"
    for rule in detect(product):
        limit = rule.liquid_max if product.is_liquid else rule.solid_max
        if limit is None:
            continue
        actual = getattr(product.nutrition, rule.nutrient)
        if actual is None:
            passes, msg = None, f"Claims '{rule.label}', but the label data has no {rule.unit} value to check."
        elif actual > limit:
            passes, msg = False, (
                f"Claims '{rule.label}' but has {actual:g} {rule.unit} per {per}, "
                f"above the EU limit of {limit:g}."
            )
        elif actual > 0:
            passes, msg = True, (
                f"'{rule.label.capitalize()}' only means ≤{limit:g} {rule.unit} per {per}. "
                f"This has {actual:g}: legal, but not literally zero."
                if rule.key.endswith("_free")
                else f"Meets the EU '{rule.label}' limit (≤{limit:g} {rule.unit} per {per}); has {actual:g}."
            )
        else:
            passes, msg = True, f"Meets the '{rule.label}' claim ({actual:g} {rule.unit} per {per})."
        findings.append(ClaimFinding(
            claim=rule.label, rule=rule.key, actual=actual, threshold=limit, unit=rule.unit,
            passes=passes, message=msg, source_url=EU_ANNEX, source_verified=True,
        ))
    return findings
