import asyncio
import json

import httpx
import pytest
import respx

from app import ask
from app.config import Settings

PAGE = ("Aspartame is one of the most studied food additives. FDA scientists do not have safety concerns "
        "when aspartame is used under the approved conditions.")


def deepseek(reply: dict):
    return respx.post("https://api.deepseek.com/chat/completions").mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(reply)}}]}))


@pytest.fixture
def asker():
    async def pages(question):
        return [("https://www.fda.gov/food/aspartame", PAGE)]
    a = ask.Asker(Settings(deepseek_api_key="k"), None, httpx.AsyncClient())
    a.search = pages
    return a


@respx.mock
def test_answers_from_a_quoted_trusted_source(asker):
    deepseek({"on_topic": True, "answer": "According to the US FDA, aspartame is safe at approved levels.",
              "quote": "FDA scientists do not have safety concerns when aspartame is used under the approved conditions.",
              "source_url": "https://www.fda.gov/food/aspartame"})
    assert (asyncio.run(asker.answer("Is aspartame safe?"))).startswith("According to the US FDA")


@respx.mock
def test_refuses_an_answer_whose_quote_is_not_on_the_page(asker):
    deepseek({"on_topic": True, "answer": "Aspartame causes cancer.", "quote": "aspartame causes cancer in every study we ran",
              "source_url": "https://www.fda.gov/food/aspartame"})
    assert asyncio.run(asker.answer("Is aspartame safe?")) == ask.NO_ANSWER


@respx.mock
def test_off_topic(asker):
    deepseek({"on_topic": False, "answer": "", "quote": "", "source_url": ""})
    assert asyncio.run(asker.answer("Who won the cricket world cup?")) == ask.OFF_TOPIC


def test_only_trusted_hosts_get_a_name():
    assert ask.source_name("https://www.efsa.europa.eu/en/topics/aspartame") == "EFSA, the EU's food safety authority"
    assert ask.source_name("https://randomblog.com/aspartame") is None
