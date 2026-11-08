"""Tests for the private access probe (agentcore/probe/private_access_probe.py). No AWS, no
network, no listener: DNS is monkeypatched, HTTP goes through a fake transport built with the
module's own _build_opener(), and any real socket connection fails the test.

The invariant under test: the caller's bearer token is never stored, logged, returned, or sent
to any host other than the configured gateway and the AgentCore data-plane host.
"""
import ast
import http.client
import importlib.util
import io
import json
import logging
import socket
import ssl
import urllib.parse
import urllib.request
import urllib.response
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "agentcore" / "probe" / "private_access_probe.py"

REGION = "ap-southeast-2"
GW_HOST = "examplegw-abc123.gateway.bedrock-agentcore.ap-southeast-2.amazonaws.com"
DP_HOST = "bedrock-agentcore.ap-southeast-2.amazonaws.com"
ARN = "arn:aws:bedrock-agentcore:ap-southeast-2:123456789012:runtime/gactest_orchestrator-AbC123"
# A fake, JWT-shaped placeholder (not a real credential).
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJwcm9iZS10ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXBsYWNlaG9sZGVy"  # pragma: allowlist secret

ENV = {
    "GAC_PROBE_GATEWAY_URL": f"https://{GW_HOST}/mcp",
    "GAC_PROBE_RUNTIME_ARN": ARN,
    "GAC_PROBE_REGION": REGION,
    "GAC_PROBE_VPC_CIDR": "10.80.0.0/16",
    "GAC_PROBE_TOOL_NAME": "gac___companion_kb",
}


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real network connection attempted")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.80.1.10", port)),
                                                     (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.80.17.20", port))])


def _load(monkeypatch, env=None):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in (env if env is not None else ENV).items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("private_access_probe_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resp(req, code, body, ctype="application/json", headers=None):
    msg = http.client.HTTPMessage()
    msg["Content-Type"] = ctype
    for k, v in (headers or {}).items():
        msg[k] = v
    r = urllib.response.addinfourl(io.BytesIO(body.encode("utf-8")), msg, req.full_url, code)
    r.msg = "fake"
    return r


class _FakeTransport(urllib.request.HTTPSHandler):
    """Replaces the HTTPS handler (subclass, so build_opener drops the default one) and answers
    http:// too (handler_order 100 puts it ahead of the default HTTPHandler)."""
    handler_order = 100

    def __init__(self, answer):
        super().__init__(context=ssl.create_default_context())
        self.answer = answer
        self.requests = []

    def https_open(self, req):
        self.requests.append(req)
        return self.answer(req)

    def http_open(self, req):
        self.requests.append(req)
        return self.answer(req)


def _ok_answer(echo_token=False):
    def answer(req):
        body = json.loads(req.data.decode("utf-8"))
        host = urllib.parse.urlsplit(req.full_url).hostname
        leak = (" echo " + req.get_header("Authorization")) if echo_token else ""
        if host == DP_HOST:
            return _resp(req, 200, json.dumps({"answer": "ok" + leak}))
        m = body.get("method")
        if m == "initialize":
            return _resp(req, 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}),
                         headers={"Mcp-Session-Id": "sess-1"})
        if m == "notifications/initialized":
            return _resp(req, 202, "")
        if m == "tools/list":
            return _resp(req, 200, "event: message\ndata: " + json.dumps(
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "gac___companion_kb"}]}}) + "\n\n",
                ctype="text/event-stream")
        return _resp(req, 200, json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"content": [{"text": "policy" + leak}]}}))
    return answer


class _Ctx:
    def __init__(self, ms=110000):
        self.ms = ms

    def get_remaining_time_in_millis(self):
        return self.ms


def _install(mod, answer):
    t = _FakeTransport(answer)
    mod._OPENER = mod._build_opener(t)
    return t


def _no_token_anywhere(result, caplog, capsys):
    out = json.dumps(result)
    assert TOKEN not in out
    assert not mod_jwt_re().search(out)
    assert TOKEN not in caplog.text
    cap = capsys.readouterr()
    assert TOKEN not in cap.out and TOKEN not in cap.err


