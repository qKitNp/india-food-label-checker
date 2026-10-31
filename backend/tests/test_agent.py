import asyncio

import httpx
import numpy as np

from app import catalog
from app.agent import Identification, _Run, number_on_page
from app.config import Settings
from app.models import Nutrition, Product
from app.services import ProductService
from app.sources.counterpart import grounded
from app.store import MemoryStore, Vectors
from tests.test_api import FakeEmbedder

PAGE = """Kurkure Masala Munch 90 g. Ingredients: Rice Meal, Edible Vegetable Oil (Palmolein),
Corn Meal, Gram Meal, Spices & Condiments, Salt, Sugar, Acidity Regulator (330), Flavour Enhancers (627, 631).
Nutrition per 100 g: Energy 556 kcal, Protein 6.6 g, Carbohydrate 56.3 g, Fat 34.4 g."""
QUOTE = ("Rice Meal, Edible Vegetable Oil (Palmolein),\nCorn Meal, Gram Meal, Spices & Condiments, Salt, Sugar, "
         "Acidity Regulator (330), Flavour Enhancers (627, 631).")
URL = "https://www.bigbasket.com/pd/kurkure-masala-munch"
IDENT = Identification(brand="Kurkure", product_name="Masala Munch")


def run_with_page() -> _Run:
    run = _Run(Settings(), ProductService(MemoryStore(), httpx.AsyncClient(), None), None)
    run.pages[URL] = PAGE
    return run


def test_number_on_page():
    assert number_on_page(556, PAGE)
    assert number_on_page(6.6, PAGE)
    assert not number_on_page(600, PAGE)
    assert not number_on_page(56, PAGE)  # only "56.3" is on the page
    assert number_on_page(0.3, "Energy 0,3 kcal")


def test_submit_accepts_grounded_quote_and_drops_unseen_numbers():
    run = run_with_page()
    result = run.submit({"brand": "Kurkure", "name": "Masala Munch", "source_url": URL, "ingredients_quote": QUOTE,
                         "energy_kcal": 556, "fat_g": 34.4, "sugars_g": 3.1}, IDENT)
    assert result.startswith("Accepted") and "sugars_g" in result
    p = run.final.product
    assert p.source == "web" and p.verified is False and p.evidence.source_url == URL
    assert p.nutrition.energy_kcal == 556 and p.nutrition.sugars_g is None
    assert p.additives == ["E330", "E627", "E631"]


def test_submit_rejects_made_up_quote():
    run = run_with_page()
    result = run.submit({"brand": "Kurkure", "name": "Masala Munch", "source_url": URL,
                         "ingredients_quote": "Rice Meal, Edible Vegetable Oil, Tartrazine (102), Salt"}, IDENT)
    assert result.startswith("Rejected") and run.final is None


def test_submit_rejects_other_variant_page():
    run = run_with_page()
    chatka = Identification(brand="Kurkure", product_name="Chilli Chatka")
    result = run.submit({"brand": "Kurkure", "name": "Chilli Chatka", "source_url": URL, "ingredients_quote": QUOTE},
                        chatka)
    assert "different variant" in result and run.final is None


def test_mentions_product_allows_spelling_drift():
    from app.agent import mentions_product
    page = "Kurkure Red Chilli Chataka Chips | Tangy & Crunchy. Ingredients: ..."
    assert mentions_product(page, Identification(brand="Kurkure", product_name="Chilli Chatka"))
    assert not mentions_product(page, Identification(brand="Kurkure", product_name="Masala Munch"))


def test_submit_rejects_unread_page():
    run = run_with_page()
    assert run.submit({"source_url": "https://example.com", "ingredients_quote": QUOTE}, IDENT).startswith("Rejected")


def test_vector_index_ranks_and_updates():
    index = catalog.VectorIndex()
    index.add("a", "a", np.array([1, 0] + [0] * 766, dtype=np.float32))
    index.add("b", "b", np.array([0, 1] + [0] * 766, dtype=np.float32))
    [top, second] = index.search(np.array([0.9, 0.1] + [0] * 766, dtype=np.float32), k=2)
    assert top.key == "a" and top.score > second.score
    index.add("a", "a2", np.array([0, 1] + [0] * 766, dtype=np.float32))  # re-adding replaces
    assert len(index) == 2 and index.search(np.array([1, 0] + [0] * 766, dtype=np.float32), k=1)[0].score < 0.01


def test_vector_roundtrip():
    v = np.random.default_rng(0).random(768).astype(np.float32)
    assert np.array_equal(catalog.decode(catalog.encode(v)), v)


def test_service_indexes_on_save_and_reloads():
    async def go():
        store = MemoryStore()
        svc = ProductService(store, httpx.AsyncClient(), FakeEmbedder())
        p = Product(brand="Kurkure", name="Masala Munch", source="web", nutrition=Nutrition())
        await svc.save(p)
        assert p.key.startswith("n-") and (await svc.similar("Kurkure Masala Munch"))[0].key == p.key
        fresh = ProductService(store, httpx.AsyncClient(), FakeEmbedder())
        await fresh.load_index()
        assert len(fresh.index) == 1
    asyncio.run(go())


