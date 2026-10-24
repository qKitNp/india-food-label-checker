import asyncio

import httpx
import pytest
import respx

from app import research, verdict
from app.config import Settings
from app.models import Nutrition, Product
from app.rules import substances

REGIONS = {
    "EU": {"status": "allowed", "quote": "Guar gum is authorised", "source_url": "https://eur-lex.europa.eu/x"},
    "US": {"status": "unknown"},
    "IN": {"status": "unknown"},
}


@pytest.fixture
def fake_research(monkeypatch):
    calls = []

    async def fake(exa, settings, sub):
        calls.append(sub["codes"][0])
        await asyncio.sleep(0)
        return dict(REGIONS)
    monkeypatch.setattr(research, "research_substance", fake)
    return calls


def test_names_come_from_open_food_facts():
    assert research.name_for("E412") == "Guar gum"
    assert research.name_for("E330") == "Citric acid"


def test_only_valid_named_codes_are_researchable():
    assert research.is_researchable("E412")
    assert not research.is_researchable("E4511")  # beyond the INS range: a misread
    unnamed = next(f"E{n}" for n in range(100, 1522) if research.name_for(f"E{n}") is None)
    assert not research.is_researchable(unnamed)  # no such additive in the taxonomy


def test_queue_researches_once_and_learns(fake_research):
    known: dict[str, dict] = {}

    async def save(sub):
        known[sub["id"]] = sub

    async def go():
        q = research.ResearchQueue(Settings(), exa=object(), save=save,
                                   is_known=lambda code: any(code in s["codes"] for s in known.values()))
        assert q.enqueue(["E412", "E412", "E4511"]) == ["E412"]  # duplicate and misread skipped
        while q.pending:
            await asyncio.sleep(0.01)
        assert q.enqueue(["E412"]) == []  # known now: not researched again
    asyncio.run(go())

    [sub] = known.values()
    assert fake_research == ["E412"]
    assert sub["id"] == "auto-e412" and sub["name"] == "Guar gum" and sub["origin"] == "auto"
    assert sub["regions"]["EU"]["status"] == "allowed" and sub["researched_at"]


def test_daily_cap(fake_research):
    async def go():
        q = research.ResearchQueue(Settings(), exa=object(), is_known=lambda c: False,
                                   save=lambda s: asyncio.sleep(0), daily_cap=1)
        assert q.enqueue(["E412", "E330"]) == ["E412"]
        while q.pending:
            await asyncio.sleep(0.01)
    asyncio.run(go())


def test_no_research_without_web_search():
    q = research.ResearchQueue(Settings(), exa=None, is_known=lambda c: False, save=None)
    assert q.enqueue(["E412"]) == []


def test_verdict_names_other_additives(monkeypatch):
    monkeypatch.setattr(substances, "_items", {})  # nothing researched yet
    p = Product(source="label", verified=True, ingredients_text="Water, guar gum (412), citric acid (330)",
                additives=["E412", "E330"], nutrition=Nutrition(energy_kcal=10))
    v = verdict.build(p)
    others = {o.code: o.name for o in v.other_additives}
    assert others == {"E412": "Guar gum", "E330": "Citric acid"}


def test_learned_substance_is_used_by_next_verdict(monkeypatch):
    learned = {"id": "auto-e412", "name": "Guar gum", "codes": ["E412"], "synonyms": ["guar gum"],
               "category": "thickener", "origin": "auto", "researched_at": "2026-10-24T00:00:00+00:00",
               "regions": REGIONS}
    monkeypatch.setattr(substances, "_items", {})
    p = Product(source="label", verified=True, ingredients_text="Water, thickener (412)", additives=["E412"],
                nutrition=Nutrition(energy_kcal=10))
    assert [o.code for o in verdict.build(p).other_additives] == ["E412"]
    substances.upsert(learned)
    v = verdict.build(p)
    assert v.other_additives == [] and v.additives[0].origin == "auto"
