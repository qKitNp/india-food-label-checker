"""Live captions for the voice call: what the caller said and what the agent said.

Bolna's web call carries audio only and publishes the transcript after the call
ends, so the page records each utterance itself and we transcribe it here with a
fast Gemini model. Captions are for reassurance ("it heard me"), not for lookups.
"""

import base64

import httpx

from app.config import Settings

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_BYTES = 1_500_000  # ~15 s of opus; the page cuts clips well before this
PROMPT = (
    "Transcribe the speech in this audio exactly. It is from a call about Indian packaged food "
    "between a shopper and an assistant called Fineprint (brands like Kurkure, Maggi, Parle-G, "
    "Frooti, Haldiram's). Write Hindi or Hinglish in Latin "
    "script. Reply with only the words spoken. If there is no clear speech, reply with nothing."
)


class CaptionError(Exception):
    pass


async def transcribe(settings: Settings, audio: bytes, media_type: str, client: httpx.AsyncClient) -> str:
    if not settings.gemini_api_key:
        raise CaptionError("captions need GEMINI_API_KEY")
    body = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": media_type, "data": base64.b64encode(audio).decode()}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 200,
                             "thinkingConfig": {"thinkingLevel": "minimal"}},
    }
    try:
        r = await client.post(URL.format(model=settings.caption_model), json=body,
                              params={"key": settings.gemini_api_key}, timeout=settings.caption_timeout)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"].get("parts") or []
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise CaptionError(str(e)) from e
    text = " ".join(p.get("text", "") for p in parts).strip()
    # Models sometimes describe silence instead of returning nothing.
    if text.strip("[]().").lower() in {"", "no speech", "silence", "nothing", "inaudible"}:
        return ""
    return text
