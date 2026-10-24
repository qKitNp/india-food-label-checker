import asyncio

import httpx
import numpy as np

from app import catalog
from app.agent import Identification, _Run, number_on_page
from app.config import Settings
from app.models import Nutrition, Product
from app.services import ProductService
from app.store import MemoryStore
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
