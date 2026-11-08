"""In-process tests for the MCP runtime's stateless streamable-HTTP contract.

These drive the REAL ASGI app returned by
agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py::build_app() through httpx's in-process
ASGI transport. Nothing binds a socket. They prove the protocol behaviour the docs claim:

- initialize succeeds and tools/list lists the companion's two tools without a prior session;
- an Mcp-Session-Id the server never issued (the AgentCore platform adds one to requests that
  lack it) is accepted, and the server issues no session id of its own (stateless);
- the server is built with stateless_http=True on 0.0.0.0:8000 at /mcp, and the container
  (Dockerfile + __main__) serves that same bind.

They do NOT prove the AgentCore Runtime platform health check or READY state; only a deploy can
(docs/DEPLOY.md post-deploy test 6). The SDK-dependent tests need mcp 1.30.0 (Python 3.10+) and
skip on Python 3.9, where requirements.txt leaves mcp uninstalled.
"""
import asyncio
import json
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "orchestrator"))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "mcp_runtime"))

import runtime_mcp_entrypoint as mcp_rt  # noqa: E402

MCP_DIR = ROOT / "agentcore" / "app" / "mcp_runtime"
PROTOCOL_VERSION = "2025-06-18"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _rpc(method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _initialize(rid=1):
    return _rpc("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    }, rid)


def _parse(resp):
    """Return the JSON-RPC message from a JSON or SSE (text/event-stream) response."""
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"no data: line in SSE body: {resp.text[:200]!r}")
    return resp.json()


def _post_all(messages, extra_headers=None):
    """POST each JSON-RPC message to /mcp on a FRESH app (a streamable-HTTP session manager
    runs once per instance) and return the list of httpx responses."""
    httpx = pytest.importorskip("httpx")
    app = mcp_rt.build_app()
    headers = dict(HEADERS)
    headers.update(extra_headers or {})

    async def _run():
        out = []
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://127.0.0.1:8000") as client:
                for m in messages:
                    out.append(await client.post("/mcp", json=m, headers=headers))
        return out

    return asyncio.run(_run())


def test_initialize_succeeds():
    pytest.importorskip("mcp.server.fastmcp")
    (resp,) = _post_all([_initialize()])
    assert resp.status_code == 200
    msg = _parse(resp)
    assert "error" not in msg
    assert msg["result"]["serverInfo"]["name"] == "governed-companion"
    assert msg["result"]["protocolVersion"] == PROTOCOL_VERSION


def test_tools_list_without_session():
    pytest.importorskip("mcp.server.fastmcp")
    (resp,) = _post_all([_rpc("tools/list", {}, rid=2)])
    assert resp.status_code == 200
    msg = _parse(resp)
    assert "error" not in msg
    names = {t["name"] for t in msg["result"]["tools"]}
    assert names == {"ask_companion", "companion_kb", "draw_diagram"}


def test_accepts_platform_session_id_not_issued_by_server():
    pytest.importorskip("mcp.server.fastmcp")
    platform_sid = secrets.token_hex(32)  # 64 hex chars, never issued by this server
    responses = _post_all(
        [
            _initialize(rid=1),
            _rpc("tools/list", {}, rid=2),
            _rpc("tools/call", {"name": "companion_kb",
                                "arguments": {"query": "how are secrets handled?"}}, rid=3),
        ],
        extra_headers={"Mcp-Session-Id": platform_sid},
    )
    for resp in responses:
        assert resp.status_code == 200, resp.text[:300]
        assert "error" not in _parse(resp)
    call = _parse(responses[2])["result"]
    text = "".join(c.get("text", "") for c in call["content"])
    assert "Governance Outcome" in text  # the tool result passed the always-on gate


def test_server_issues_no_session_id():
    pytest.importorskip("mcp.server.fastmcp")
    (resp,) = _post_all([_initialize()])
    assert resp.status_code == 200
    # A stateful streamable-HTTP server issues mcp-session-id on initialize; stateless does not.
    assert "mcp-session-id" not in {k.lower() for k in resp.headers.keys()}


def test_stateless_flag_host_port_path(monkeypatch):
    fastmcp_mod = pytest.importorskip("mcp.server.fastmcp")
    # Force the documented 1.x import path deterministically, even if a future pin ships 2.x.
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    built = []

    class _Spy(fastmcp_mod.FastMCP):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(fastmcp_mod, "FastMCP", _Spy)
    assert mcp_rt.build_app() is not None
    assert len(built) == 1
    settings = built[0].settings
    assert settings.stateless_http is True
    assert settings.host == "0.0.0.0"  # nosec B104 - asserting the in-container bind contract
    assert settings.port == 8000
    assert settings.streamable_http_path == "/mcp"


def test_accepts_non_loopback_host_header():
    pytest.importorskip("mcp.server.fastmcp")
    (resp,) = _post_all([_initialize()],
                        extra_headers={"Host": "bedrock-agentcore.us-east-1.amazonaws.com"})
    assert resp.status_code == 200
    assert "error" not in _parse(resp)


def test_container_contract_static():
    pytest.importorskip("mcp.server.fastmcp")
    dockerfile = (MCP_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "GAC_MCP_PORT=8000" in dockerfile
    assert any(line.strip() == "EXPOSE 8000" for line in dockerfile.splitlines())
    cmd_lines = [ln for ln in dockerfile.splitlines() if ln.startswith("CMD ")]
    assert cmd_lines and cmd_lines[-1].rstrip().endswith('runtime_mcp_entrypoint.py"]')
    src = (MCP_DIR / "runtime_mcp_entrypoint.py").read_text(encoding="utf-8")
    assert 'uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)' in src
    assert 'os.getenv("GAC_MCP_PORT", "8000")' in src


def test_build_app_without_sdk_raises_clear_error(monkeypatch):
    # A None entry in sys.modules makes `import` raise ImportError, so both SDK paths are absent.
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(RuntimeError, match="mcp"):
        mcp_rt.build_app()
