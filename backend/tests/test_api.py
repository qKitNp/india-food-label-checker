import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import numpy as np

from app import llm
from app.config import get_settings
from app.main import app
from app.models import Nutrition, Product
from tests.test_rules import OFF_COKE_ZERO_IN

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{}.json"


class FakeEmbedder:
    """Bag-of-words vectors: texts sharing words are similar, unrelated texts score 0.
    An image's 'content' is its bytes decoded as text, so tests can control image similarity."""

    model_id = "fake-embedder"

    @staticmethod
    def _bow(text):
        v = np.zeros(768, dtype=np.float32)
        for word in text.lower().split():
            v[hash(word) % 768] += 1
        return v

    async def queries(self, texts):
        return [self._bow(t) for t in texts]

    async def documents(self, products):
        return [self._bow(text) for _title, text in products]

    async def images(self, images):
        return [self._bow(b.decode()) for b in images]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APPWRITE_PROJECT_ID", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@respx.mock
def test_product_lookup_then_cached(client):
    route = respx.get(OFF_URL.format("8901764111105")).mock(
        return_value=httpx.Response(200, json={"status": 1, "product": OFF_COKE_ZERO_IN})
    )
    r = client.get("/products/8901764111105")
    assert r.status_code == 200
    assert r.json()["data_issues"]
    client.get("/products/8901764111105")
    assert route.call_count == 1  # second hit served from the store


@respx.mock
def test_unknown_product(client):
    respx.get(OFF_URL.format("123")).mock(return_value=httpx.Response(200, json={"status": 0}))
    assert client.get("/products/123").status_code == 404


def test_confirm_marks_verified(client):
    body = {"barcode": "999", "name": "Coca-Cola Zero Sugar", "is_liquid": True, "source": "off",
            "ingredients_text": "Carbonated water, colour (150d), acid (338), sweeteners (951, 950)",
            "additives": ["E150d", "E338", "E951", "E950"],
            "nutrition": {"energy_kcal": 0.3, "sugars_g": 0, "carbs_g": 0, "fat_g": 0, "protein_g": 0}}
    r = client.post("/products/confirm", json=body)
    assert r.status_code == 200
    v = r.json()
    assert v["product"]["verified"] is True
    assert any("not literally zero" in h for h in v["headline"])


@respx.mock
def test_voice_lookup_strips_non_digits(client):
    respx.get(OFF_URL.format("8901764111105")).mock(
        return_value=httpx.Response(200, json={"status": 1, "product": OFF_COKE_ZERO_IN})
    )
    r = client.post("/voice/lookup", json={"barcode": "8901 7641 11105"})
    assert r.json()["found"] is True
    # The OFF entry contradicts itself, so the voice agent mustn't make claims about it.
    assert r.json()["answer"].startswith("I found Coca-Cola Coke Zero Can, but I don't have reliable data")


def test_voice_lookup_by_name(client):
    svc = client.app.state.products
    svc.embedder = FakeEmbedder()

    async def seed():
        await svc.save(Product(barcode="111", brand="Kurkure", name="Masala Munch", source="label",
                               verified=True, ingredients_text="Rice meal, corn meal, gram meal, edible oil",
                               nutrition=Nutrition(energy_kcal=550)))
    client.portal.call(seed)

    r = client.post("/voice/lookup", json={"product_name": "kurkure masala munch"}).json()
    assert r["found"] is True and r["answer"].startswith("Kurkure Masala Munch.")
    r = client.post("/voice/lookup", json={"product_name": "maggi noodles"}).json()
    assert r["found"] is False


def test_voice_lookup_needs_input(client):
    assert client.post("/voice/lookup", json={}).status_code == 422


def test_label_extract(client, monkeypatch):
    async def fake(settings, schema, prompt, images=None):
        assert images == [(b"x", "image/jpeg")]
        return schema(name="Coke Zero", ingredients_text="Water, colour (150d), INS 951", is_liquid=True)
    monkeypatch.setattr(llm, "complete_json", fake)
    r = client.post("/labels/extract", files={"images": ("a.jpg", b"x", "image/jpeg")}, data={"barcode": "1"})
    assert r.status_code == 200
    p = r.json()["product"]
    assert p["additives"] == ["E150d", "E951"] and p["verified"] is False and p["barcode"] == "1"


def test_llm_failure_is_502(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise llm.LLMError("deepseek/deepseek-flash failed: timeout")
    monkeypatch.setattr(llm, "complete_json", fail)
    r = client.post("/labels/extract", files={"images": ("a.jpg", b"x", "image/jpeg")})
    assert r.status_code == 502
