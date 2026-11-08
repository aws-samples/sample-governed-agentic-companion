"""`./run.sh diagram-fetch` and the bridge tools when the upstream refuses (Change 1).

In private mode, from outside the VPC, the gateway answers HTTP 403 with JSON-RPC -32002 and the
runtime answers 403 "explicit deny in a resource-based policy". Both CLI paths must print one
clean line plus a hint, exit 3 (2 stays for config), never show a traceback, token or URL, and
write nothing. The gateway path runs the REAL MCP streamable-HTTP client over an httpx
MockTransport; the runtime path runs the real runtime client over a stubbed `requests`.
No AWS, no network.
"""
import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "frontdoor"):
    sys.path.insert(0, str(p))

pytest.importorskip("anyio")
httpx = pytest.importorskip("httpx")
pytest.importorskip("mcp")

import main as cli  # noqa: E402
import mcp_bridge as bridge  # noqa: E402
import diagram_relay as relay  # noqa: E402
import cognito_token  # noqa: E402
import runtime_client as rc  # noqa: E402
from upstream_errors import EXIT_UPSTREAM, HINT, UpstreamError, classify  # noqa: E402

REGION = "us-east-1"
GW_HOST = "examplegw-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com"
GW_URL = f"https://{GW_HOST}/mcp"
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-abc123"
STANDIN = "fake-front-door-value-0123456789"  # a placeholder, not a credential
GW_DENY = {"jsonrpc": "2.0", "id": 0,
           "error": {"code": -32002, "message": "Authorization error - Insufficient permissions"}}
RT_DENY = {"message": "User: anonymous is not authorized to perform: bedrock-agentcore:"
                      "InvokeAgentRuntime on resource: " + ARN + " with an explicit deny in a "
                      "resource-based policy"}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "ws"
    w.mkdir()
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(w))
    monkeypatch.setenv("GAC_GATEWAY_URL", GW_URL)
    monkeypatch.setenv("GAC_GATEWAY_REGION", REGION)
    monkeypatch.setenv("GAC_RUNTIME_ARN", ARN)
    monkeypatch.setenv("GAC_RUNTIME_REGION", REGION)
    monkeypatch.delenv("GAC_AGENTCORE_ENDPOINT", raising=False)
    monkeypatch.delenv("GAC_DIAGRAM_LOCAL_DIR", raising=False)
    monkeypatch.setattr(cognito_token.CognitoTokenProvider, "get_token", lambda self: STANDIN)
    monkeypatch.setattr(cognito_token.CognitoTokenProvider, "invalidate", lambda self: None)
    return w


def _gateway(monkeypatch, handler):
    """Route the real streamable-HTTP client through a MockTransport (with the relay's
    error-body hook, as in production)."""
    def factory(headers=None, timeout=None, auth=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=headers,
                                   timeout=timeout, auth=auth)
        client.event_hooks = {"request": [], "response": [relay._read_error_body]}
        return client
    monkeypatch.setattr(relay, "_http_client_factory", factory)


def _requests_stub(monkeypatch, answer):
    class _ConnErr(Exception):
        pass

    class _Timeout(Exception):
        pass

    class _Resp:
        def __init__(self, status, body):
            self.status_code, self.text = status, body
            self.headers = {}

        def iter_content(self, n):
            yield self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    calls = []

    def post(url, headers=None, data=None, timeout=None, stream=False):
        calls.append(headers.get("Authorization"))
        status, body = answer()
        if status is None:
            raise _ConnErr("connect failed to " + url)
        return _Resp(status, body)
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        ConnectionError=_ConnErr, Timeout=_Timeout, post=post))
    return calls


def _run(capsys, *argv):
    code = cli.main(["diagram-fetch", "draw our private network path", *argv])
    out = capsys.readouterr()
    return code, out.out + out.err


def _clean(text):
    assert "Traceback" not in text
    assert STANDIN not in text and "Bearer" not in text
    assert "https://" not in text and GW_HOST not in text
    assert "arn:aws" not in text and "123456789012" not in text


def _nothing_written(w):
    assert not (w / "outputs").exists() or not any((w / "outputs").rglob("*.*"))


# ---- gateway upstream ----------------------------------------------------------------------

class _Streamed(httpx.AsyncByteStream):
    """A body that is NOT pre-read, as on the real network: the message is only available
    because the relay's response hook reads small error bodies."""
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        yield self.data


def test_production_factory_installs_error_body_hook():
    client = relay._http_client_factory(headers={"a": "b"})
    assert relay._read_error_body in client.event_hooks["response"]
    assert client.follow_redirects is False


