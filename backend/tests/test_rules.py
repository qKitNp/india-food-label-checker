import pytest

from app import additive_codes, sanity, verdict
from app.models import Counterpart, Nutrition, Product
from app.rules import claims, substances
from app.sources.counterpart import grounded
from app.sources.off import parse


def product(**kw) -> Product:
    return Product(source="label", verified=True, **kw)


# Real Open Food Facts entries, trimmed.
OFF_COKE_ZERO_IN = {
    "code": "8901764111105", "product_name": "Coke Zero Can", "brands": "Coca-Cola",
    "ingredients_text": "CARBONATED WATER, SUGAR, ACIDITY REGULATOR (338), CAFFEINE (8.7 mg/100 g), "
                        "COLOUR (150d), FLAVOURS (NATURAL FLAVOURING SUBSTANCES).",
    "additives_tags": ["en:e100", "en:e150d", "en:e338"],
    "nutriments": {"energy-kcal_100g": 0, "sugars_100g": 0, "carbohydrates_100g": 0},
    "quantity": "300 ml",
}


class TestAdditiveCodes:
    def test_normalise(self):
        assert additive_codes.normalise("en:e150d") == "E150d"
        assert additive_codes.normalise("INS 338") == "E338"
        assert additive_codes.normalise("150D") == "E150d"
        assert additive_codes.normalise("caffeine") is None

    def test_find_in_text(self):
        text = "Water, acidity regulator (338), colour (150d), sweetener INS 951, E211, (338)"
        assert additive_codes.find_in_text(text) == ["E338", "E150d", "E951", "E211"]

    def test_find_codes_with_ins_sub_numbers(self):
        # Real Maggi Masala label text
        text = ("Thickeners (508 & 412). Acidity regulators (501(i) & 500(i)) and Humectant (451(1)). "
                "Acidity regulator (330), Flavour enhancer (635), Colour (150d)")
        assert additive_codes.find_in_text(text) == ["E508", "E412", "E501", "E500", "E451", "E330", "E635", "E150d"]

    def test_find_grouped_codes(self):
        text = ("Edible Oil (Palmolein), Flavour Enhancers (627, 631), Colours (INS 110 & INS 102), "
                "Caffeine (8.7 mg/100 g), Preservative (211 and 202)")
        assert additive_codes.find_in_text(text) == ["E627", "E631", "E110", "E102", "E211", "E202"]


class TestOff:
    def test_parse(self):
        p = parse(OFF_COKE_ZERO_IN)
        assert p.is_liquid is True
        assert p.brand == "Coca-Cola"
        assert {"E150d", "E338"} <= set(p.additives)
        assert p.verified is False


class TestSanity:
    def test_flags_sugar_in_ingredients_with_zero_sugar(self):
        issues = sanity.check(parse(OFF_COKE_ZERO_IN))
        assert any("mention sugar" in i for i in issues)

    def test_sugar_free_wording_is_not_sugar(self):
        p = product(ingredients_text="Water, sugar-free syrup", nutrition=Nutrition(energy_kcal=1, sugars_g=0))
        assert sanity.check(p) == []

    def test_energy_mismatch(self):
        # The bad Parle-G entry: 3.4 kcal for a biscuit.
        p = product(ingredients_text="Wheat flour, sugar", is_liquid=False,
                    nutrition=Nutrition(energy_kcal=3.4, carbs_g=77, sugars_g=25, protein_g=7, fat_g=12))
        issues = sanity.check(p)
        assert any("doesn't match macros" in i for i in issues)
        assert any("Implausibly low" in i for i in issues)


class TestClaims:
    def test_zero_is_not_literally_zero(self):
        p = product(name="Coca-Cola Zero Sugar", is_liquid=True, nutrition=Nutrition(energy_kcal=0.3, sugars_g=0))
        found = {c.rule: c for c in claims.evaluate(p)}
        assert found["energy_free"].passes is True
        assert "not literally zero" in found["energy_free"].message
        assert found["sugar_free"].passes is True

    def test_claim_fails_above_limit(self):
        p = product(name="Lite Cola", claims=["Low Calorie"], is_liquid=True, nutrition=Nutrition(energy_kcal=25))
        [c] = claims.evaluate(p)
        assert c.rule == "low_energy" and c.passes is False

    def test_energy_free_not_defined_for_solids(self):
        p = product(name="Zero biscuits", is_liquid=False, nutrition=Nutrition(energy_kcal=400))
        assert claims.evaluate(p) == []

    def test_missing_value(self):
        p = product(claims=["Sugar Free"], is_liquid=False)
        [c] = claims.evaluate(p)
        assert c.passes is None


