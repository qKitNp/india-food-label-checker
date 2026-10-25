"""Push label-check-agent.json to the live Bolna agent and check it stuck.

    BOLNA_API_KEY=... python3 bolna/push.py [agent_id]

Bolna keeps each tool's request template twice: under the tool's `value` and in
`tools_params`. If `tools_params[*].param` ends up null, Bolna posts an empty
body and every tool call fails (FastAPI answers 422), so we fill both and
verify after saving.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

AGENT_ID = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "BOLNA_AGENT_ID", "d09afdca-5794-4127-b2b5-7cf243ab361f")
KEY = os.environ["BOLNA_API_KEY"]
URL = f"https://api.bolna.ai/v2/agent/{AGENT_ID}"


def call(method: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(URL, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


config = json.loads((Path(__file__).parent / "label-check-agent.json").read_text())
api_tools = config["agent_config"]["tasks"][0]["tools_config"]["api_tools"]
for tool in api_tools["tools"]:
    api_tools["tools_params"][tool["name"]] = {**api_tools["tools_params"].get(tool["name"], {}), **tool["value"]}

print(call("PUT", config))

saved = call("GET")["tasks"][0]["tools_config"]["api_tools"]["tools_params"]
for name, params in saved.items():
    print(f"{name}: param={params.get('param')}")
    if not params.get("param"):
        sys.exit(f"{name} has no request template; tool calls will send an empty body")