def mod_jwt_re():
    import re
    return re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")


# ---- Tests ---------------------------------------------------------------------------------

def test_happy_path(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN, "call_runtime": True}, _Ctx())
    assert res["ok"] is True
    assert res["dns"]["ips"] == ["10.80.1.10", "10.80.17.20"]
    assert res["dns"]["all_private"] is True and res["dns"]["in_vpc_cidr"] is True
    assert [s["step"] for s in res["steps"]] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert [s["status"] for s in res["steps"]] == [200, 202, 200, 200]
    assert res["steps"][2]["tool_listed"] is True
    assert res["runtime"]["invoke"]["status"] == 200
    # Session header forwarded after initialize; every request went to an allowed host over https.
    assert t.requests[1].get_header("Mcp-session-id") == "sess-1"
    for r in t.requests:
        parts = urllib.parse.urlsplit(r.full_url)
        assert parts.scheme == "https" and parts.hostname in (GW_HOST, DP_HOST)
    assert "step=tools/call status=200" in caplog.text
    _no_token_anywhere(res, caplog, capsys)


@pytest.mark.parametrize("code", [302, 307])
def test_redirect_never_followed(monkeypatch, caplog, capsys, code):
    mod = _load(monkeypatch)

    def answer(req):
        return _resp(req, code, "moved, your token is " + TOKEN, headers={"Location": "http://evil.example/"})
    t = _install(mod, answer)
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN}, _Ctx())
    # One request per step, all to the gateway; none to the Location host.
    assert all(urllib.parse.urlsplit(r.full_url).hostname == GW_HOST for r in t.requests)
    assert len(t.requests) == 4
    assert res["steps"][0]["status"] == code
    assert "evil.example" not in json.dumps(res)
    assert res["ok"] is False
    _no_token_anywhere(res, caplog, capsys)


def test_opener_composition(monkeypatch):
    mod = _load(monkeypatch)
    hs = mod._OPENER.handlers
    # ProxyHandler({}) replaces the default env-reading ProxyHandler and registers no proxy_open,
    # so no handler in the chain can route through a proxy.
    assert not [h for h in hs if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
    assert any(isinstance(h, mod._NoRedirect) for h in hs)
    https = [h for h in hs if type(h) is urllib.request.HTTPSHandler]
    assert len(https) == 1
    ctx = https[0]._context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_fake_transport_shadows_real_handlers(monkeypatch):
    mod = _load(monkeypatch)
    fake = _FakeTransport(_ok_answer())
    opener = mod._build_opener(fake)
    assert not [h for h in opener.handlers if type(h) is urllib.request.HTTPSHandler]
    for h in opener.handlers:
        if type(h) is urllib.request.HTTPHandler:
            assert h.handler_order > fake.handler_order


def test_host_allowlist(monkeypatch):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    for url in ("https://evil.example/mcp", f"http://{GW_HOST}/mcp", "https://bedrock-agentcore.us-east-1.amazonaws.com/x"):
        req = urllib.request.Request(url, data=b"{}", method="POST")
        with pytest.raises(mod._HostNotAllowed):
            mod._send(req, 10)
    assert t.requests == []


def test_ast_http_and_logging_rules():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            assert name != "urlopen"
            assert name != "print"
            assert name != "exception"
    # _OPENER.open is called only inside _send.
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open"
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "_OPENER"):
                    assert fn.name == "_send"
    # The production opener is built with no transport.
    assigns = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_OPENER" for t in n.targets)]
    assert len(assigns) == 1
    call = assigns[0].value
    assert isinstance(call, ast.Call) and call.func.id == "_build_opener" and not call.args and not call.keywords
    # No logging call references a name that could carry the token, the event, a body or headers.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "_LOG"
                and node.func.attr in ("debug", "info", "warning", "error", "critical", "log")):
            for arg in node.args[1:]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name):
                        low = sub.id.lower()
                        assert not any(w in low for w in ("token", "tok", "event", "body", "header", "hdrs", "raw")), sub.id
            assert isinstance(node.args[0], ast.Constant)


