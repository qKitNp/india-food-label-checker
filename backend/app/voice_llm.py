"""The voice agent's LLM, proxied through us so the live page can show exact captions.

Bolna's web call carries audio only and publishes its transcript after hang-up.
Pointing the agent at this OpenAI-compatible endpoint (Bolna "custom LLM") means
every turn passes through here: the request holds Bolna's transcript of what the
caller said, and the streamed reply is exactly what the agent will speak. We pass
both to the page as captions and forward the request to a fast upstream model.
"""

import itertools
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import Settings

SESSION_IN_PROMPT = re.compile(r"session id for this call: ([A-Za-z0-9_-]{8,64})")
MAX_SESSIONS = 500
SESSION_TTL = 30 * 60
MAX_CAPTIONS = 60
# Fields Bolna's OpenAI SDK may send that upstreams reject or we set ourselves.
DROP_FIELDS = {"model", "service_tier", "parallel_tool_calls", "user", "metadata", "store"}
GEMINI_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai"


def _agent_lines() -> tuple[str, dict[str, str]]:
    """Lines Bolna speaks itself, without the LLM: the greeting and each tool's
    pre-call message. Read from the agent config kept in the repo."""
    path = Path(__file__).resolve().parents[2] / "bolna" / "label-check-agent.json"
    try:
        cfg = json.loads(path.read_text())["agent_config"]
        tools = cfg["tasks"][0]["tools_config"]["api_tools"]["tools"]
        return cfg.get("agent_welcome_message", ""), {
            t["name"]: t["pre_call_message"] for t in tools if t.get("pre_call_message")}
    except (OSError, KeyError, IndexError, ValueError):
        return "", {}


WELCOME, PRE_CALL = _agent_lines()


@dataclass
class Caption:
    id: int
    role: str  # "user" | "assistant"
    text: str
    final: bool
    rev: int  # bumped on every change, so the page can ask for "what changed since"
    at: float = field(default_factory=time.time)


@dataclass
class _Session:
    captions: list[Caption] = field(default_factory=list)
    user_turns: int = 0  # user messages already captioned
    touched: float = field(default_factory=time.time)


class Transcripts:
    def __init__(self) -> None:
        self.sessions: OrderedDict[str, _Session] = OrderedDict()
        self._ids = itertools.count(1)
        self._revs = itertools.count(1)

    def _session(self, sid: str) -> _Session:
        now = time.time()
        s = self.sessions.pop(sid, None) or _Session()
        while self.sessions and (len(self.sessions) >= MAX_SESSIONS
                                 or now - next(iter(self.sessions.values())).touched > SESSION_TTL):
            self.sessions.popitem(last=False)  # oldest first
        s.touched = now
        self.sessions[sid] = s
        return s

    def add(self, sid: str, role: str, text: str, final: bool = True) -> Caption:
        s = self._session(sid)
        c = Caption(next(self._ids), role, text, final, next(self._revs))
        s.captions = [*s.captions[-(MAX_CAPTIONS - 1):], c]
        return c

    def update(self, c: Caption, text: str, final: bool) -> None:
        c.text, c.final, c.rev = text, final, next(self._revs)

    def note_user_turns(self, sid: str, messages: list[dict]) -> None:
        """Caption the caller's newest message once, however often Bolna resends the history."""
        users = [m for m in messages if m.get("role") == "user"]
        s = self._session(sid)
        if len(users) > s.user_turns:
            s.user_turns = len(users)
            text = _text(users[-1].get("content"))
            if text.strip():
                self.add(sid, "user", text.strip())

    def since(self, sid: str, rev: int) -> list[Caption]:
        s = self.sessions.get(sid)
        return [c for c in s.captions if c.rev > rev] if s else []


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def session_of(body: dict) -> str | None:
    """The live session this turn belongs to, from the system prompt. Only conversation
    turns carry tools; Bolna's own end-of-call checks are proxied without captions."""
    if not body.get("tools"):
        return None
    for m in body.get("messages", []):
        if m.get("role") == "system" and (hit := SESSION_IN_PROMPT.search(_text(m.get("content")))):
            return hit.group(1)
    return None


def _upstreams(settings: Settings) -> list[tuple[str, str, str, dict]]:
    """(base url, key, model, extra fields) in the order to try."""
    out = []
    if settings.deepseek_api_key:
        out.append(("https://api.deepseek.com", settings.deepseek_api_key, settings.voice_llm_model,
                    {"thinking": {"type": "disabled"}}))
    if settings.gemini_api_key:
        out.append((GEMINI_OPENAI, settings.gemini_api_key, settings.voice_llm_fallback,
                    {"reasoning_effort": "minimal"}))
    return out


def _for_gemini(messages: list[dict]) -> list[dict]:
    # Gemini 3 wants each earlier tool call's thought signature back; turns produced by
    # another model have none, and Google documents this value for that case.
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m = {**m, "tool_calls": [
                {**tc, "extra_content": tc.get("extra_content")
                 or {"google": {"thought_signature": "skip_thought_signature_validator"}}}
                for tc in m["tool_calls"]]}
        out.append(m)
    return out


class UpstreamError(Exception):
    pass


async def open_stream(client: httpx.AsyncClient, settings: Settings, body: dict) -> httpx.Response:
    """Start the completion on the first upstream that answers 200."""
    last = "no voice LLM configured"
    for base, key, model, extra in _upstreams(settings):
        payload = {k: v for k, v in body.items() if k not in DROP_FIELDS} | extra | {"model": model}
        if base == GEMINI_OPENAI:
            payload["messages"] = _for_gemini(payload.get("messages", []))
        req = client.build_request("POST", f"{base}/chat/completions", json=payload,
                                   headers={"Authorization": f"Bearer {key}"},
                                   timeout=httpx.Timeout(settings.voice_llm_timeout, connect=3.0))
        try:
            r = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            last = f"{base}: {e!r}"
            continue
        if r.status_code == 200:
            return r
        last = f"{base}: {r.status_code} {(await r.aread())[:200]!r}"
        await r.aclose()
    raise UpstreamError(last)


async def relay(r: httpx.Response, transcripts: Transcripts, sid: str | None) -> AsyncIterator[bytes]:
    """Pass the upstream SSE stream through unchanged, captioning the reply as it grows."""
    caption: Caption | None = None
    text = ""
    tools: list[str] = []
    try:
        async for line in r.aiter_lines():
            yield (line + "\n").encode()
            if sid is None or not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            try:
                choices = json.loads(line[5:]).get("choices") or []
            except ValueError:
                continue
            delta = (choices[0].get("delta") or {}) if choices else {}
            if piece := delta.get("content"):
                text += piece
                if caption is None:
                    caption = transcripts.add(sid, "assistant", text, final=False)
                else:
                    transcripts.update(caption, text, final=False)
            for tc in delta.get("tool_calls") or []:
                name = (tc.get("function") or {}).get("name")
                if name and name not in tools:
                    tools.append(name)
                    # Caption it now: Bolna speaks it as soon as it runs the tool, and
                    # may drop this stream before we'd reach the end.
                    tool_line(transcripts, sid, name)
    finally:
        # Caption first: if Bolna hangs up on the stream (it often does once it has a
        # tool call), this runs inside a cancelled task and the first await raises.
        if sid is not None:
            if caption is not None:
                transcripts.update(caption, text.strip(), final=True)
        await r.aclose()


def tool_line(transcripts: Transcripts, sid: str, name: str) -> None:
    """Bolna speaks a tool's pre-call message itself while the tool runs."""
    if line := PRE_CALL.get(name):
        transcripts.add(sid, "assistant", line)
