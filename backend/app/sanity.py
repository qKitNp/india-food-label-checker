"""Plausibility checks for product data. Crowd-sourced entries (Open Food Facts) are
often wrong, e.g. an Indian Coke Zero entry listing 'sugar' as an ingredient. A product
with issues must not produce red flags until a label photo confirms it."""

import re

from app.models import Product

_SUGAR_WORD = re.compile(r"\bsugar\b(?![\s-]*free)", re.IGNORECASE)


def missing(product: Product) -> list[str]:
    """Gaps that limit what we can check, but don't make the rest of the data suspect."""
    gaps = []
    if not product.ingredients_text:
        gaps.append("Ingredients list is missing.")
    if product.nutrition.energy_kcal is None:
        gaps.append("Energy value is missing.")
    return gaps


def check(product: Product) -> list[str]:
    """Contradictions that make the data suspect."""
    issues: list[str] = []
    n = product.nutrition

    if n.sugars_g is not None and n.carbs_g is not None and n.sugars_g > n.carbs_g + 0.5:
        issues.append(f"Sugars ({n.sugars_g} g) exceed total carbohydrates ({n.carbs_g} g).")

    if product.ingredients_text and _SUGAR_WORD.search(product.ingredients_text) and n.sugars_g == 0:
        issues.append("Ingredients mention sugar but the nutrition table says 0 g sugar.")

    if None not in (n.energy_kcal, n.carbs_g, n.protein_g, n.fat_g):
        computed = 4 * n.carbs_g + 4 * n.protein_g + 9 * n.fat_g
        # Generous tolerance: fibre, polyols, alcohol and label rounding all skew this.
        if computed > 20 and abs(computed - n.energy_kcal) > max(30, 0.25 * computed):
            issues.append(
                f"Stated energy ({n.energy_kcal:g} kcal) doesn't match macros (~{computed:.0f} kcal)."
            )

    if product.is_liquid is False and n.energy_kcal is not None and n.energy_kcal < 10 and not product.claims:
        issues.append(f"Implausibly low energy for a solid food ({n.energy_kcal:g} kcal/100 g).")

    return issues