def axis(i: int) -> np.ndarray:
    v = np.zeros(768, dtype=np.float32)
    v[i] = 1
    return v


def test_photo_scan_blends_name_and_image():
    index = catalog.VectorIndex()
    # Same brand, so names are close; packs differ.
    index.add("munch", "Kurkure Masala Munch", name=axis(0) + 0.9 * axis(1), image=axis(2))
    index.add("tomato", "Kurkure Naughty Tomato", name=axis(0) + 1.0 * axis(1), image=axis(3))
    # Name read from the pack slightly favours "tomato", but the photo clearly matches "munch".
    [top, second] = index.search(name=axis(0) + 1.0 * axis(1), image=axis(2), k=2, image_weight=0.5)
    assert top.key == "munch" and top.image_score > 0.99 and top.name_score < second.name_score


def test_voice_query_also_matches_pack_images():
    index = catalog.VectorIndex()
    index.add("photo-only", "Unnamed snack", image=axis(5))
    index.add("named", "Other", name=axis(6))
    [top] = index.search(name=axis(5), k=1)
    assert top.key == "photo-only" and top.name_score is None and top.image_score > 0.99


def test_products_without_image_still_rank_by_name():
    index = catalog.VectorIndex()
    index.add("a", "A", name=axis(0))
    index.add("b", "B", name=axis(1), image=axis(2))
    assert index.search(name=axis(0), image=axis(9), k=1)[0].key == "a"


def test_vectors_from_another_model_are_reembedded():
    async def go():
        store = MemoryStore()
        p = Product(brand="Kurkure", name="Masala Munch", source="web")
        await store.save_product(p, Vectors("nomic-embed-text", name=axis(0)))
        svc = ProductService(store, httpx.AsyncClient(), FakeEmbedder())
        await svc.load_index()
        assert store.stored_vectors[p.key].model == "fake-embedder" and len(svc.index) == 1
    asyncio.run(go())


def test_save_with_photo_stores_image_and_vector():
    async def go():
        store = MemoryStore()
        svc = ProductService(store, httpx.AsyncClient(), FakeEmbedder())
        saved = await svc.save(Product(brand="Kurkure", name="Masala Munch", source="web"),
                               image=b"orange kurkure pack")
        assert saved.image_file_id in store.images
        assert store.stored_vectors[saved.key].image is not None
        # A later save without a photo keeps the stored image vector.
        await svc.save(saved)
        assert store.stored_vectors[saved.key].image is not None
        [top] = await svc.similar(image_vector=(await FakeEmbedder().images([b"orange kurkure pack"]))[0], k=1)
        assert top.key == saved.key
    asyncio.run(go())


def test_bulleted_ingredients_match_comma_separated_quote():
    # Blinkit/Instamart list ingredients as bullets; the agent writes them with commas.
    page = "Ingredients\n\n- Water\n- Mango Pulp (26.5%)\n- Sugar\n- Acidity Regulator (330)\n- Antioxidant (300)"
    assert grounded("Water, Mango Pulp (26.5%), Sugar, Acidity Regulator (330), Antioxidant (300)", page)
    # Still rejected: an invented ingredient, or a reordered list.
    assert not grounded("Water, Mango Pulp (26.5%), Sugar, Colour (102), Acidity Regulator (330)", page)
    assert not grounded("Sugar, Water, Mango Pulp (26.5%), Acidity Regulator (330), Antioxidant (300)", page)


def test_variant_check_ignores_generic_words():
    from app.agent import mentions_product
    page = "Wai Wai Ready to Eat Veg Masala Flavoured Instant Noodles Pack of 6"
    assert mentions_product("Buy Wai Wai Ready to Eat Veg Masala Noodles 70 g", Identification(
        brand="Wai Wai", product_name="Veg Masala Flavour Instant Noodles"))
    # A different variant is still rejected.
    assert not mentions_product(page, Identification(brand="Wai Wai", product_name="Chicken Noodles"))


def test_catalog_pick_must_share_a_name_word():
    from app.agent import Identification, shares_a_name_word
    from app.models import Product
    coke_zero = Product(source="off", brand="Coca-Cola", name="Coke Zero Can")
    assert not shares_a_name_word(coke_zero, Identification(brand="Coca-Cola", product_name="No Sugar No Calories Crisp Taste"))
    assert shares_a_name_word(coke_zero, Identification(brand="Coca-Cola", product_name="Coke Zero"))
    munch = Product(source="web", brand="Kurkure", name="Kurkure Namkeen Masala Munch")
    assert shares_a_name_word(munch, Identification(brand="Kurkure", product_name="Masala Munch"))
    assert shares_a_name_word(munch, Identification(brand="Kurkure", product_name=None))
