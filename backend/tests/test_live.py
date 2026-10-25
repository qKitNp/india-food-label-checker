import io

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from app import agent, barcodes
from app.config import get_settings
from app.main import app
from app.models import Nutrition, Product


def jpeg(size=(64, 64)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "orange").save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APPWRITE_PROJECT_ID", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("EXA_API_KEY", "")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("BOLNA_API_KEY", "")
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_live_frame_rejects_bad_session(client):
    r = client.post("/live/frame", data={"session_id": "x"},
                    files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 400


def test_live_lookup_no_frame(client):
    r = client.post("/voice/live-lookup", json={"session_id": "nosuchsession123"})
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False and body["reason"] == "no_frame"


def test_live_lookup_barcode_hit(client, monkeypatch):
    async def seed():
        await client.app.state.products.store.save_product(
            Product(barcode="8901764111105", brand="Kurkure", name="Masala Munch", source="label",
                    verified=True, ingredients_text="Rice meal, corn meal",
                    nutrition=Nutrition(energy_kcal=550)))
    client.portal.call(seed)
    monkeypatch.setattr(barcodes, "read", lambda data: ["8901764111105"])

    sid = "testsession-barcode1"
    r = client.post("/live/frame", data={"session_id": sid},
                    files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    assert r.json()["ok"] is True

    body = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert body["found"] is True and body["reason"] == "barcode"
    assert "Kurkure" in body["answer"]


def test_live_lookup_unknown_starts_job(client, monkeypatch):
    monkeypatch.setattr(barcodes, "read", lambda data: [])

    async def no_vision(settings, images, usage=None):
        raise agent.llm.LLMError("no key")
    monkeypatch.setattr(agent, "identify", no_vision)

    async def fake_find(settings, products, exa, images, steps):
        steps.append(agent.Step(tool="read_pack", args={}, result="pack"))
        return agent.AgentResult(status="not_found",
                                 identification=agent.Identification(brand="X"),
                                 reason="nope", trace=list(steps))
    monkeypatch.setattr(agent, "find_from_front", fake_find)

    sid = "testsession-unknown1"
    client.post("/live/frame", data={"session_id": sid},
                files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    body = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    # The job finished inside the tool's time budget, so the answer is final.
    assert body["found"] is False and body["reason"] == "not_found"
    assert "barcode" in body["answer"].lower()


def test_live_result_feeds_voice_answers_to_page(client, monkeypatch):
    async def seed():
        await client.app.state.products.store.save_product(
            Product(barcode="8901764111105", brand="Kurkure", name="Masala Munch", source="label",
                    verified=True, ingredients_text="Rice meal, corn meal",
                    nutrition=Nutrition(energy_kcal=550)))
    client.portal.call(seed)
    monkeypatch.setattr(barcodes, "read", lambda data: ["8901764111105"])

    assert client.get("/live/result", params={"session_id": "testsession-feed1"}).json()["found"] is False
    sid = "testsession-feed1"
    client.post("/live/frame", data={"session_id": sid},
                files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    answer = client.post("/voice/live-lookup", json={"session_id": sid}).json()["answer"]
    fed = client.get("/live/result", params={"session_id": sid}).json()
    assert fed["found"] is True and fed["answer"] == answer and fed["product_key"] == "8901764111105"


def test_live_session_needs_key(client):
    r = client.post("/live/session", json={"user_data": {"session_id": "abc"}})
    assert r.status_code == 503  # BOLNA_API_KEY not set in tests


OFF_SEARCH_HIT = {"products": [{
    "code": "8901491361026", "product_name": "Kurkure Masala Munch", "brands": "Kurkure",
    "ingredients_text": "Rice meal, corn meal, gram meal, edible oil",
    "nutriments": {"energy-kcal_100g": 550}, "quantity": "90 g", "labels": "",
}]}


@respx.mock
def test_live_lookup_vision_barcode(client, monkeypatch):
    async def seed():
        await client.app.state.products.store.save_product(
            Product(barcode="8901764111105", brand="Kurkure", name="Masala Munch", source="label",
                    verified=True, ingredients_text="Rice meal, corn meal",
                    nutrition=Nutrition(energy_kcal=550)))
    client.portal.call(seed)
    monkeypatch.setattr(barcodes, "read", lambda data: [])

    async def fake_identify(settings, images, usage=None):
        return agent.Identification(brand="Kurkure", product_name="Masala Munch",
                                    barcode="8901764111105")
    monkeypatch.setattr(agent, "identify", fake_identify)

    sid = "testsession-visionbc1"
    client.post("/live/frame", data={"session_id": sid},
                files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    body = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert body["found"] is True and body["reason"] == "barcode"


@respx.mock
def test_live_lookup_off_name_search(client, monkeypatch):
    respx.get("https://world.openfoodfacts.org/cgi/search.pl").mock(
        return_value=httpx.Response(200, json=OFF_SEARCH_HIT))
    monkeypatch.setattr(barcodes, "read", lambda data: [])

    async def fake_identify(settings, images, usage=None):
        return agent.Identification(brand="Kurkure", product_name="Masala Munch")
    monkeypatch.setattr(agent, "identify", fake_identify)

    sid = "testsession-offname1"
    client.post("/live/frame", data={"session_id": sid},
                files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    body = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert body["found"] is True and body["reason"] == "name"
    assert "Kurkure" in body["answer"]
    # Saved for next time: the re-ask hits the cache.
    assert client.portal.call(client.app.state.products.store.get_product, "8901491361026") is not None


@respx.mock
def test_live_lookup_not_food(client, monkeypatch):
    monkeypatch.setattr(barcodes, "read", lambda data: [])

    async def fake_identify(settings, images, usage=None):
        return agent.Identification(is_packaged_food=False)
    monkeypatch.setattr(agent, "identify", fake_identify)

    sid = "testsession-notfood1"
    client.post("/live/frame", data={"session_id": sid},
                files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    body = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert body["found"] is False and body["reason"] == "not_food" and body["job_id"] is None


@respx.mock
def test_live_lookup_name_match_rejects_sibling_variant(client, monkeypatch):
    """A Chilli Chatka pack must not be answered with Masala Munch just because the
    names embed close; it falls through to OFF and then the background job."""
    from app.catalog import Match

    async def seed():
        await client.app.state.products.store.save_product(
            Product(barcode="8901764111105", brand="Kurkure", name="Masala Munch", source="label",
                    verified=True, ingredients_text="Rice meal, corn meal",
                    nutrition=Nutrition(energy_kcal=550)))
    client.portal.call(seed)
    respx.get("https://world.openfoodfacts.org/cgi/search.pl").mock(
        return_value=httpx.Response(200, json=OFF_SEARCH_HIT))
    monkeypatch.setattr(barcodes, "read", lambda data: [])

    async def fake_identify(settings, images, usage=None):
        return agent.Identification(brand="Kurkure", product_name="Chilli Chatka")
    monkeypatch.setattr(agent, "identify", fake_identify)

    async def close_name(text=None, image_vector=None, k=1):
        return [Match(key="8901764111105", score=0.72, text="Kurkure Masala Munch")]
    monkeypatch.setattr(client.app.state.products, "similar", close_name)

    async def fake_find(settings, products, exa, images, steps):
        return agent.AgentResult(status="not_found", identification=agent.Identification(brand="Kurkure"),
                                 reason="nope", trace=[])
    monkeypatch.setattr(agent, "find_from_front", fake_find)

    sid = "testsession-sibling1"
    client.post("/live/frame", data={"session_id": sid},
                files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    body = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert body["found"] is False and body["reason"] == "not_found"


def test_live_result_reports_snap_being_looked_at(client, monkeypatch):
    async def seed():
        await client.app.state.products.store.save_product(
            Product(barcode="8901764111105", brand="Kurkure", name="Masala Munch", source="label",
                    verified=True, ingredients_text="Rice meal, corn meal",
                    nutrition=Nutrition(energy_kcal=550)))
    client.portal.call(seed)
    monkeypatch.setattr(barcodes, "read", lambda data: ["8901764111105"])

    sid = "testsession-snap1"
    assert client.get("/live/snap", params={"session_id": sid}).status_code == 404
    frame = jpeg()
    client.post("/live/frame", data={"session_id": sid}, files={"image": ("f.jpg", frame, "image/jpeg")})
    client.post("/voice/live-lookup", json={"session_id": sid})

    fed = client.get("/live/result", params={"session_id": sid}).json()
    assert fed["looking"] is False and fed["snap_at"] == fed["latest_snap_at"] is not None
    snap = client.get("/live/snap", params={"session_id": sid})
    assert snap.status_code == 200 and snap.content == frame


def test_live_follow_up_answers_from_the_running_lookup(client, monkeypatch):
    """"Did you find it?" must not re-read the camera (which by then often points at
    the floor) or start a second job: it answers from the session's lookup."""
    import asyncio

    from app import main

    monkeypatch.setattr(main, "LIVE_BUDGET", 1.0)
    monkeypatch.setattr(barcodes, "read", lambda data: [])
    reads = []

    async def fake_identify(settings, images, usage=None):
        reads.append(1)
        return agent.Identification(brand="Coca-Cola", product_name="Diet Coke")
    monkeypatch.setattr(agent, "identify", fake_identify)
    release = asyncio.Event()
    started = []

    async def fake_find(settings, products, exa, images, steps):
        started.append(1)
        await release.wait()
        product = Product(brand="Coca-Cola", name="Diet Coke", source="web", ingredients_text="Carbonated water",
                          nutrition=Nutrition(energy_kcal=0))
        await products.save(product)
        return agent.AgentResult(status="web", product=product,
                                 identification=agent.Identification(brand="Coca-Cola"), trace=[])
    monkeypatch.setattr(agent, "find_from_front", fake_find)

    sid = "testsession-followup1"
    client.post("/live/frame", data={"session_id": sid}, files={"image": ("f.jpg", jpeg(), "image/jpeg")})
    first = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert first["reason"] == "unknown" and "15 to 30 seconds" in first["answer"]

    second = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert second["reason"] == "researching" and second["answer"].startswith("Still looking")
    assert second["snap_at"] == first["snap_at"]  # still about the first frame

    client.portal.call(release.set)
    third = client.post("/voice/live-lookup", json={"session_id": sid}).json()
    assert third["found"] is True and third["reason"] == "researched"
    assert third["answer"].startswith("Found it. Coca-Cola Diet Coke.") and third["answer"].endswith("on your screen.")
    assert started == [1] and reads == [1]  # one job, one vision read


@respx.mock
def test_off_name_search_falls_back_when_classic_search_is_down():
    import asyncio

    from app.sources import off

    respx.get(off.SEARCH_API).mock(return_value=httpx.Response(503))
    respx.get(off.SEARCH_API_V2).mock(return_value=httpx.Response(200, json={"hits": [{
        "code": "54491496", "product_name": "Diet Coke", "brands": ["Coca-Cola", "Diet Coke"],
        "ingredients_text": "Carbonated water, colour (150d)", "labels": ["Vegan"], "nutriments": {},
    }]}))

    async def go():
        async with httpx.AsyncClient() as http:
            return await off.search_by_name(http, "Diet Coke")
    [p] = asyncio.run(go())
    assert p.barcode == "54491496" and p.brand == "Coca-Cola" and p.claims == ["Vegan"]
