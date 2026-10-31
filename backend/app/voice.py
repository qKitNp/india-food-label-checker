"""What the voice agent says about a product: written for the ear, built from checked facts.

The screen shows the full report; a caller needs the gist in a few seconds, in words a
person would use. The agent reads `answer` word for word, and answers follow-up questions
only from `more`, so every number and status still comes from our rules, never the LLM.
"""

import random
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import urlparse

from app import research
from app.models import Verdict
from app.verdict import suspect

SWEETENERS = {"E950": "acesulfame K", "E951": "aspartame", "E952": "cyclamate", "E954": "saccharin",
              "E955": "sucralose", "E960": "stevia", "E961": "neotame", "E962": "aspartame-acesulfame"}
REGION_NAMES = {"IN": "India", "EU": "the EU", "US": "the US"}
SHORT_COUNTRY = {"United Kingdom": "UK", "United States": "US", "United States of America": "US"}
SMALL_WORDS = {"and", "or", "of", "with", "in", "the", "a", "an", "to", "for"}
WHO_SODIUM_MG = 2000  # WHO daily sodium limit (5 g salt)
SALTY_MG = 400  # per 100 g: worth a mention
GLASS_ML = 250  # a drink's sugar is easier to picture per glass than per 100 ml
TSP_SUGAR_G = 4

INTROS = ["That's {name}.", "This is {name}.", "Okay, that's {name}."]
GOOD = ["Good news:", "Looks good:", "The good news:"]
HEADS_UP = ["Heads up:", "One thing to know:"]


@dataclass
class Spoken:
    answer: str
    more: list[str] = field(default_factory=list)


def name(v: Verdict) -> str:
    """How a person says it: "Diet Coke", not "Coca-Cola Diet coke"."""
    brand = re.sub(r"\s*\(.*?\)", "", v.product.brand or "").strip()
    product = re.sub(r"\s*\([^)]*\)", "", v.product.name or "").strip()
    product = re.sub(r",\s*\d+\s*(?:g|kg|ml|l)\b.*$", "", product, flags=re.IGNORECASE)  # ", 70 g pouch"
    if not product or _carries_brand(product, brand):
        said = product or brand
    else:
        said = f"{brand} {product}"
    words = [w if not w.islower() or (i and w in SMALL_WORDS) else w[:1].upper() + w[1:]
             for i, w in enumerate(said.split())]
    return " ".join(words) or "this product"


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _carries_brand(product: str, brand: str) -> bool:
    """"Kurkure Masala Munch" starts with its brand; "Diet Coke" carries Coca-Cola's
    everyday short form. Either way, saying the brand again sounds robotic."""
    if not brand or _key(product).startswith(_key(brand)):
        return True
    first = _key(brand.split()[0].split("-")[0])
    return any(len(w) >= 3 and SequenceMatcher(None, _key(w), first).ratio() >= 0.5
               for w in product.split() if _key(w) and _key(w)[:2] == first[:2])


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _additive(code: str, v: Verdict) -> str:
    for a in v.additives:
        if a.code == code:
            return _plain(a.name)
    return _plain(research.name_for(code) or code)


def _plain(n: str) -> str:
    # "Acesulfame k" -> "acesulfame K"; chemical names read lower-case mid-sentence.
    n = n[:1].lower() + n[1:]
    return re.sub(r"\bk$", "K", n)


def _country(v: Verdict) -> str:
    c = v.counterpart.country if v.counterpart else ""
    return SHORT_COUNTRY.get(c, c)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]  # str.capitalize() would lower-case "FCF"


def _per(v: Verdict) -> str:
    return "100 ml" if v.product.is_liquid else "100 grams"


def _claim_line(c, v: Verdict) -> str:
    unit = {"kcal": "calories", "g": "grams", "mg": "milligrams"}.get(c.unit, c.unit)
    claim = c.claim.replace(" ", "-")  # "the zero-calorie claim"
    if c.passes:
        return f"the {claim} claim checks out"
    return (f"the {claim} claim doesn't hold up: it has {c.actual:g} {unit} per {_per(v)}, "
            f"more than the {c.threshold:g} allowed")


def _sugar_line(v: Verdict) -> str | None:
    sugar = v.product.nutrition.sugars_g
    if not sugar or sugar < 5:
        return None
    if v.product.is_liquid:
        tsp = round(sugar * GLASS_ML / 100 / TSP_SUGAR_G)
        return f"It has about {tsp} teaspoons of sugar in a glass"
    tsp = round(sugar / TSP_SUGAR_G)
    return f"It has about {tsp} teaspoons of sugar in every 100 grams"


def _salt_line(v: Verdict) -> str | None:
    sodium = v.product.nutrition.sodium_mg
    if v.product.is_liquid or not sodium or sodium < SALTY_MG:
        return None
    pct = round(sodium / WHO_SODIUM_MG * 100 / 5) * 5
    return f"A 100-gram serving has about {pct}% of the salt the WHO says to have in a whole day"


