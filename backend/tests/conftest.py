import pytest


@pytest.fixture(autouse=True)
def no_tool_token(monkeypatch):
    # A developer's backend/.env may set TOOL_TOKEN; tests call /voice/* as the agent
    # unless they set one themselves (tests/test_limits.py).
    monkeypatch.setenv("TOOL_TOKEN", "")
