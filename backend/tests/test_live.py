import io

import pytest
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
    assert body["found"] is False and body["reason"] == "unknown"
    assert "barcode" in body["answer"].lower() and body["job_id"]


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