def test_deadline_skips_and_timeouts(monkeypatch):
    mod = _load(monkeypatch)
    seen = []

    def answer(req):
        seen.append(req.timeout)
        return _ok_answer()(req)
    t = _install(mod, answer)
    # 30 s left: the 10 s steps run, tools/call (45 s + 2 s margin) is skipped and sends nothing.
    res = mod.lambda_handler({"token": TOKEN, "call_runtime": True}, _Ctx(30000))
    statuses = [s["status"] for s in res["steps"]]
    assert statuses[:3] == [200, 202, 200] and statuses[3] == "skipped_deadline"
    assert res["runtime"]["invoke"]["status"] == "skipped_deadline"
    assert len(t.requests) == 3 and seen == [10, 10, 10]
    assert res["ok"] is False
    # Full budget: per-step timeouts are 10/10/10/45/45.
    seen.clear()
    mod.lambda_handler({"token": TOKEN, "call_runtime": True}, _Ctx())
    assert seen == [10, 10, 10, 45, 45]


def test_log_level_info(monkeypatch):
    _load(monkeypatch)
    assert logging.getLogger("gac.probe").getEffectiveLevel() == logging.INFO


def test_echoed_token_is_redacted(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)

    def answer(req):
        if json.loads(req.data.decode("utf-8")).get("method") == "tools/call":
            return _resp(req, 500, "upstream saw Authorization: " + req.get_header("Authorization") + " raw " + TOKEN)
        return _ok_answer(echo_token=True)(req)
    _install(mod, answer)
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN, "call_runtime": True}, _Ctx())
    assert res["steps"][3]["status"] == 500
    assert "[redacted]" in res["steps"][3]["body"]
    _no_token_anywhere(res, caplog, capsys)


def test_unexpected_exception_returns_type_only(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)
    _install(mod, _ok_answer())

    def broken(*a, **k):
        raise RuntimeError("boom " + TOKEN)
    monkeypatch.setattr(mod, "_http_step", broken)
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN}, _Ctx())
    assert res == {"ok": False, "error": "RuntimeError"}
    _no_token_anywhere(res, caplog, capsys)


@pytest.mark.parametrize("event", [
    {},
    {"token": 123},
    {"token": "short.a.b"},  # pragma: allowlist secret
    {"token": TOKEN, "url": "https://evil.example/"},
    {"token": TOKEN, "call_runtime": "yes"},
    {"token": "not a jwt at all with spaces ......"},  # pragma: allowlist secret
    "not-a-dict",
    None,
])
def test_invalid_payload_rejected_without_echo(monkeypatch, event):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    res = mod.lambda_handler(event, _Ctx())
    assert res == {"ok": False, "error": "invalid payload: expected {token, call_runtime?}"}
    assert t.requests == []


@pytest.mark.parametrize("override", [
    {"GAC_PROBE_GATEWAY_URL": "https://evil.example/mcp"},
    {"GAC_PROBE_GATEWAY_URL": f"https://{GW_HOST}/other"},
    {"GAC_PROBE_GATEWAY_URL": f"http://{GW_HOST}/mcp"},
    {"GAC_PROBE_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/x"},
    {"GAC_PROBE_REGION": "nowhere"},
])
def test_invalid_config(monkeypatch, override):
    mod = _load(monkeypatch, dict(ENV, **override))
    t = _install(mod, _ok_answer())
    res = mod.lambda_handler({"token": TOKEN}, _Ctx())
    assert res == {"ok": False, "error": "probe configuration invalid"}
    assert t.requests == [] and mod._ALLOWED_HOSTS == frozenset()


def test_runtime_call_url_and_session(monkeypatch):
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    mod.lambda_handler({"token": TOKEN, "call_runtime": True}, _Ctx())
    rt = [r for r in t.requests if urllib.parse.urlsplit(r.full_url).hostname == DP_HOST]
    assert len(rt) == 1
    assert rt[0].full_url == f"https://{DP_HOST}/runtimes/{urllib.parse.quote(ARN, safe='')}/invocations"
    assert len(rt[0].get_header("X-amzn-bedrock-agentcore-runtime-session-id")) >= 33
    assert json.loads(rt[0].data.decode("utf-8")) == {"prompt": "What is the deployment policy?", "environment": "dev"}