SUBSTANCES = [
    {"id": "titanium-dioxide", "name": "Titanium dioxide", "codes": ["E171"], "synonyms": ["titanium dioxide"],
     "category": "colour", "regions": {
         "EU": {"status": "banned", "since": "2022", "reason": "Genotoxicity concerns.",
                "quote": "...", "source_url": "https://eur-lex.europa.eu/x"},
         "US": {"status": "restricted", "quote": "...", "source_url": "https://ecfr.gov/x"},
         "IN": {"status": "unknown"}}},
    {"id": "trans-fat", "name": "Trans fats", "codes": [], "synonyms": ["vanaspati", "partially hydrogenated"],
     "category": "fat", "regions": {}},
    {"id": "phosphoric-acid", "name": "Phosphoric acid", "codes": ["E338"], "synonyms": ["phosphoric acid"],
     "category": "acidity regulator", "regions": {"EU": {"status": "allowed", "quote": "...", "source_url": "u"}}},
]


class TestSubstances:
    @pytest.fixture(autouse=True)
    def table(self, monkeypatch):
        monkeypatch.setattr(substances, "table", lambda: SUBSTANCES)

    def test_banned_somewhere_is_flagged(self):
        [f] = substances.evaluate(product(additives=["E171"]))
        assert f.flagged and f.message == "Titanium dioxide: banned in the EU (from 2022)."
        assert f.status["IN"].status == "unknown" and f.matched == "E171"

    def test_matches_by_name_without_code(self):
        [f] = substances.evaluate(product(ingredients_text="Wheat flour, Vanaspati, salt"))
        assert f.id == "trans-fat" and f.matched == "vanaspati" and not f.flagged

    def test_name_match_needs_whole_words(self):
        # "phosphoric acidity" must not count as "phosphoric acid"
        assert substances.evaluate(product(ingredients_text="contains phosphoric acidity regulator")) == []

    def test_allowed_is_not_flagged(self):
        [f] = substances.evaluate(product(additives=["E338"]))
        assert not f.flagged

    def test_unlisted_code_is_skipped(self):
        assert substances.evaluate(product(additives=["E9999"])) == []


class TestVerdict:
    def test_bad_data_gets_no_accusations(self):
        v = verdict.build(parse(OFF_COKE_ZERO_IN))
        assert v.headline == ["This product's online data looks unreliable. Scan the back label to check it."]

    def test_counterpart_diff_in_headline(self):
        p = product(name="Cola", is_liquid=True, additives=["E338", "E150d"],
                    ingredients_text="water", nutrition=Nutrition(energy_kcal=40))
        cp = Counterpart(region="EU", country="UK", product_name="Cola", ingredients_quote="...",
                         source_url="https://example.com", only_in_india=["E150d"])
        v = verdict.build(p, cp)
        assert any("UK version" in h and "E150d" in h for h in v.headline)


def test_grounding():
    page = "Ingredients: Carbonated Water,\n  Colour (Caramel E150d), Phosphoric Acid. Nutrition..."
    assert grounded("carbonated water, colour (caramel e150d), phosphoric acid", page)
    assert not grounded("carbonated water, sugar, colour (caramel e150d)", page)
    assert not grounded("water", page)


def test_facts_come_with_sources(monkeypatch):
    table = [{**SUBSTANCES[0], "regions": {
        "EU": {**SUBSTANCES[0]["regions"]["EU"], "fact": "The EU banned titanium dioxide in food in 2022."},
        "US": {**SUBSTANCES[0]["regions"]["US"], "fact": "The US limits titanium dioxide to 1% of food weight."}}}]
    monkeypatch.setattr(substances, "table", lambda: table)
    assert substances.facts() == [{"text": "The EU banned titanium dioxide in food in 2022.",
                                   "source_url": "https://eur-lex.europa.eu/x",
                                   "substance": "Titanium dioxide", "region": "EU"}]


def test_normalise_sub_numbers_without_brackets():
    # From a real Wai Wai listing: "500ii", "170i" and "4511" (451(1) with brackets lost).
    assert additive_codes.find_in_text("Acidity regulators (INS 500ii, INS 170i), Humectant (INS 4511)") == [
        "E500", "E170", "E451"]
    # Real 4-digit codes and letter suffixes are kept.
    assert additive_codes.normalise("1400") == "E1400" and additive_codes.normalise("150d") == "E150d"
    assert additive_codes.normalise("472e") == "E472e"