def _banned_line(a) -> str:
    where = [REGION_NAMES[r] for r, s in a.status.items() if s.status == "banned"]
    return f"it has {_plain(a.name)}, which is banned in {_list(where)}"


def _compare_line(v: Verdict) -> str | None:
    cp = v.counterpart
    if not cp or not cp.additives or not (cp.only_in_india or cp.only_in_counterpart):
        return None  # an empty list for their pack usually means we couldn't read it
    parts, country = [], _country(v)
    if cp.only_in_india:
        parts.append(f"the Indian one has {_list([_additive(c, v) for c in cp.only_in_india])}, "
                     f"which the {country} label doesn't list")
    if cp.only_in_counterpart:
        parts.append(f"the {country} one has {_list([_additive(c, v) for c in cp.only_in_counterpart])} instead")
    return f"Compared with the {country} version, " + "; ".join(parts) + "."


def describe(v: Verdict, rng: random.Random | None = None) -> Spoken:
    rng = rng or random.Random()
    said = name(v)
    if suspect(v):
        return Spoken(f"I found {said}, but the data online for it looks off, so I won't judge it. "
                      "A photo of its back label in the app would sort that out.")

    banned = [a for a in v.additives if a.flagged]
    failed = [c for c in v.claims if c.passes is False]
    passed = [c for c in v.claims if c.passes]
    sweeteners = [SWEETENERS[c] for c in v.product.additives if c in SWEETENERS]
    compare = _compare_line(v)

    lines = [rng.choice(INTROS).format(name=said)]
    if banned or failed:
        bad = [_banned_line(a) for a in banned[:2]] + [_claim_line(c, v) for c in failed[:1]]
        lines.append(f"{rng.choice(HEADS_UP)} {bad[0]}.")
        lines += [f"Also, {b}." for b in bad[1:2]]
    else:
        good = []
        if passed:
            good.append(_claim_line(passed[0], v))
        if v.additives:
            good.append("nothing in it is banned in India, the EU or the US")
        if good:
            lines.append(f"{rng.choice(GOOD)} {' and '.join(good)}.")
        else:
            lines.append("Nothing stands out in what we check.")
    if sweeteners:
        lines.append(f"It's sweetened with {_list(sweeteners)}.")
    elif extra := _sugar_line(v) or _salt_line(v):
        lines.append(extra + ".")

    if compare:
        lines.append(f"Want to hear how it differs from the {_country(v)} version?")
    elif v.additives:
        lines.append("Want to know what else is in it?")
    else:
        lines.append("Want to check another pack?")
    return Spoken(" ".join(lines), more=_more(v, compare))


def _what_is(a) -> str:
    """"What is aspartame?": its kind, plus the checked rule for India and one other region."""
    kind = a.category.replace("_", " ").strip() or "additive"
    article = "an" if kind[:1].lower() in "aeiou" else "a"
    line = f"{_cap(_plain(a.name))} is {article} {kind}."
    facts = [a.status[r].fact for r in ("IN", "EU", "US") if r in a.status and a.status[r].fact]
    return " ".join([line, *facts[:2]])


def _more(v: Verdict, compare: str | None) -> list[str]:
    """Checked facts for follow-up questions, each a sentence the agent can say as is."""
    more = []
    if compare:
        more.append(compare)
    if v.product.additives:
        more.append(f"Its additives are {_list([_additive(c, v) for c in v.product.additives])}.")
    # Banned ones and sweeteners first: those are what people ask about.
    for a in sorted(v.additives, key=lambda a: (not a.flagged, a.code not in SWEETENERS))[:6]:
        more.append(_what_is(a))
    limited = [_plain(a.name) for a in v.additives if any(s.status == "restricted" for s in a.status.values())]
    if limited:
        more.append(f"{_cap(_list(limited))} {'has' if len(limited) == 1 else 'have'} legal usage "
                    "limits, but labels don't say how much is used, so we can't check them.")
    n = v.product.nutrition
    facts = [f"{n.energy_kcal:g} calories" if n.energy_kcal is not None else "",
             f"{n.sugars_g:g} grams of sugar" if n.sugars_g is not None else "",
             f"{n.sodium_mg:g} milligrams of sodium" if n.sodium_mg is not None else ""]
    if facts := [f for f in facts if f]:
        more.append(f"Per {_per(v)} it has {_list(facts)}.")
    for extra in (_sugar_line(v), _salt_line(v)):
        if extra:
            more.append(extra + ".")
    for limit in v.limits:
        more.append(limit.message)
    if v.product.source == "web" and v.product.evidence:
        site = urlparse(v.product.evidence.source_url).netloc.removeprefix("www.")
        more.append(f"The ingredients are from {site}, not read from this pack.")
    more.append("The full report, with sources, is on the screen.")
    return more
