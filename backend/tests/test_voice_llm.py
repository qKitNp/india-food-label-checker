import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import voice_llm
from app.config import get_settings
from app.main import app

SID = "sess-abc12345"
KEY = {"Authorization": "Bearer proxy-key"}
TOOLS = [{"type": "function", "function": {"name": "lookup_visible_product", "parameters": {"type": "object"}}}]


def turn(*user_lines, tools=TOOLS, stream=True):
    msgs = [{"role": "system", "content": f"You are Fineprint. Live camera session id for this call: {SID}."}]
    for u in user_lines:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": "ok"}]
    msgs.pop()  # the newest user line is unanswered
    body = {"model": "fineprint-voice", "stream": stream, "messages": msgs, "parallel_tool_calls": False}
    if tools:
        body["tools"] = tools
    return body


def sse(*deltas):
    lines = [f"data: {json.dumps({'choices': [{'index': 0, 'delta': d}]})}\n\n" for d in deltas]
    return "".join(lines) + "data: [DONE]\n\n"


@pytest.fixture
def client(monkeypatch):
    for k, v in {"APPWRITE_PROJECT_ID": "", "EMBEDDINGS_ENABLED": "false", "EXA_API_KEY": "",
                 "BOLNA_API_KEY": "", "LLM_PROXY_KEY": "proxy-key", "DEEPSEEK_API_KEY": "ds",
                 "GEMINI_API_KEY": "gm"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def captions(client):
    return [(c["role"], c["text"], c["final"]) for c in
            client.get("/live/captions", params={"session_id": SID}).json()["captions"]]


def test_proxy_needs_the_key(client):
    assert client.post("/llm/v1/chat/completions", json=turn("hi")).status_code == 401
    assert client.get("/llm/v1/models", headers=KEY).json()["data"][0]["id"] == "fineprint-voice"


@respx.mock
def test_proxy_streams_and_captions_both_sides(client):
    route = respx.post("https://api.deepseek.com/chat/completions").mock(return_value=httpx.Response(
        200, text=sse({"role": "assistant"}, {"content": "Maggi 2-Minute "}, {"content": "Noodles."}),
        headers={"content-type": "text/event-stream"}))
    r = client.post("/llm/v1/chat/completions", json=turn("tell me about maggi"), headers=KEY)
    assert r.status_code == 200 and "Noodles." in r.text and r.text.rstrip().endswith("[DONE]")
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "deepseek-flash" and sent["thinking"] == {"type": "disabled"}
    assert "parallel_tool_calls" not in sent
    assert captions(client) == [("user", "tell me about maggi", True),
                                ("assistant", "Maggi 2-Minute Noodles.", True)]


@respx.mock
def test_resent_history_is_captioned_once_and_tool_lines_added(client):
    respx.post("https://api.deepseek.com/chat/completions").mock(return_value=httpx.Response(
        200, text=sse({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "lookup_visible_product",
                                                                              "arguments": "{}"}}]}),
        headers={"content-type": "text/event-stream"}))
    client.post("/llm/v1/chat/completions", json=turn("what is this"), headers=KEY)
    client.post("/llm/v1/chat/completions", json=turn("what is this"), headers=KEY)  # tool-result turn
    users = [c for c in captions(client) if c[0] == "user"]
    assert users == [("user", "what is this", True)]
    assert ("assistant", voice_llm.PRE_CALL["lookup_visible_product"], True) in captions(client)


@respx.mock
def test_falls_back_to_gemini_with_a_signature(client):
    respx.post("https://api.deepseek.com/chat/completions").mock(return_value=httpx.Response(503))
    gem = respx.post(f"{voice_llm.GEMINI_OPENAI}/chat/completions").mock(return_value=httpx.Response(
        200, text=sse({"content": "Hello."}), headers={"content-type": "text/event-stream"}))
    body = turn("hi")
    body["messages"].insert(2, {"role": "assistant", "tool_calls": [
        {"id": "c0", "type": "function", "function": {"name": "lookup_visible_product", "arguments": "{}"}}]})
    assert client.post("/llm/v1/chat/completions", json=body, headers=KEY).status_code == 200
    sent = json.loads(gem.calls[0].request.content)
    assert sent["model"] == "gemini-3.5-flash-lite"
    assert sent["messages"][2]["tool_calls"][0]["extra_content"]["google"]["thought_signature"]


@respx.mock
def test_bolnas_own_checks_are_not_captioned(client):
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "No"}}]}))
    r = client.post("/llm/v1/chat/completions", json=turn("bye", tools=None, stream=False), headers=KEY)
    assert r.json()["choices"][0]["message"]["content"] == "No"
    assert captions(client) == []
