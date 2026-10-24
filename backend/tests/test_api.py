import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import asyncio
import io
import time

import numpy as np
from PIL import Image

from app import agent, llm
from app.config import get_settings
from app.main import app
from app.models import Nutrition, Product
from tests.test_rules import OFF_COKE_ZERO_IN

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{}.json"


def jpeg(size=(64, 64), fmt="JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "orange").save(out, fmt)
    return out.getvalue()


PHOTO = jpeg()


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
        assert images == [(PHOTO, "image/jpeg")]
        return schema(name="Coke Zero", ingredients_text="Water, colour (150d), INS 951", is_liquid=True)
    monkeypatch.setattr(llm, "complete_json", fake)
    r = client.post("/labels/extract", files={"images": ("a.jpg", PHOTO, "image/jpeg")}, data={"barcode": "1"})
    assert r.status_code == 200
    p = r.json()["product"]
    assert p["additives"] == ["E150d", "E951"] and p["verified"] is False and p["barcode"] == "1"


def poll(client, url, until=("done", "failed"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(url).json()
        if job["status"] in until:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job still {job['status']}")


def test_identify_runs_as_background_job(client, monkeypatch):
    gate = asyncio.Event()

    async def fake_find(settings, products, exa, images, steps):
        steps.append(agent.Step(tool="read_pack", args={}, result="Kurkure Masala Munch"))
        await gate.wait()  # hold the job open so progress can be observed mid-run
        product = Product(brand="Kurkure", name="Masala Munch", source="web",
                          ingredients_text="Rice meal, corn meal, gram meal", nutrition=Nutrition(energy_kcal=550))
        return agent.AgentResult(status="web", identification=agent.Identification(brand="Kurkure"),
                                 product=product, trace=list(steps))
    monkeypatch.setattr(agent, "find_from_front", fake_find)

    r = client.post("/identify", files={"images": ("a.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 202
    url = r.json()["poll_url"]

    running = poll(client, url, until=("running",))
    assert [s["tool"] for s in running["steps"]] == ["read_pack"] and running["result"] is None
    assert running["stage"] == "matching"

    client.portal.call(gate.set)
    done = poll(client, url)
    assert done["status"] == "done" and done["stage"] == "done" and done["result"]["result"]["status"] == "web"
    assert done["result"]["verdict"]["headline"]

    # The full trace is stored for observability.
    [log] = client.app.state.products.store.scan_logs
    assert log["job_id"] == done["id"] and log["outcome"] == "web" and log["steps"][0]["tool"] == "read_pack"


def test_failed_job_reports_error(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise llm.LLMError("deepseek/deepseek-flash failed: timeout")
    monkeypatch.setattr(agent, "find_from_front", boom)
    url = client.post("/identify", files={"images": ("a.jpg", jpeg(), "image/jpeg")}).json()["poll_url"]
    job = poll(client, url)
    assert job["status"] == "failed" and "timeout" in job["error"]


def test_identify_rejects_unreadable_image_before_starting_job(client):
    r = client.post("/identify", files={"images": ("a.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope").status_code == 404


def test_llm_failure_is_502(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise llm.LLMError("deepseek/deepseek-flash failed: timeout")
    monkeypatch.setattr(llm, "complete_json", fail)
    r = client.post("/labels/extract", files={"images": ("a.jpg", PHOTO, "image/jpeg")})
    assert r.status_code == 502


class TestUploadLimits:
    def post(self, client, files):
        return client.post("/labels/extract", files=[("images", f) for f in files])

    def test_png_and_webp_accepted_formats(self, client, monkeypatch):
        async def fake(settings, schema, prompt, images=None):
            return schema(name="x")
        monkeypatch.setattr(llm, "complete_json", fake)
        for fmt in ("PNG", "WEBP"):
            assert self.post(client, [("a", jpeg(fmt=fmt), "image/" + fmt.lower())]).status_code == 200

    def test_other_formats_rejected(self, client):
        for fmt in ("GIF", "BMP", "TIFF"):
            r = self.post(client, [("a", jpeg(fmt=fmt), "image/jpeg")])  # lying content type
            assert r.status_code == 400 and "JPEG, PNG or WebP" in r.json()["detail"]

    def test_too_many_images(self, client):
        assert self.post(client, [("a.jpg", PHOTO, "image/jpeg")] * 5).status_code == 400

    def test_too_large_file(self, client, monkeypatch):
        import app.main as main
        monkeypatch.setattr(main, "MAX_IMAGE_BYTES", len(PHOTO) - 1)
        assert self.post(client, [("a.jpg", PHOTO, "image/jpeg")]).status_code == 413

    def test_too_many_pixels(self, client, monkeypatch):
        from PIL import Image
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
        r = self.post(client, [("a.jpg", jpeg(size=(100, 100)), "image/jpeg")])
        assert r.status_code == 400 and "megapixels" in r.json()["detail"]

    def test_bad_second_image_rejected_before_job_starts(self, client):
        r = client.post("/identify", files=[("images", ("a.jpg", PHOTO, "image/jpeg")),
                                            ("images", ("b.jpg", b"not an image", "image/jpeg"))])
        assert r.status_code == 400
