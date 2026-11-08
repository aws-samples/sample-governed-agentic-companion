"""Tests for the bridge's runtime client: the GAC_AGENTCORE_ENDPOINT override (private network
mode) and the private-mode connection-error message. No AWS, no network.

The override must only ever send the bearer token to an AWS-owned AgentCore data-plane host for
the configured Region, and a rejected value must fail BEFORE any token is fetched.
"""
import sys
import types
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from frontdoor import runtime_client as rc  # noqa: E402

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-abc123"
ENC = urllib.parse.quote(ARN, safe="")
VPCE = "vpce-0123456789abcdef0-abcd1234.bedrock-agentcore.us-east-1.vpce.amazonaws.com"


class _SpyTokens:
    def __init__(self):
        self.calls = 0

    def get_token(self):
        self.calls += 1
        return "test-token"

    def invalidate(self):
        pass


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GAC_RUNTIME_ARN", ARN)
    monkeypatch.setenv("GAC_RUNTIME_REGION", "us-east-1")
    monkeypatch.delenv("GAC_AGENTCORE_ENDPOINT", raising=False)
    return monkeypatch


def _client(posted):
    def _post(url, headers, data):
        posted.append(url)
        return 200, '{"response": "ok"}'
    tokens = _SpyTokens()
    return rc.CompanionRuntimeClient(tokens, _post=_post), tokens


def test_default_url_unchanged_when_override_unset(env):
    posted = []
    client, tokens = _client(posted)
    assert client.invoke("hi") == {"response": "ok"}
    assert posted == [f"https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{ENC}/invocations"]
    assert tokens.calls == 1


@pytest.mark.parametrize("override, host", [
    ("https://bedrock-agentcore.us-east-1.amazonaws.com", "bedrock-agentcore.us-east-1.amazonaws.com"),
    ("https://bedrock-agentcore.us-east-1.amazonaws.com/", "bedrock-agentcore.us-east-1.amazonaws.com"),
    ("https://bedrock-agentcore.us-east-1.amazonaws.com:443", "bedrock-agentcore.us-east-1.amazonaws.com"),
    (f"https://{VPCE}", VPCE),
    ("https://vpce-0123456789abcdef0-abcd1234-us-east-1a.bedrock-agentcore.us-east-1.vpce.amazonaws.com",
     "vpce-0123456789abcdef0-abcd1234-us-east-1a.bedrock-agentcore.us-east-1.vpce.amazonaws.com"),
])
def test_override_accepted(env, override, host):
    env.setenv("GAC_AGENTCORE_ENDPOINT", override)
    posted = []
    client, _ = _client(posted)
    client.invoke("hi")
    assert posted == [f"https://{host}/runtimes/{ENC}/invocations"]


@pytest.mark.parametrize("override", [
    "http://bedrock-agentcore.us-east-1.amazonaws.com",
    "https://abc123.execute-api.us-east-1.amazonaws.com",
    "https://bucket.s3-website-us-east-1.amazonaws.com",
    "https://x.elb.amazonaws.com",
    "https://bedrock-agentcore.us-west-2.amazonaws.com",
    "https://BEDROCK-AGENTCORE.us-east-1.amazonaws.com",
    "https://bedrock-agentcore.us-east-1.amazonaws.com.",
    "https://bedrock-agentcore.us-east-1.amazonaws.com.evil.example",
    "https://user:pw@bedrock-agentcore.us-east-1.amazonaws.com",  # pragma: allowlist secret
    "https://bedrock-agentcore.us-east-1.amazonaws.com:8443",
    "https://bedrock-agentcore.us-east-1.amazonaws.com:notaport",
    "https://bedrock-agentcore.us-east-1.amazonaws.com/x",
    "https://bedrock-agentcore.us-east-1.amazonaws.com?q=1",
    "https://bedrock-agentcore.us-east-1.amazonaws.com#f",
    "https://10.0.0.5",
    f"https://{VPCE.replace('us-east-1.vpce', 'us-west-2.vpce')}",
])
def test_override_rejected_before_token(env, override):
    env.setenv("GAC_AGENTCORE_ENDPOINT", override)
    posted = []
    client, tokens = _client(posted)
    with pytest.raises(rc.RuntimeConfigError) as exc:
        client.invoke("hi")
    assert tokens.calls == 0 and posted == []
    assert override not in str(exc.value)  # the rejected value is never echoed
    assert "pw" not in str(exc.value)


def test_malformed_region_rejected(env):
    env.setenv("GAC_RUNTIME_REGION", "us-east-1.*")
    env.setenv("GAC_AGENTCORE_ENDPOINT", "https://bedrock-agentcore.us-east-1.amazonaws.com")
    posted = []
    client, tokens = _client(posted)
    with pytest.raises(rc.RuntimeConfigError):
        client.invoke("hi")
    assert tokens.calls == 0 and posted == []


def _auto_approve_values(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "autoApprove":
                yield v
            else:
                yield from _auto_approve_values(v)
    elif isinstance(node, list):
        for v in node:
            yield from _auto_approve_values(v)


def test_auto_approve_empty_in_every_sample():
    # Static test 12: every autoApprove entry in the shipped MCP samples is an empty list, so
    # each knowledge tool call needs the user's confirmation in the IDE.
    import json
    samples = ["frontdoor/mcp.example.json", "onboarding/companion-kiro-mcp.example.json",
               "onboarding/companion-kiro-gateway.example.json"]
    values = []
    for rel in samples:
        values.extend(_auto_approve_values(json.loads((ROOT / rel).read_text(encoding="utf-8"))))
    # four in frontdoor/mcp.example.json (options A and B, the Kiro gac-gateway and gac-bridge
    # pair), one in each onboarding sample
    assert len(values) == 6
    assert all(v == [] for v in values)


def test_connection_error_maps_to_private_mode_message(env, monkeypatch, caplog):
    class _ConnErr(Exception):
        pass

    class _Timeout(Exception):
        pass

    def _boom(*args, **kwargs):
        raise _ConnErr("connect failed")

    fake = types.SimpleNamespace(ConnectionError=_ConnErr, Timeout=_Timeout, post=_boom)
    monkeypatch.setitem(sys.modules, "requests", fake)
    client = rc.CompanionRuntimeClient(_SpyTokens())
    with caplog.at_level("WARNING", logger="companion.frontdoor.runtime"):
        with pytest.raises(RuntimeError, match="in private mode, run from a network that reaches"):
            client.invoke("hi")
    assert "test-token" not in caplog.text
