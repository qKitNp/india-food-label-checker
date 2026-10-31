import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

TOOL = {"Authorization": "Bearer tool-secret"}


@pytest.fixture
def client(monkeypatch):
    for k, v in {"APPWRITE_PROJECT_ID": "", "EMBEDDINGS_ENABLED": "false", "EXA_API_KEY": "", "BOLNA_API_KEY": "",
                 "GEMINI_API_KEY": "", "TOOL_TOKEN": "tool-secret"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_tool_endpoints_need_the_agents_token(client):
    assert client.post("/voice/lookup", json={"product_name": "maggi"}).status_code == 401
    assert client.post("/voice/ask", json={"question": "is msg safe?"}).status_code == 401
    assert client.post("/voice/live-wait", json={"session_id": "sess-12345678"}).status_code == 401
    assert client.post("/voice/lookup", json={"product_name": "maggi"}, headers=TOOL).status_code == 200


def test_starting_calls_is_limited_per_visitor(client):
    me, other = {"cf-connecting-ip": "203.0.113.7"}, {"cf-connecting-ip": "198.51.100.9"}
    codes = [client.post("/live/session", json={"user_data": {}}, headers=me).status_code for _ in range(7)]
    assert codes[:6] == [503] * 6  # passed the limiter (Bolna isn't configured in tests)
    r = client.post("/live/session", json={"user_data": {}}, headers=me)
    assert codes[6] == 429 and r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert client.post("/live/session", json={"user_data": {}}, headers=other).status_code == 503


def test_page_live_lookups_are_limited_but_the_agents_are_not(client):
    page = {"cf-connecting-ip": "203.0.113.8"}
    body = {"session_id": "sess-12345678"}
    for _ in range(40):
        assert client.post("/voice/live-lookup", json=body, headers=page).status_code == 200
    assert client.post("/voice/live-lookup", json=body, headers=page).status_code == 429
    assert client.post("/voice/live-lookup", json=body, headers={**page, **TOOL}).status_code == 200
