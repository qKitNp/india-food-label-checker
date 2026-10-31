"""Register our voice LLM proxy (/llm/v1 on the API) as a Bolna custom model.

    set -a && source backend/.env && set +a && backend/.venv/bin/python bolna/register_llm.py

Bolna calls GET {url}/models with the key to check it, so deploy the API with
LLM_PROXY_KEY set first. Run once; label-check-agent.json then names the model.
"""

import json
import os
import sys
import urllib.error
import urllib.request

NAME = "fineprint-voice"
URL = "https://food-label-api.tinkerers.space/llm/v1"
BOLNA = "https://api.bolna.ai/user/model/custom"
HEADERS = {"Authorization": f"Bearer {os.environ['BOLNA_API_KEY']}", "Content-Type": "application/json"}


def call(method: str, url: str, body: dict | None = None):
    req = urllib.request.Request(url, method=method, headers=HEADERS,
                                 data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url}: {e.code} {e.read()[:300]!r}")


existing = call("GET", BOLNA) or []
if any(NAME in json.dumps(m) for m in (existing if isinstance(existing, list) else [existing])):
    print(f"{NAME} already registered")
else:
    print(call("POST", BOLNA, {"custom_model_name": NAME, "custom_model_url": URL,
                               "custom_model_key": os.environ["LLM_PROXY_KEY"], "custom_model_family": "openai"}))
