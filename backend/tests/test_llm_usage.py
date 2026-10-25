"""Usage (tokens, DeepSeek cache hits) and cost logging for LLM calls."""

import asyncio
from types import SimpleNamespace

from app import llm
from app.agent import AgentResult, Identification
from app.config import Settings
from app.jobs import IdentifyResponse, Job


def _settings():
    return Settings(llm_model="deepseek/deepseek-flash", llm_fallbacks=[], llm_timeout=5)


def _no_cost(monkeypatch):
    def boom(**kwargs):
        raise Exception("no prices")
    monkeypatch.setattr(llm.litellm, "completion_cost", boom)


def test_usage_record_reads_deepseek_cache_fields(monkeypatch):
    _no_cost(monkeypatch)
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=50, total_tokens=1050,
                            prompt_tokens_details=None,
                            prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200)
    rec = llm.usage_record(SimpleNamespace(model="deepseek/deepseek-flash", usage=usage))
    assert rec["model"] == "deepseek/deepseek-flash"
    assert rec["prompt_tokens"] == 1000 and rec["completion_tokens"] == 50
    assert rec["cached_tokens"] == 800 and rec["cache_miss_tokens"] == 200
    assert rec["cost_usd"] is None


def test_usage_record_reads_litellm_normalised_shape(monkeypatch):
    _no_cost(monkeypatch)
    usage = SimpleNamespace(prompt_tokens=500, completion_tokens=10, total_tokens=510,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=100))
    rec = llm.usage_record(SimpleNamespace(model="deepseek/deepseek-flash", usage=usage))
    assert rec["cached_tokens"] == 100
    assert rec["cache_miss_tokens"] == 400  # derived when the provider omits it


def test_usage_record_without_usage(monkeypatch):
    _no_cost(monkeypatch)
    rec = llm.usage_record(SimpleNamespace(model="x", usage=None))
    assert rec["prompt_tokens"] == 0 and rec["cached_tokens"] == 0 and rec["cost_usd"] is None


def test_usage_totals():
    records = [
        {"prompt_tokens": 1000, "completion_tokens": 50, "cached_tokens": 800, "cost_usd": 0.001},
        {"prompt_tokens": 500, "completion_tokens": 10, "cached_tokens": 0, "cost_usd": None},
    ]
    totals = llm.usage_totals(records)
    assert totals["calls"] == 2
    assert totals["prompt_tokens"] == 1500 and totals["cached_tokens"] == 800
    assert totals["hit_rate"] == 800 / 1500
    assert totals["cost_usd"] == 0.001 and totals["cost_known_for_calls"] == 1


def test_usage_totals_empty():
    totals = llm.usage_totals([])
    assert totals["calls"] == 0 and totals["hit_rate"] is None and totals["cost_usd"] is None


def test_complete_json_collects_usage_and_cost(monkeypatch):
    from pydantic import BaseModel

    class S(BaseModel):
        name: str

    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=50, total_tokens=1050,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
                            prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200)
    response = SimpleNamespace(
        model="deepseek/deepseek-flash",
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"name": "x"}'))],
        usage=usage,
    )

    async def fake_acompletion(**kwargs):
        return response

    monkeypatch.setattr(llm.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **kwargs: 0.002)

    got: list[dict] = []
    out = asyncio.run(llm.complete_json(_settings(), S, "hi", usage=got))
    assert out.name == "x" and len(got) == 1
    assert got[0]["cached_tokens"] == 800 and got[0]["cost_usd"] == 0.002
    assert llm.usage_totals(got)["hit_rate"] == 0.8


def test_job_log_record_includes_llm_totals():
    usage = [
        {"model": "deepseek/deepseek-flash", "prompt_tokens": 1000, "completion_tokens": 50,
         "total_tokens": 1050, "cached_tokens": 800, "cache_miss_tokens": 200, "cost_usd": 0.001},
    ]
    result = AgentResult(status="web", identification=Identification(brand="K"), usage=usage)
    job = Job(id="x", status="done", result=IdentifyResponse(result=result, verdict=None))
    rec = job.log_record()
    assert rec["usage"] == usage
    assert rec["llm"]["calls"] == 1 and rec["llm"]["hit_rate"] == 0.8
    assert rec["llm"]["cost_usd"] == 0.001


def _record(prompt=2000, completion=20, cached=100, cost=0.001):
    return {"model": "deepseek/deepseek-flash", "prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion, "cached_tokens": cached,
            "cache_miss_tokens": prompt - cached, "cost_usd": cost}


def test_label_extract_collects_usage(monkeypatch):
    from app.sources import label

    async def fake(settings, schema, prompt, images=None, usage=None):
        if usage is not None:
            usage.append(_record())
        return schema(name="x", ingredients_text="Water, salt")

    monkeypatch.setattr(llm, "complete_json", fake)
    res = asyncio.run(label.extract(_settings(), [(b"img", "image/jpeg")], barcode="1"))
    assert res.product.name == "x" and len(res.usage) == 1
    assert llm.usage_totals(res.usage)["hit_rate"] == 0.05


def test_counterpart_find_collects_usage(monkeypatch):
    from app.sources import counterpart
    from app.models import Product

    page = "Ingredients: potato, salt and spices. Made in the UK."

    async def fake(settings, schema, prompt, images=None, usage=None):
        if usage is not None:
            usage.append(_record())
        return schema(same_product=True, country="UK", product_name="Kurkure Masala Munch",
                      ingredients_quote="potato, salt and spices")

    monkeypatch.setattr(llm, "complete_json", fake)
    exa = SimpleNamespace(search=lambda *a, **k: SimpleNamespace(
        results=[SimpleNamespace(url="https://kurkure.co.uk/x", text=page)]))
    product = Product(brand="Kurkure", name="Masala Munch", source="web")
    usage: list[dict] = []
    found = asyncio.run(counterpart.find(exa, _settings(), product, "EU", usage=usage))
    assert found is not None and found.country == "UK"
    assert len(usage) == 1 and llm.usage_totals(usage)["calls"] == 1


def test_research_substance_collects_usage(monkeypatch):
    from app import research
    from app.research import Fact, Finding

    page = "Guar gum is authorised for use in food under EU regulation."

    async def fake(settings, schema, prompt, images=None, usage=None):
        if usage is not None:
            usage.append(_record())
        if schema is Finding:
            return Finding(status="allowed", quote="Guar gum is authorised for use in food")
        return Fact(fact="Guar gum is allowed in the European Union.")

    monkeypatch.setattr(llm, "complete_json", fake)
    exa = SimpleNamespace(search=lambda *a, **k: SimpleNamespace(
        results=[SimpleNamespace(url="https://eur-lex.europa.eu/x", text=page)]))
    sub = {"id": "guar", "name": "Guar gum", "codes": ["E412"]}
    usage: list[dict] = []
    regions = asyncio.run(research.research_substance(exa, _settings(), sub, usage=usage))
    assert regions["EU"]["status"] == "allowed"
    assert llm.usage_totals(usage)["calls"] == len(usage) >= 3  # one Finding per region
