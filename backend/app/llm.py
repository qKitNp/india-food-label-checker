"""All model calls go through LiteLLM, so the model is a config change (LLM_MODEL /
LLM_FALLBACKS), e.g. "deepseek/deepseek-flash" or "gemini/gemini-3.8-flash"."""

import base64
import io
import json
import logging
import os
import re
from typing import TypeVar

import litellm
from PIL import Image, ImageOps
from pydantic import BaseModel, ValidationError

from app.config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

litellm.drop_params = True  # ignore params a provider doesn't support instead of erroring

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

# Phone photos are several MB; sending them as-is made DeepSeek calls 13-22 s instead of ~5 s.
# 1600 px keeps small ingredient text readable.
MAX_IMAGE_SIDE = 1600


class LLMError(Exception):
    pass


class InvalidImage(ValueError):
    pass


def export_keys(settings: Settings) -> None:
    """LiteLLM reads provider keys from the environment; .env is only loaded into Settings."""
    for var, value in (("DEEPSEEK_API_KEY", settings.deepseek_api_key),
                       ("GEMINI_API_KEY", settings.gemini_api_key)):
        if value:
            os.environ.setdefault(var, value)


# Uploads: the app always sends JPEG; PNG and WebP are accepted for direct API use. Limiting
# formats keeps Pillow's rarely used parsers (TIFF, PSD, ...) away from untrusted input.
IMAGE_FORMATS = ("JPEG", "PNG", "WEBP")
# A decoded image costs ~3 bytes per pixel; 60 MP covers phone cameras (~180 MB decoded).
Image.MAX_IMAGE_PIXELS = 60_000_000


def _open(data: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(data), formats=IMAGE_FORMATS)
    except Image.DecompressionBombError as e:
        raise InvalidImage("Image is too large (over 60 megapixels)") from e
    except OSError as e:  # UnidentifiedImageError is an OSError
        raise InvalidImage("Not a JPEG, PNG or WebP image") from e


def check_image(data: bytes) -> None:
    """Cheap check before doing any work: a supported format, within the pixel limit, not corrupt."""
    image = _open(data)
    if image.width * image.height > Image.MAX_IMAGE_PIXELS:
        raise InvalidImage("Image is too large (over 60 megapixels)")
    try:
        image.verify()
    except Exception as e:
        raise InvalidImage("The image file is damaged") from e


def shrink(data: bytes) -> bytes:
    """Upright, downscaled JPEG."""
    try:
        image = ImageOps.exif_transpose(_open(data)).convert("RGB")
    except (OSError, Image.DecompressionBombError) as e:
        raise InvalidImage("Not a readable image") from e
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def thumbnail(data: bytes, width: int) -> bytes:
    """A small WebP for product cards and headers."""
    image = ImageOps.exif_transpose(_open(data)).convert("RGB")
    image.thumbnail((width, width * 2))
    out = io.BytesIO()
    image.save(out, "WEBP", quality=78, method=4)
    return out.getvalue()


def _content(prompt: str, images: list[tuple[bytes, str]]) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for data, _mime in images:
        url = f"data:image/jpeg;base64,{base64.b64encode(shrink(data)).decode()}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


async def complete_json(settings: Settings, schema: type[T], prompt: str,
                        images: list[tuple[bytes, str]] | None = None,
                        usage: list[dict] | None = None) -> T:
    """Ask for JSON matching `schema`. Validates the reply and retries once on bad JSON.

    Pass `usage` (a list) to collect one record per API call with token counts,
    DeepSeek cache hits and cost; see `usage_record` / `usage_totals`."""
    prompt = (f"{prompt}\n\nReply with only a JSON object matching this JSON schema:\n"
              f"{json.dumps(schema.model_json_schema())}")
    messages = [{"role": "user", "content": _content(prompt, images or [])}]
    last_error: Exception | None = None
    for _ in range(2):
        try:
            response = await litellm.acompletion(
                model=settings.llm_model,
                fallbacks=settings.llm_fallbacks,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=settings.llm_timeout,
            )
        except Exception as e:  # LiteLLM raises provider-specific exception types
            raise LLMError(f"{settings.llm_model} failed: {e}") from e
        try:
            record = usage_record(response)
        except Exception as e:  # observability must never break a scan
            log.warning("LLM usage record failed: %s", e)
        else:
            log.debug("llm usage %s", json.dumps(record, ensure_ascii=False))
            if usage is not None:
                usage.append(record)
        text = _FENCE.sub("", (response.choices[0].message.content or "").strip())
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            last_error = e
    raise LLMError(f"Model returned invalid JSON twice: {last_error}")


def usage_record(response) -> dict:
    """One JSON-serialisable record per LLM call: token counts, cache hits, cost.

    DeepSeek reports `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` on
    `usage`; LiteLLM normalises the hits to `usage.prompt_tokens_details.cached_tokens`.
    `cost_usd` comes from LiteLLM's price map when the model is known, else None."""
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    total_tokens = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None)
    if cached is None:  # raw DeepSeek field, or LiteLLM's private attr
        cached = getattr(usage, "_cache_read_input_tokens", None)
    if cached is None:
        cached = getattr(usage, "prompt_cache_hit_tokens", None)
    cached = cached or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    if miss is None and prompt_tokens:
        miss = max(prompt_tokens - cached, 0)
    cost = None
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception as e:  # unknown model, price map unreachable, ...
        log.debug("LLM cost lookup failed: %s", e)
    return {
        "model": getattr(response, "model", None),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached,
        "cache_miss_tokens": miss,
        "cost_usd": cost,
    }


def usage_totals(records: list[dict]) -> dict:
    """Sums across calls, with the overall cache hit rate. Cost is None unless
    LiteLLM knew the price for at least one call."""
    prompt_tokens = sum(r.get("prompt_tokens") or 0 for r in records)
    costs = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    return {
        "calls": len(records),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in records),
        "cached_tokens": sum(r.get("cached_tokens") or 0 for r in records),
        "hit_rate": (sum(r.get("cached_tokens") or 0 for r in records) / prompt_tokens)
        if prompt_tokens else None,
        "cost_usd": sum(costs) if costs else None,
        "cost_known_for_calls": len(costs),
    }
