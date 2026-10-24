"""Normalise additive codes. Indian labels use INS numbers ("INS 338", "(338)"),
EU labels use E-numbers; both share the same numbering."""

import re

_CODE = re.compile(r"^(?:E|INS)?\s*-?\s*(\d{3,4})\s*([a-z]{0,2})\s*$", re.IGNORECASE)
# Either a prefixed code anywhere ("INS 338", "E951"), or a bracketed group of codes,
# which Indian labels use for several at once: "Flavour Enhancers (627, 631)".
_IN_TEXT = re.compile(r"(?:\bE|\bINS)\s*-?\s*(\d{3,4}[a-z]{0,2})\b|\(([^()]*)\)", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s*(?:,|&|/|\band\b)\s*", re.IGNORECASE)
# INS sub-numbers: "501(i)", "500 (ii)", "451(i)". The sub-number refers to a variant of the same
# additive, and its nested brackets would break group matching, so drop it before parsing.
_SUB_NUMBER = re.compile(r"(\d{3,4}[a-z]?)\s*\((?:i{1,3}|iv|vi{0,3}|ix|x|\d)\)", re.IGNORECASE)


def normalise(code: str) -> str | None:
    """'en:e150d' / 'INS 150d' / '150D' -> 'E150d'."""
    code = code.split(":")[-1].strip()
    m = _CODE.match(code)
    if not m:
        return None
    return f"E{m.group(1)}{m.group(2).lower()}"


def find_in_text(text: str) -> list[str]:
    """Pull additive codes out of a free-text ingredients list, in order, deduplicated."""
    seen: dict[str, None] = {}
    for m in _IN_TEXT.finditer(_SUB_NUMBER.sub(r"\1", text or "")):
        if m.group(1):
            codes = [normalise(m.group(1))]
        else:
            # A bracket counts only if everything in it is a code; "(Palmolein)" or "(8.7 mg)" isn't.
            codes = [normalise(part) for part in _SEPARATORS.split(m.group(2).strip())]
            if not all(codes):
                codes = []
        for code in codes:
            if code:
                seen[code] = None
    return list(seen)
