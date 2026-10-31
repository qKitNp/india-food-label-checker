"""General label questions for the voice agent: "is aspartame safe?", "what is INS 621?".

The product tools answer about one pack; callers also ask about ingredients, additives,
claims and food rules in general. We search regulators and public-health bodies only,
and a fast model answers in two or three spoken sentences from what it found. The
model must quote the sentence it relied on, and the quote must be on the page, so a
reply can't drift beyond its source.
"""

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from urllib.parse import urlparse

import httpx
from exa_py import Exa
from pydantic import BaseModel, ValidationError

from app.config import Settings

log = logging.getLogger("live_lookup")

# Who we'll quote, and how to say their name out loud.
SOURCES = {
    "fssai.gov.in": "FSSAI, India's food regulator",
    "pib.gov.in": "the Indian government",
    "efsa.europa.eu": "EFSA, the EU's food safety authority",
    "food.ec.europa.eu": "the European Commission",
    "eur-lex.europa.eu": "EU law",
    "fda.gov": "the US FDA",
    "who.int": "the WHO",
    "fao.org": "the UN's FAO",
    "nhs.uk": "the UK's NHS",
    "food.gov.uk": "the UK Food Standards Agency",
    "cancer.gov": "the US National Cancer Institute",
    "iarc.who.int": "the WHO's cancer agency",
}
BUDGET_S = 8.0  # Bolna gives a tool 10 s
CACHE_SIZE = 200
CACHE_TTL = 24 * 3600

NO_ANSWER = ("I couldn't find a reliable answer to that from food regulators just now. "
             "The full report on your screen covers this pack's ingredients.")
OFF_TOPIC = "I can only help with food labels, ingredients and additives."

PROMPT = """A shopper in India is on a voice call about packaged food and asked:
"{question}"
{context}
Answer using ONLY the sources below. Rules:
- Two or three short sentences, plain spoken English, no lists, no markdown, under 70 words.
- Say who says it, by the name given for the source, e.g. "According to EFSA, the EU's food safety authority, ...".
- No medical advice and no telling them what to eat; say what the source says.
- If the sources don't answer the question, set answer to "".
- If the question isn't about food, ingredients, additives, nutrition or food labels or rules, set on_topic to false.
- quote: one sentence copied word for word from the source you relied on most; source_url: its URL.

Sources:
{sources}"""


class Reply(BaseModel):
    on_topic: bool = True
    answer: str = ""
    quote: str = ""
    source_url: str = ""


def _words(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def quoted(quote: str, page: str) -> bool:
    q = _words(quote)
    return len(q.split()) >= 6 and f" {q} " in f" {_words(page)} "


def source_name(url: str) -> str | None:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return next((name for dom, name in SOURCES.items() if host == dom or host.endswith("." + dom)), None)


class Asker:
    def __init__(self, settings: Settings, exa: Exa | None, http: httpx.AsyncClient) -> None:
        self.settings, self.exa, self.http = settings, exa, http
        self.cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

    async def answer(self, question: str, product: str | None = None) -> str:
        key = _words(question) + "|" + _words(product or "")
        if (hit := self.cache.get(key)) and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        try:
            said = await asyncio.wait_for(self._answer(question, product), BUDGET_S)
        except (TimeoutError, httpx.HTTPError, ValidationError, ValueError, KeyError) as e:
            log.warning("ask failed for %r: %r", question, e)
            return NO_ANSWER
        if said != NO_ANSWER:
            self.cache[key] = (time.time(), said)
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
        return said

    async def search(self, question: str) -> list[tuple[str, str]]:
        """(url, text) from trusted sources only."""
        if not self.exa:
            return []
        res = await asyncio.to_thread(
            self.exa.search, f"{question} food additive regulation", num_results=4,
            include_domains=list(SOURCES), contents={"text": {"max_characters": 3500}})
        return [(r.url, r.text) for r in res.results if r.text and source_name(r.url)]

    async def _answer(self, question: str, product: str | None) -> str:
        pages = await self.search(question)
        if not pages:
            return NO_ANSWER
        sources = "\n\n".join(f"[{source_name(u)}] {u}\n{t}" for u, t in pages)
        context = f"They are looking at: {product}.\n" if product else ""
        reply = await self._ask(PROMPT.format(question=question, context=context, sources=sources))
        if not reply.on_topic:
            return OFF_TOPIC
        page = dict(pages).get(reply.source_url, "")
        if not reply.answer.strip() or not quoted(reply.quote, page):
            return NO_ANSWER
        return " ".join(reply.answer.split())

    async def _ask(self, prompt: str) -> Reply:
        s = self.settings
        r = await self.http.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {s.deepseek_api_key}"},
            json={"model": s.voice_llm_model, "temperature": 0.1, "max_tokens": 400,
                  "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt + "\n\nReply with only a JSON object with keys "
                                "on_topic, answer, quote, source_url."}]},
            timeout=6.0)
        r.raise_for_status()
        return Reply.model_validate(json.loads(r.json()["choices"][0]["message"]["content"]))
