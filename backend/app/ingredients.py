"""Split a free-text ingredients list into a structured list for display.

"Cereal products (64%) (rice meal (43.9%), corn meal (20%)), edible vegetable oil
(palmolein), acidity regulators (330, 296)" becomes one item per top-level ingredient,
with its percentage, sub-ingredients, and additive codes. Items whose code or name matches
a substance banned somewhere are flagged, so the app can highlight them.
"""

import re

from app import additive_codes
from app.models import AdditiveFinding, Ingredient

_LABEL = re.compile(r"^\s*ingredients?\s*[:\-]\s*", re.IGNORECASE)
_PERCENT = re.compile(r"\(?\s*(\d+(?:[.,]\d+)?)\s*%\s*\)?")
_BULLET = re.compile(r"^\s*[-•*·]\s*")


def split_top(text: str) -> list[str]:
    """Split on commas, semicolons, line breaks and sentence-ending full stops ('. Masala
    Tastemaker:') that aren't inside brackets. Decimals ('26.5%') have no space after the dot."""
    parts, depth, current = [], 0, []
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        sentence_end = ch == "." and text[i + 1:i + 2] in (" ", "\n")
        if depth == 0 and (ch in ",;\n" or sentence_end):
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p for p in (_BULLET.sub("", p).strip(" .\t") for p in parts) if p]


def _outer_brackets(text: str) -> tuple[str, list[str]]:
    """'Seasoning (spices, salt)' -> ('Seasoning', ['spices, salt']); nested brackets kept whole."""
    head, groups, depth, current = [], [], 0, []
    for ch in text:
        if ch == "(":
            if depth > 0:
                current.append(ch)
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
            if depth == 0:
                groups.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        elif depth > 0:
            current.append(ch)
        else:
            head.append(ch)
    return "".join(head).strip(), groups


def _only_codes(group: str) -> bool:
    """True for '330, 296' or '508 & 412' or 'INS 951': nothing but additive codes."""
    parts = [p for p in additive_codes._SEPARATORS.split(group.strip()) if p.strip()]
    return bool(parts) and all(additive_codes.normalise(p) for p in parts)


_SECTION = re.compile(r"^\s*([A-Za-z][A-Za-z '&-]{1,30}?)\s*:\s*(?=\S)")


def _parse_item(text: str, depth: int = 0) -> Ingredient:
    name, groups = _outer_brackets(text)
    percent, codes, subs = None, [], []
    for group in groups:
        if m := re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*%\s*", group):
            percent = m.group(1).replace(",", ".")
        elif _only_codes(group):
            codes += additive_codes.find_in_text(f"({group})")  # "(330, 296)": codes, not sub-ingredients
        elif depth == 0:
            subs += [_parse_item(part, depth + 1) for part in split_top(group)]
        else:
            name = f"{name} ({group})"  # deeper nesting: keep as text
    if percent is None and (m := _PERCENT.search(name)):
        percent = m.group(1).replace(",", ".")
        name = _PERCENT.sub("", name, count=1).strip()
    codes += [c for c in additive_codes.find_in_text(name) if c not in codes]
    return Ingredient(name=" ".join(name.split()).strip(" .:"), percent=percent, codes=codes, sub=subs)


def parse(text: str | None) -> list[Ingredient]:
    """Top-level ingredients. A label like 'Noodles:' starts a section that later items inherit."""
    if not text:
        return []
    text = additive_codes._SUB_NUMBER.sub(r"\1", _LABEL.sub("", text.strip()))  # 501(i) -> 501
    items, section = [], None
    for part in split_top(text):
        if m := _SECTION.match(part):
            section = m.group(1).strip()
            part = part[m.end():]
        item = _parse_item(part)
        item.section = section
        items.append(item)
    return items


def flag(items: list[Ingredient], findings: list[AdditiveFinding], synonyms: dict[str, list[str]]) -> None:
    """Mark items containing a substance that is banned somewhere (by code or by name)."""
    banned = [f for f in findings if f.flagged]
    for item in items:
        flag(item.sub, findings, synonyms)
        text = item.name.lower()
        for f in banned:
            names = synonyms.get(f.id, [])
            if (f.code and f.code in item.codes) or any(re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", text)
                                                       for n in names):
                item.flagged = True
                item.flag_reason = f.message
                break