def test_gateway_403_clean_line_exit_3(ws, monkeypatch, capsys):
    _gateway(monkeypatch, lambda req: httpx.Response(
        403, headers={"content-type": "application/json"},
        stream=_Streamed(json.dumps(GW_DENY).encode("utf-8"))))
    code, text = _run(capsys)
    assert code == EXIT_UPSTREAM == 3
    assert text.splitlines()[0] == ("Not fetched: gateway denied the request (HTTP 403: "
                                    "Authorization error - Insufficient permissions)")
    assert HINT in text and "in private mode, run from a network that reaches the VPC endpoints" in text
    _clean(text)
    _nothing_written(ws)


class _Chunked(httpx.AsyncByteStream):
    """An endless-looking chunked body with no Content-Length; counts what was pulled."""
    def __init__(self, chunk, count):
        self.chunk, self.count, self.pulled = chunk, count, 0

    async def __aiter__(self):
        for _ in range(self.count):
            self.pulled += 1
            yield self.chunk


def test_error_body_hook_caps_undeclared_length():
    big = _Chunked(b"x" * 4096, 10_000)  # about 40 MB if read to the end
    resp = httpx.Response(403, stream=big)
    assert "content-length" not in resp.headers
    asyncio.run(relay._read_error_body(resp))
    assert big.pulled * 4096 <= relay.ERROR_BODY_CAP + 4096  # stopped at the cap
    with pytest.raises(httpx.ResponseNotRead):
        resp.content  # over the cap: no body kept, the status alone is reported
    small = httpx.Response(403, stream=_Streamed(json.dumps(GW_DENY).encode("utf-8")))
    asyncio.run(relay._read_error_body(small))
    assert json.loads(small.content) == GW_DENY


def test_gateway_403_oversize_chunked_body_status_only(ws, monkeypatch, capsys):
    _gateway(monkeypatch, lambda req: httpx.Response(
        403, headers={"content-type": "application/json"}, stream=_Chunked(b"x" * 4096, 10_000)))
    code, text = _run(capsys)
    assert code == 3
    assert text.splitlines()[0] == "Not fetched: gateway denied the request (HTTP 403: Forbidden)"
    _clean(text)
    _nothing_written(ws)


def test_gateway_401(ws, monkeypatch, capsys):
    _gateway(monkeypatch, lambda req: httpx.Response(401, json={"message": "Unauthorized"}))
    code, text = _run(capsys)
    assert code == 3
    assert text.startswith("Not fetched: gateway denied the request (HTTP 401: Unauthorized)")
    _clean(text)
    _nothing_written(ws)


def test_gateway_network_error(ws, monkeypatch, capsys):
    def handler(req):
        raise httpx.ConnectError("connection refused to " + str(req.url))
    _gateway(monkeypatch, handler)
    code, text = _run(capsys)
    assert code == 3
    assert text.startswith("Not fetched: gateway is not reachable from this network (ConnectError)")
    assert HINT in text
    _clean(text)
    _nothing_written(ws)


def test_gateway_500_is_http_error(ws, monkeypatch, capsys):
    _gateway(monkeypatch, lambda req: httpx.Response(500, text="internal"))
    code, text = _run(capsys)
    assert code == 3 and text.startswith("Not fetched: gateway returned an error (HTTP 500: internal)")
    assert HINT not in text
    _clean(text)


def test_gateway_jsonrpc_error_on_200(ws, monkeypatch, capsys):
    _gateway(monkeypatch, lambda req: httpx.Response(200, json=GW_DENY))
    code, text = _run(capsys)
    assert code == 3
    assert text.startswith("Not fetched: gateway denied the request (MCP -32002: "
                           "Authorization error - Insufficient permissions)")
    _clean(text)


def test_config_error_stays_exit_2(ws, monkeypatch, capsys):
    monkeypatch.setenv("GAC_GATEWAY_URL", "https://evil.example/mcp")
    code, text = _run(capsys)
    assert code == 2 and text.startswith("Not fetched: GAC_GATEWAY_URL is not")
    _clean(text)


# ---- runtime upstream ----------------------------------------------------------------------

def test_runtime_403_clean_line_exit_3(ws, monkeypatch, capsys):
    calls = _requests_stub(monkeypatch, lambda: (403, json.dumps(RT_DENY)))
    code, text = _run(capsys, "--upstream", "runtime")
    assert code == 3
    first = text.splitlines()[0]
    assert first.startswith("Not fetched: runtime denied the request (HTTP 403: ")
    assert "explicit deny in a resource-based policy" in first and "[arn]" in first
    assert HINT in text
    assert len(calls) == 2  # the one token refresh retry, then the clean error
    _clean(text)
    _nothing_written(ws)


def test_runtime_401(ws, monkeypatch, capsys):
    _requests_stub(monkeypatch, lambda: (401, ""))
    code, text = _run(capsys, "--upstream", "runtime")
    assert code == 3 and text.startswith("Not fetched: runtime denied the request (HTTP 401)")
    _clean(text)
    _nothing_written(ws)


