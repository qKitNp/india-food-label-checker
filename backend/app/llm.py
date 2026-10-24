"""All model calls go through LiteLLM, so the model is a config change (LLM_MODEL /
LLM_FALLBACKS), e.g. "deepseek/deepseek-flash" or "gemini/gemini-3.8-flash"."""

import base64
import io
import json
import os
import re
from typing import TypeVar

import litellm
from PIL import Image, ImageOps
from pydantic import BaseModel, ValidationError

from app.config import Settings

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


def shrink(data: bytes) -> bytes:
    """Upright, downscaled JPEG."""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except (OSError, Image.DecompressionBombError) as e:  # UnidentifiedImageError is an OSError
        raise InvalidImage("Not a readable image") from e
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def _content(prompt: str, images: list[tuple[bytes, str]]) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for data, _mime in images:
        url = f"data:image/jpeg;base64,{base64.b64encode(shrink(data)).decode()}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


async def complete_json(settings: Settings, schema: type[T], prompt: str,
                        images: list[tuple[bytes, str]] | None = None) -> T:
    """Ask for JSON matching `schema`. Validates the reply and retries once on bad JSON."""
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
        text = _FENCE.sub("", (response.choices[0].message.content or "").strip())
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            last_error = e
    raise LLMError(f"Model returned invalid JSON twice: {last_error}")
