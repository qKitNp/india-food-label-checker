from app import ingredients
from app.models import AdditiveFinding, RegionStatus

KURKURE = ("Cereal products (64%) (rice meal (43.9%), corn meal (20%)), edible vegetable oil (palmolein), "
           "seasoning (iodised salt, sugar, acidity regulators (330, 296), colour (160c)), gram meal (9%)")
MAGGI = ("Noodles: Refined wheat flour (Maida), Thickeners (508 & 412), Acidity regulators (501(i) & 500(i)) "
         "and Humectant (451(1)). Masala Tastemaker: Mixed Spices (26.2%) (Onion powder), Colour (150d)")
FROOTI = "Ingredients\n\n- Water\n- Mango Pulp (26.5%)\n- Sugar\n- Acidity Regulator (330)"


def names(items):
    return [i.name for i in items]


def test_percentages_and_sub_ingredients():
    items = ingredients.parse(KURKURE)
    assert names(items) == ["Cereal products", "edible vegetable oil", "seasoning", "gram meal"]
    cereal, _, seasoning, gram = items
    assert cereal.percent == "64" and [(s.name, s.percent) for s in cereal.sub] == [("rice meal", "43.9"), ("corn meal", "20")]
    assert gram.percent == "9"
    acidity = seasoning.sub[2]
    assert acidity.name == "acidity regulators" and acidity.codes == ["E330", "E296"] and acidity.sub == []


def test_code_groups_sub_numbers_and_sections():
    items = ingredients.parse(MAGGI)
    assert [i.section for i in items] == ["Noodles"] * 3 + ["Masala Tastemaker"] * 2
    assert items[1].name == "Thickeners" and items[1].codes == ["E508", "E412"]
    assert items[2].name == "Acidity regulators and Humectant" and items[2].codes == ["E501", "E500", "E451"]
    assert items[3].percent == "26.2" and names(items[3].sub) == ["Onion powder"]


def test_bulleted_list():
    items = ingredients.parse(FROOTI)
    assert names(items) == ["Water", "Mango Pulp", "Sugar", "Acidity Regulator"]
    assert items[1].percent == "26.5" and items[3].codes == ["E330"]


def finding(fid, code, flagged):
    status = {r: RegionStatus(status="banned" if flagged and r == "EU" else "allowed") for r in ("IN", "EU", "US")}
    return AdditiveFinding(id=fid, code=code, name=fid, category="x", matched=code or fid, status=status,
                           flagged=flagged, message=f"{fid}: banned in the EU." if flagged else "")


def test_flags_banned_by_code_and_by_name():
    items = ingredients.parse("Sugar, Colour (171), Vanaspati, Acidity regulator (330)")
    findings = [finding("titanium-dioxide", "E171", True), finding("trans-fat", "", True),
                finding("phosphoric-acid", "E330", False)]
    ingredients.flag(items, findings, {"trans-fat": ["vanaspati"]})
    assert [i.flagged for i in items] == [False, True, True, False]
    assert items[1].flag_reason == "titanium-dioxide: banned in the EU."


def test_flags_sub_ingredients():
    items = ingredients.parse("Seasoning (salt, colour (171))")
    ingredients.flag(items, [finding("titanium-dioxide", "E171", True)], {})
    assert not items[0].flagged and items[0].sub[1].flagged


def test_empty():
    assert ingredients.parse(None) == [] and ingredients.parse("") == []


def test_section_label_with_stray_quote():
    items = ingredients.parse('Noodles: Wheat flour, Salt. Masala Tastemaker": Mixed Spices (26.2%), Sugar')
    assert [(i.section, i.name) for i in items] == [
        ("Noodles", "Wheat flour"), ("Noodles", "Salt"), ("Masala Tastemaker", "Mixed Spices"), ("Masala Tastemaker", "Sugar")]