def test_runtime_network_error(ws, monkeypatch, capsys):
    _requests_stub(monkeypatch, lambda: (None, None))
    code, text = _run(capsys, "--upstream", "runtime")
    assert code == 3
    assert text.startswith("Not fetched: runtime is not reachable from this network")
    assert HINT in text
    _clean(text)
    _nothing_written(ws)


def test_runtime_gated_decline_exit_4(ws, monkeypatch, capsys):
    decline = "[diagram] I can't draw this from the knowledge base, so no file was produced."
    _requests_stub(monkeypatch, lambda: (200, json.dumps({"response": decline, "gated": True})))
    code, text = _run(capsys, "--upstream", "runtime")
    assert code == 4 and text.strip() == decline
    _clean(text)
    _nothing_written(ws)


def test_runtime_oversize_body_not_saved_exit_1(ws, monkeypatch, capsys):
    _requests_stub(monkeypatch, lambda: (200, "{" + " " * (rc.UPSTREAM_CAP + 1) + "}"))
    code, text = _run(capsys, "--upstream", "runtime")
    assert code == 1 and "exceeded the size limit" in text
    _nothing_written(ws)


def test_cognito_not_authorized_exit_3(ws, monkeypatch, capsys):
    class ClientError(Exception):
        def __init__(self):
            super().__init__("Incorrect username or password for user someone")
            self.response = {"Error": {"Code": "NotAuthorizedException",
                                       "Message": "Incorrect username or password."}}
    e = cognito_token._mapped(ClientError())
    assert isinstance(e, UpstreamError) and e.kind == "auth"

    def raising(self):
        raise e
    monkeypatch.setattr(cognito_token.CognitoTokenProvider, "get_token", raising)
    code, text = _run(capsys)
    assert code == 3
    assert text.strip() == "Not fetched: Cognito denied the sign-in (NotAuthorizedException)"
    assert "someone" not in text and "password" not in text
    _clean(text)


def test_cognito_missing_credentials_is_config():
    class NoCredentialsError(Exception):
        pass
    e = cognito_token._mapped(NoCredentialsError())
    assert isinstance(e, cognito_token.CognitoConfigError)


# ---- the IDE bridge tool path --------------------------------------------------------------

def _call_tool(server, name, args):
    from mcp import types as mt
    handler = server._mcp_server.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(method="tools/call",
                             params=mt.CallToolRequestParams(name=name, arguments=args))
    return asyncio.run(handler(req)).root


@pytest.mark.parametrize("tool,args", [
    ("draw_diagram", {"prompt": "draw our private network path"}),
    ("companion_kb", {"query": "what is the deployment policy?"}),
    ("ask_companion", {"prompt": "what is the deployment policy?"}),
])
def test_bridge_gateway_tools_return_clean_error(ws, monkeypatch, tool, args):
    _gateway(monkeypatch, lambda req: httpx.Response(403, json=GW_DENY))
    server = bridge.build_server("gateway", list(bridge.ALL_TOOLS))
    res = _call_tool(server, tool, args)
    assert res.isError is True
    text = res.content[0].text
    assert "Not fetched: gateway denied the request (HTTP 403: Authorization error" in text
    _clean(text)
    _nothing_written(ws)


@pytest.mark.parametrize("tool,args", [
    ("draw_diagram", {"prompt": "draw our private network path"}),
    ("companion_kb", {"query": "what is the deployment policy?"}),
])
def test_bridge_runtime_tools_return_clean_error(ws, monkeypatch, tool, args):
    _requests_stub(monkeypatch, lambda: (403, json.dumps(RT_DENY)))
    client = rc.CompanionRuntimeClient(cognito_token.CognitoTokenProvider())
    server = bridge.build_server("runtime", list(bridge.ALL_TOOLS), client)
    res = _call_tool(server, tool, args)
    assert res.isError is True
    text = res.content[0].text
    assert "Not fetched: runtime denied the request (HTTP 403:" in text
    _clean(text)
    _nothing_written(ws)


def test_check_gateway_tools_denied_exit_3(ws, monkeypatch, capsys):
    _gateway(monkeypatch, lambda req: httpx.Response(403, json=GW_DENY))
    assert bridge.check_gateway_tools() == 3
    out = capsys.readouterr().out
    assert out.startswith("Not fetched: gateway denied the request (HTTP 403")
    _clean(out)


# ---- the classifier ------------------------------------------------------------------------

def test_classify_unknown_shows_type_only():
    e = classify(ValueError("secret " + STANDIN + " https://x.example/?a=b"), "gateway")
    assert str(e) == "Not fetched: the gateway call failed (ValueError)"


def test_short_message_strips_secrets():
    from upstream_errors import short_message
    s = short_message("Bearer abc.def.ghi at https://u:p@host/x for arn:aws:iam::1:role/r eyJabcdef.ghijkl.mno")
    assert "abc.def" not in s and "https" not in s and "arn:aws" not in s and "eyJ" not in s
