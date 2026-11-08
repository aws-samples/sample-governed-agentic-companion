"""Probe anonymous mode (design D7.4 M6 option a; AC 32). Stubbed HTTP, no AWS, no network.

{"anonymous": true} exactly runs dns, initialize, notifications/initialized and tools/list
against the gateway with NO Authorization header, never a tools/call or the runtime, and returns
the step statuses (ok false on 401/403). Every other use of the key is today's invalid payload.
"""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "frontdoor", ROOT / "tests"):
    sys.path.insert(0, str(p))

import probe_fetch  # noqa: E402
from test_private_access_probe import (  # noqa: E402,F401
    GW_HOST, TOKEN, _Ctx, _install, _load, _ok_answer, _resp, no_real_network)

INVALID = {"ok": False, "error": "invalid payload: expected {token, call_runtime?}"}


def _denied(code):
    def answer(req):
        return _resp(req, code, json.dumps({"jsonrpc": "2.0", "id": 0, "error": {
            "code": -32002, "message": "Authorization error"}}))
    return answer


def _no_auth_header(t):
    for r in t.requests:
        assert r.get_header("Authorization") is None
        assert "Authorization" not in r.headers and "authorization" not in {
            k.lower() for k in r.unredirected_hdrs}


@pytest.mark.parametrize("code", [401, 403])
def test_anonymous_denied_reports_statuses(monkeypatch, code):
    mod = _load(monkeypatch)
    t = _install(mod, _denied(code))
    res = mod.lambda_handler({"anonymous": True}, _Ctx())
    assert res["ok"] is False and res["mode"] == "anonymous"
    assert [s["step"] for s in res["steps"]] == ["initialize", "notifications/initialized",
                                                 "tools/list"]
    assert [s["status"] for s in res["steps"]] == [code, code, code]
    assert res["runtime"] is None
    assert len(t.requests) == 3
    assert all(urllib.parse.urlsplit(r.full_url).hostname == GW_HOST for r in t.requests)
    _no_auth_header(t)
    methods = [json.loads(r.data)["method"] for r in t.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list"]


def test_anonymous_never_calls_tools_or_runtime(monkeypatch):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    res = mod.lambda_handler({"anonymous": True}, _Ctx())
    # A gateway that (wrongly) allows anonymous access shows up as ok true: the harness asserts
    # the denial, the probe only reports.
    assert res["ok"] is True
    methods = [json.loads(r.data)["method"] for r in t.requests]
    assert "tools/call" not in methods and len(methods) == 3
    assert res["steps"][-1]["tool_listed"] is True
    _no_auth_header(t)


@pytest.mark.parametrize("event", [
    {"anonymous": True, "token": TOKEN},
    {"anonymous": False},
    {"anonymous": "true"},
    {"anonymous": 1},
    {"anonymous": True, "call_runtime": False},
])
def test_other_anonymous_shapes_are_invalid(monkeypatch, event):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    assert mod.lambda_handler(event, _Ctx()) == INVALID
    assert t.requests == []


def test_existing_modes_unchanged(monkeypatch):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    res = mod.lambda_handler({"token": TOKEN}, _Ctx())
    assert "mode" not in res and res["ok"] is True
    assert [s["step"] for s in res["steps"]] == ["initialize", "notifications/initialized",
                                                 "tools/list", "tools/call"]
    assert all(r.get_header("Authorization") == "Bearer " + TOKEN for r in t.requests)


def test_redact_without_a_token():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "probe_redact_under_test", ROOT / "agentcore" / "probe" / "private_access_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._redact("Bearer abc " + TOKEN, None) == "Bearer [redacted] [redacted]"


def test_invoke_probe_anonymous_helper(monkeypatch):
    monkeypatch.setenv("GAC_PROBE_REGION", "ap-southeast-2")
    mod = _load(monkeypatch)
    _install(mod, _denied(401))
    sent = []

    def invoker(function, payload):
        sent.append((function, payload))
        return 200, None, json.dumps(mod.lambda_handler(json.loads(payload), _Ctx())).encode()
    doc = probe_fetch.invoke_probe_anonymous("gactest-private-access-probe", invoker=invoker)
    assert sent == [("gactest-private-access-probe", b'{"anonymous": true}')]
    assert doc["ok"] is False and [s["status"] for s in doc["steps"]] == [401, 401, 401]


def test_invoke_probe_anonymous_validates_the_name():
    with pytest.raises(probe_fetch.ProbeConfigError):
        probe_fetch.invoke_probe_anonymous("bad name!", invoker=lambda f, p: (200, None, b"{}"))