def test_dns_failure_recorded(monkeypatch):
    mod = _load(monkeypatch)
    _install(mod, _ok_answer())

    def fail(*a, **k):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    res = mod.lambda_handler({"token": TOKEN}, _Ctx())
    assert res["dns"]["status"] == "dns_error" and res["dns"]["error"] == "gaierror"


def test_proxy_environment_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("https_proxy", "http://proxy.example:3128")
    mod = _load(monkeypatch)
    t = _install(mod, _ok_answer())
    mod.lambda_handler({"token": TOKEN}, _Ctx())
    assert t.requests and all(urllib.parse.urlsplit(r.full_url).hostname == GW_HOST for r in t.requests)
    assert all(r.host == GW_HOST for r in t.requests)


# ---- Diagram mode ({token, diagram}) -------------------------------------------------------

DIAGRAM_TOOL = "gac___draw_diagram"
REQ = "draw our private network path"


def diagram_bundle(name="probe-test", tamper=False, extra_text=""):
    """A small, valid gac.diagram/1 bundle (the same envelope the bridge consumes)."""
    import base64
    import hashlib

    def art(file, media, enc, data):
        content = base64.b64encode(data).decode("ascii") if enc == "base64" else data.decode("utf-8")
        return {"file": file, "media_type": media, "encoding": enc, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "content": content}
    report = json.dumps({"schema": "gac.diagram-report/1",
                         "quality": {"score": 92.5, "pass": True, "threshold": 85.0}}).encode("utf-8")
    arts = [art(f"{name}.drawio", "application/vnd.jgraph.mxfile", "utf-8", ("<mxfile>" + extra_text + "</mxfile>").encode()),
            art(f"{name}.svg", "image/svg+xml", "utf-8", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
            art(f"{name}.png", "image/png", "base64", b"\x89PNG\r\n\x1a\nfake-png-bytes"),
            art(f"{name}.report.json", "application/json", "utf-8", report)]
    if tamper:
        arts[1]["sha256"] = "0" * 64
    return {"schema": "gac.diagram/1", "name": name, "draft": False,
            "quality": {"score": 92.5, "pass": True}, "artifacts": arts, "omitted": []}


def diagram_answer(bundle=None, *, code=200, sse=False, response_text="Diagram drawn (gated).",
                   is_error=False, raw=None, seen=None):
    def answer(req):
        body = json.loads(req.data.decode("utf-8"))
        if seen is not None:
            seen.append(body)
        m = body.get("method")
        if m == "initialize":
            return _resp(req, 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
                         headers={"Mcp-Session-Id": "sess-d"})
        if m == "notifications/initialized":
            return _resp(req, 202, "")
        if raw is not None:
            return _resp(req, code, raw)
        inner = json.dumps({"response": response_text,
                            **({"diagram": bundle} if bundle is not None else {})}, separators=(",", ":"))
        msg = json.dumps({"jsonrpc": "2.0", "id": 3,
                          "result": {"content": [{"type": "text", "text": inner}], "isError": is_error}})
        if sse:
            return _resp(req, code, "event: message\ndata: " + msg + "\n\n", ctype="text/event-stream")
        return _resp(req, code, msg)
    return answer


@pytest.mark.parametrize("sse", [False, True])
def test_diagram_mode_returns_full_envelope(monkeypatch, caplog, capsys, sse):
    mod = _load(monkeypatch)
    seen = []
    bundle = diagram_bundle()
    t = _install(mod, diagram_answer(bundle, sse=sse, seen=seen))
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN, "diagram": "  " + REQ + "  "}, _Ctx())
    assert res["ok"] is True and res["mode"] == "diagram" and res["error"] is None
    assert [s["step"] for s in res["steps"]] == ["initialize", "notifications/initialized", "tools/call:draw_diagram"]
    assert res["result"]["diagram"] == bundle
    assert res["result"]["response"] == "Diagram drawn (gated)."
    call = seen[-1]
    assert call["params"] == {"name": DIAGRAM_TOOL, "arguments": {
        "prompt": REQ, "environment": "dev", "title": "", "name": "", "include_artifacts": True}}
    # Only the gateway, no tools/list, no runtime call; the session header is forwarded.
    assert len(t.requests) == 3 and all(urllib.parse.urlsplit(r.full_url).hostname == GW_HOST for r in t.requests)
    assert t.requests[2].get_header("Mcp-session-id") == "sess-d"
    # Step lines only: never artefact content in the logs; the step record carries no body.
    assert "step=tools/call:draw_diagram status=200" in caplog.text
    assert "mxfile" not in caplog.text and "PNG" not in caplog.text
    assert res["steps"][2]["body"] == ""
    _no_token_anywhere(res, caplog, capsys)


