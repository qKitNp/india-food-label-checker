import random

from app import verdict, voice
from app.models import Counterpart, Nutrition, Product

UK_QUOTE = ("Carbonated Water, Colour (Caramel E150d), Sweeteners (Aspartame, Acesulfame K), Natural "
            "Flavourings, Caffeine Flavouring, Acids (Phosphoric Acid, Citric Acid).")


def diet_coke() -> Product:
    return Product(source="label", verified=True, brand="Coca-Cola", name="Diet coke", is_liquid=True,
                   ingredients_text="carbonated water, caramel color, aspartame, phosphoric acid, "
                                    "potassium benzoate, natural flavors, citric acid, and caffeine",
                   additives=["E150c", "E212", "E330", "E338", "E951"], claims=["zero calorie"],
                   nutrition=Nutrition(energy_kcal=0, sugars_g=0, sodium_mg=0))


def say(product, counterpart=None):
    return voice.describe(verdict.build(product, counterpart), random.Random(1))


def test_diet_coke_sounds_like_a_person():
    cp = Counterpart(region="EU", country="United Kingdom", product_name="Diet Coke",
                     ingredients_quote=UK_QUOTE, source_url="https://www.coca-cola.com/gb/en/brands/diet-coke")
    s = say(diet_coke(), cp)
    assert "Diet Coke." in s.answer and "Coca-Cola" not in s.answer
    assert "zero-calorie claim checks out" in s.answer
    assert "sweetened with aspartame" in s.answer
    assert s.answer.endswith("Want to hear how it differs from the UK version?")
    assert "kcal" not in s.answer and "per 100 ml" not in s.answer
    # The UK can lists aspartame too (by name), so it must not show up as India-only.
    compare = s.more[0]
    assert compare.startswith("Compared with the UK version")
    assert "acesulfame K" in compare and "aspartame" not in compare.split("instead")[0].split("has")[1]


def test_bracketed_codes_and_names_count_for_the_comparison():
    cp = verdict.compare(diet_coke(), Counterpart(region="EU", country="United Kingdom", product_name="Diet Coke",
                                                  ingredients_quote=UK_QUOTE, source_url="x"))
    assert {"E150d", "E951", "E950", "E338"} <= set(cp.additives)
    assert "E951" not in cp.only_in_india and "E950" in cp.only_in_counterpart


def test_banned_additive_leads():
    p = Product(source="label", verified=True, brand="Kurkure", name="Masala Munch",
                ingredients_text="rice meal, colour (INS 171)", additives=["E171"])
    s = say(p)
    assert s.answer.startswith(("That's Kurkure Masala Munch.", "This is Kurkure Masala Munch.",
                                "Okay, that's Kurkure Masala Munch."))
    assert "titanium dioxide, which is banned in the eu" in s.answer.lower()


def test_sugary_drink_in_teaspoons_per_glass():
    p = Product(source="label", verified=True, brand="Frooti", name="Frooti Mango Drink", is_liquid=True,
                ingredients_text="water, mango pulp, sugar", nutrition=Nutrition(sugars_g=13))
    s = say(p)
    assert "about 8 teaspoons of sugar in a glass" in s.answer
    assert any("13 grams of sugar" in m for m in s.more)


def test_salty_snack_gets_a_salt_line():
    p = Product(source="label", verified=True, brand="Kurkure", name="Masala Munch", ingredients_text="rice meal",
                nutrition=Nutrition(sodium_mg=800))
    assert "about 40% of the salt the WHO says to have in a whole day" in say(p).answer


def test_follow_up_facts_explain_each_additive():
    more = say(diet_coke()).more
    assert "Aspartame is a sweetener added to this product." in more
    rules = next(m for m in more if m.startswith("The rules on aspartame:"))
    assert "India" in rules  # the checked rule, kept apart from the plain line
    assert sum(m.startswith("Aspartame and phosphoric acid have legal usage limits") for m in more) == 1


def test_drinks_by_name_shouting_names_and_one_teaspoon():
    p = Product(source="web", brand="Gatorade", name="GATORADE THIRST QUENCHER", ingredients_text="water, sugar",
                nutrition=Nutrition(sugars_g=6))
    s = say(p)
    assert "Gatorade Thirst Quencher." in s.answer
    assert "teaspoons of sugar in a glass" in s.answer and "100 grams" not in s.answer
    one = Product(source="web", brand="X", name="Plain Crackers", ingredients_text="flour", nutrition=Nutrition(sugars_g=5))
    assert "about 1 teaspoon of sugar in every 100 grams" in say(one).answer