def test_diagram_mode_signature_matches_mcp_runtime():
    import inspect
    sys_path = str(ROOT / "agentcore" / "app" / "mcp_runtime")
    import sys
    sys.path.insert(0, sys_path)
    import runtime_mcp_entrypoint as mcp_rt
    params = list(inspect.signature(mcp_rt.draw_diagram).parameters)
    assert params == ["prompt", "environment", "title", "name", "include_artifacts", "planner"]


def test_diagram_tool_name_is_config_only(monkeypatch):
    mod = _load(monkeypatch)
    t = _install(mod, diagram_answer(diagram_bundle()))
    for event in ({"token": TOKEN, "diagram": REQ, "tool": "gac___other"},
                  {"token": TOKEN, "diagram": REQ, "name": "x"},
                  {"token": TOKEN, "diagram": REQ, "call_runtime": True}):
        assert mod.lambda_handler(event, _Ctx()) == {"ok": False, "error": mod._INVALID_DIAGRAM}
    assert t.requests == []
    bad = _load(monkeypatch, dict(ENV, GAC_PROBE_DIAGRAM_TOOL_NAME="bad name!"))
    assert bad.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx()) == {"ok": False, "error": "probe configuration invalid"}
    # The Terraform wiring pins the tool name.
    tf = (ROOT / "agentcore" / "terraform" / "probe.tf").read_text(encoding="utf-8")
    assert 'GAC_PROBE_DIAGRAM_TOOL_NAME = "gac___draw_diagram"' in tf


@pytest.mark.parametrize("text", ["", "   ", "x" * 501, "line\nbreak", "tab\there", 42, None, ["a"]])
def test_diagram_request_bounds(monkeypatch, text):
    mod = _load(monkeypatch)
    t = _install(mod, diagram_answer(diagram_bundle()))
    res = mod.lambda_handler({"token": TOKEN, "diagram": text}, _Ctx())
    assert res == {"ok": False, "error": "invalid payload: expected {token, diagram} with diagram 1-500 characters"}
    assert t.requests == []


def test_diagram_request_500_chars_accepted(monkeypatch):
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(diagram_bundle()))
    assert mod.lambda_handler({"token": TOKEN, "diagram": "y" * 500}, _Ctx())["ok"] is True


def test_diagram_bad_token_rejected(monkeypatch):
    mod = _load(monkeypatch)
    t = _install(mod, diagram_answer(diagram_bundle()))
    res = mod.lambda_handler({"token": "short.a.b", "diagram": REQ}, _Ctx())
    assert res["ok"] is False and t.requests == []


def test_diagram_oversize_upstream_body(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(raw="x" * (mod.DIAGRAM_READ_LIMIT + 10)))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and res["result"] is None
    assert res["steps"][2]["status"] == "too_large"
    assert "5 MiB" in res["error"]
    _no_token_anywhere(res, caplog, capsys)


def test_diagram_over_lambda_response_limit(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(diagram_bundle()))
    monkeypatch.setattr(mod, "LAMBDA_RESPONSE_CAP", 1000)
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and res["result"] is None
    assert "6 MB" in res["error"]
    assert len(json.dumps(res)) < 6_000_000
    assert "step=result status=too_large" in caplog.text


def _tool_body_len(bundle):
    inner = json.dumps({"response": "Diagram drawn (gated).", "diagram": bundle}, separators=(",", ":"))
    return len(json.dumps({"jsonrpc": "2.0", "id": 3,
                           "result": {"content": [{"type": "text", "text": inner}], "isError": False}}))


def test_diagram_largest_admitted_body_fits_lambda_limit(monkeypatch, caplog, capsys):
    # Behavioural, at the REAL caps: a tool body just under the upstream read limit is returned
    # whole and the serialised result stays under the 6 MB synchronous response limit; a body
    # just over the read limit is refused unparsed.
    mod = _load(monkeypatch)
    base = _tool_body_len(diagram_bundle())
    under = diagram_bundle(extra_text="a" * (mod.DIAGRAM_READ_LIMIT - base - 1024))
    assert mod.DIAGRAM_READ_LIMIT - 2048 < _tool_body_len(under) < mod.DIAGRAM_READ_LIMIT
    _install(mod, diagram_answer(under))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is True and res["result"]["diagram"]["artifacts"][0]["bytes"] > 5_000_000
    assert len(json.dumps(res)) <= mod.LAMBDA_RESPONSE_CAP < 6 * 1024 * 1024
    mod = _load(monkeypatch)
    over = diagram_bundle(extra_text="a" * (mod.DIAGRAM_READ_LIMIT - base + 1024))
    _install(mod, diagram_answer(over))
    caplog.set_level(logging.INFO, logger="gac.probe")
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and not res.get("result")
    assert len(json.dumps(res)) < 100_000


def test_diagram_token_redacted_in_text_and_withheld_in_artefacts(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(diagram_bundle(), response_text="echo " + TOKEN + " Bearer " + TOKEN))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is True and "[redacted]" in res["result"]["response"]
    _no_token_anywhere(res, caplog, capsys)
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(diagram_bundle(extra_text=TOKEN)))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and "withheld" in res["error"] and "diagram" not in res["result"]
    _no_token_anywhere(res, caplog, capsys)


def test_diagram_denied_by_gateway(monkeypatch, caplog, capsys):
    mod = _load(monkeypatch)
    deny = json.dumps({"jsonrpc": "2.0", "id": 0, "error": {"code": -32002, "message": "Authorization error - Insufficient permissions"}})
    _install(mod, lambda req: _resp(req, 403, deny + " " + TOKEN))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and res["result"] is None
    assert [s["status"] for s in res["steps"]] == [403, 403, 403]
    assert "Insufficient permissions" in res["steps"][2]["body"]
    _no_token_anywhere(res, caplog, capsys)


def test_diagram_tool_error_and_no_artefacts(monkeypatch):
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(None, is_error=True, response_text="boom"))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and res["error"].startswith("tool error:")
    mod = _load(monkeypatch)
    _install(mod, diagram_answer(None, response_text="Declined: no grounded topic."))
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert res["ok"] is False and res["result"] == {"response": "Declined: no grounded topic."}
    assert res["error"].startswith("no artefacts returned")


def test_diagram_timeouts_and_deadline(monkeypatch):
    mod = _load(monkeypatch)
    seen = []

    def answer(req):
        seen.append(req.timeout)
        return diagram_answer(diagram_bundle())(req)
    _install(mod, answer)
    mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx())
    assert seen == [10, 10, 90]
    seen.clear()
    res = mod.lambda_handler({"token": TOKEN, "diagram": REQ}, _Ctx(60000))
    assert seen == [10, 10] and res["steps"][2]["status"] == "skipped_deadline" and res["ok"] is False


def test_existing_modes_unchanged_by_diagram_mode(monkeypatch):
    mod = _load(monkeypatch)
    _install(mod, _ok_answer())
    res = mod.lambda_handler({"token": TOKEN}, _Ctx())
    assert "mode" not in res and "result" not in res
    assert [s["step"] for s in res["steps"]] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
