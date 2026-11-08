"""Diagram front doors: runtime /invocations, MCP runtime tool (design D12, D13; acceptance
22, 23, N10)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "orchestrator"))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "mcp_runtime"))

pytest.importorskip("defusedxml")

import runtime_entrypoint as rt  # noqa: E402
import runtime_mcp_entrypoint as mcp_rt  # noqa: E402
from diagram_fixtures import fixture_assets_dir  # noqa: E402
from agents.diagram import assets as A  # noqa: E402

REQ = "draw our private network path"


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    return fixture_assets_dir(tmp_path_factory.mktemp("cat"))


@pytest.fixture
def local_catalog(monkeypatch, catalog):
    monkeypatch.delenv("GAC_DIAGRAM_ASSETS_DIR", raising=False)
    monkeypatch.setattr(A, "LOCAL_ASSETS_DIR", catalog)
    return catalog


# -- runtime /invocations -------------------------------------------------------------------

def test_missing_and_ask_action_unchanged(local_catalog):
    a = rt.handle_invocation({"prompt": "what is the deployment policy?"})
    b = rt.handle_invocation({"prompt": "what is the deployment policy?", "action": "ask"})
    assert a == b and set(a) == {"response", "gated"}


def test_diagram_action_manifest_by_default(local_catalog):
    out = rt.handle_invocation({"prompt": REQ, "action": "diagram"})
    assert set(out) == {"response", "gated", "diagram_manifest"}
    assert "content" not in json.dumps(out["diagram_manifest"])


def test_diagram_action_with_artifacts(local_catalog):
    out = rt.handle_invocation({"prompt": REQ, "action": "diagram", "include_artifacts": True,
                                "name": "my-figure", "title": "Our private path"})
    assert set(out) == {"response", "gated", "diagram"}
    assert out["diagram"]["artifacts"][0]["file"] == "my-figure.drawio"
    assert '"Our private path"' in out["response"]


def test_include_artifacts_must_be_boolean(local_catalog):
    out = rt.handle_invocation({"prompt": REQ, "action": "diagram", "include_artifacts": "true"})
    assert "diagram" not in out and "diagram_manifest" in out


def test_blocked_or_declined_has_neither_key(local_catalog, monkeypatch):
    monkeypatch.setenv("GAC_DIAGRAM_ENABLED", "false")
    out = rt.handle_invocation({"prompt": REQ, "action": "diagram", "include_artifacts": True})
    assert set(out) == {"response", "gated"}


def test_unknown_action():
    out = rt.handle_invocation({"prompt": "x", "action": "deploy"})
    assert "Unknown action 'deploy'; supported: ask, diagram." in out["response"]
    out = rt.handle_invocation({"prompt": "x", "action": "Bad Action!"})
    assert "Unknown action; supported" in out["response"] and "Bad Action" not in out["response"]


# -- MCP runtime ------------------------------------------------------------------------------

def test_draw_diagram_registered_without_structured_output():
    src = (ROOT / "agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py").read_text()
    assert "mcp.tool(structured_output=False)(draw_diagram)" in src


def test_fake_tool_rejecting_flag_leaves_draw_unregistered(caplog):
    registered = []

    class Fake:
        def tool(self, **kw):
            if kw:
                raise TypeError("unexpected keyword")
            return lambda fn: registered.append(fn.__name__)

    mcp_rt.register_tools(Fake())
    assert registered == ["ask_companion", "companion_kb"]
    assert "draw_diagram not registered" in caplog.text


def _call_tool_bytes(args):
    """Bytes FastMCP actually emits for tools/call draw_diagram (serialized JSON-RPC)."""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("mcp.server.fastmcp")
    app = mcp_rt.build_app()
    msg = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
           "params": {"name": "draw_diagram", "arguments": args}}
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://127.0.0.1:8000") as c:
                return await c.post("/mcp", json=msg, headers=headers)
    resp = asyncio.run(run())
    body = resp.text
    for line in body.splitlines():
        if line.startswith("data:"):
            data = line[5:].strip()
            return len(data.encode()), json.loads(data)
    return len(body.encode()), resp.json()


def test_default_mcp_result_small_and_single_copy(local_catalog):
    size, msg = _call_tool_bytes({"prompt": REQ})
    result = msg["result"]
    assert "structuredContent" not in result
    inner = json.loads(result["content"][0]["text"])
    assert "diagram" not in inner and "diagram_manifest" in inner
    assert size <= 16 * 1024


def test_mcp_result_with_artifacts_within_budget(local_catalog):
    size, msg = _call_tool_bytes({"prompt": REQ, "include_artifacts": True})
    inner = json.loads(msg["result"]["content"][0]["text"])
    assert inner["diagram"]["limits"]["wire_bytes"] <= 4 * 1024 * 1024
    assert size <= inner["diagram"]["limits"]["wire_bytes"]


@pytest.fixture
def worst_case(monkeypatch, local_catalog):
    """500 facts that are all flagged plus a scene at every element cap with 48-char labels."""
    from agents.diagram.topics import Topic
    from agents.specialists import diagram_specialist as DS
    from agents.specialists import platform_specialist as PS
    key = "tier_b/platform/diagram_facts"

    def lab(p, i):
        return (f"{p} {i:02d} " + "x" * 48)[:48]
    facts = [{"kind": "group", "id": f"g{i}", "type": "generic", "label": lab("Group", i),
              "topics": ["cap"]} for i in range(12)]
    facts += [{"kind": "node", "id": f"n{i}", "label": lab("Node", i), "group": f"g{i % 12}",
               "topics": ["cap"]} for i in range(24)]
    facts += [{"kind": "edge", "id": f"e{i}", "from": f"n{i % 24}", "to": f"n{(i * 7 + 1) % 24}",
               "label": lab("Edge", i), "topics": ["cap"]} for i in range(40)]
    facts += [{"kind": "node", "id": "bad" + "z" * 40 + f"{i:03d}", "label": "has been deployed",
               "topics": ["cap"]} for i in range(424)]
    for f in facts:
        fid = f["id"]
        f.update(kb_key=key, kb_ref=f"{key}#{fid}", source=f"platform:{key}#{fid}")
    monkeypatch.setattr(PS.PlatformSpecialist, "diagram_facts", lambda self, t: [dict(x) for x in facts])
    monkeypatch.setattr(DS.DiagramSpecialist, "topics",
                        lambda self: ([Topic("cap", "Cap topic", ("cap topic",), "tier_a/diagram/topics")], []))


def test_default_mcp_result_bounded_worst_case(worst_case):
    size, msg = _call_tool_bytes({"prompt": "draw the cap topic"})
    inner = json.loads(msg["result"]["content"][0]["text"])
    assert "diagram" not in inner
    print("worst-case default MCP result bytes:", size)
    assert size <= 16 * 1024, size
    assert len(inner["response"].encode()) <= 13 * 1024


# -- runtime client, relay and bridge (item 11) -------------------------------------------

sys.path.insert(0, str(ROOT / "frontdoor"))
import diagram_relay as relay  # noqa: E402
import mcp_bridge as bridge  # noqa: E402
from runtime_client import CompanionRuntimeClient  # noqa: E402


class _Tok:
    def get_token(self):
        return "t"

    def invalidate(self):
        pass


def test_client_payload_unchanged_without_kwargs(monkeypatch):
    monkeypatch.setenv("GAC_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:" + "1" * 12 + ":runtime/x")
    seen = []
    c = CompanionRuntimeClient(_Tok(), _post=lambda u, h, d: seen.append(d) or (200, "{}"))
    c.invoke("hello", "dev")
    assert seen[0] == json.dumps({"prompt": "hello", "environment": "dev"}).encode()
    c.invoke("hello", "dev", action="diagram", include_artifacts=True)
    assert json.loads(seen[1]) == {"prompt": "hello", "environment": "dev", "action": "diagram",
                                   "include_artifacts": True}


@pytest.mark.parametrize("url,ok", [
    ("https://my-gw-123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", True),
    ("https://my-gw.gateway.bedrock-agentcore.eu-west-2.amazonaws.com/mcp", False),
    ("http://my-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", False),
    ("https://u:p@my-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", False),
    ("https://my-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com:8443/mcp", False),
    ("https://my-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/other", False),
    ("https://evil.example.com/mcp", False),
])
def test_gateway_url_allowlist(url, ok, monkeypatch):
    monkeypatch.setenv("GAC_GATEWAY_REGION", "us-east-1")
    if ok:
        assert relay.gateway_url(url) == url
    else:
        with pytest.raises(relay.RelayConfigError) as e:
            relay.gateway_url(url)
        assert url not in str(e.value)


def test_tool_prefix_validation(monkeypatch):
    monkeypatch.setenv("GAC_GATEWAY_TOOL_PREFIX", "Bad-Prefix")
    with pytest.raises(relay.RelayConfigError):
        relay.tool_prefix()


class _Item:
    def __init__(self, text):
        self.text = text


class _Res:
    def __init__(self, text, is_error=False):
        self.content = [_Item(text)]
        self.isError = is_error


class _Session:
    def __init__(self, text, tools=("gac___draw_diagram",)):
        self.text, self.tools_ = text, tools
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return _Res(self.text)

    async def list_tools(self):
        class T:
            def __init__(self, n):
                self.name = n

        class L:
            pass
        lst = L()
        lst.tools = [T(n) for n in self.tools_]
        return lst


def _factory(session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def f():
        yield session
    return f


def test_bridge_gateway_mode_writes(local_catalog, tmp_path, monkeypatch):
    envelope = json.loads(mcp_rt.draw_diagram(REQ, include_artifacts=True))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(ws))
    s = _Session(json.dumps(envelope))
    text = asyncio.run(bridge.draw_via_gateway(REQ, session_factory=_factory(s))).text
    assert s.calls[0][0] == "gac___draw_diagram" and s.calls[0][1]["include_artifacts"] is True
    assert "Saved locally:" in text
    assert (ws / "outputs" / "diagrams" / "private-request-path" / "private-request-path.svg").exists()


def test_bridge_gateway_non_json_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(tmp_path))
    text = asyncio.run(bridge.draw_via_gateway("x", session_factory=_factory(_Session("blocked text")))).text
    assert text == "blocked text" and list(tmp_path.iterdir()) == []


def test_bridge_tools_filter(monkeypatch):
    monkeypatch.setenv("GAC_BRIDGE_TOOLS", "draw_diagram")
    assert bridge.enabled_tools() == ["draw_diagram"]
    monkeypatch.delenv("GAC_BRIDGE_TOOLS")
    assert bridge.enabled_tools() == ["ask_companion", "companion_kb", "draw_diagram"]
    monkeypatch.setenv("GAC_BRIDGE_UPSTREAM", "gateway")
    assert bridge.upstream() == "gateway"


@pytest.mark.parametrize("tools,code", [(("gac___draw_diagram", "gac___ask_companion"), 0),
                                        (("gac___ask_companion",), 1)])
def test_check_gateway_tools(tools, code, capsys):
    assert bridge.check_gateway_tools(_factory(_Session("", tools))) == code


def test_build_draw_result_runtime_text_only():
    r = bridge.build_draw_result({"response": "hello", "gated": True})
    assert r.text == "hello" and r.declined is True and not r.saved and not r.refused


# -- CLI (item 12) ------------------------------------------------------------------------

import main as cli  # noqa: E402


@pytest.fixture
def cli_repo(local_catalog, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cli, "_REPO", repo)
    return repo


def test_cli_diagram_writes(cli_repo, capsys):
    assert cli.main(["diagram", REQ]) == 0
    out = capsys.readouterr().out
    d = cli_repo / "outputs" / "diagrams" / "private-request-path"
    assert "Saved locally:" in out and (d / "private-request-path.drawio").exists()
    assert cli.main(["diagram", REQ]) == 0
    assert "already exists" in capsys.readouterr().out
    assert cli.main(["diagram", REQ, "--overwrite", "--name", "x-1", "--out", "outputs/alt"]) == 0
    assert (cli_repo / "outputs" / "alt" / "x-1" / "x-1.svg").exists()


def test_cli_ask_routed_draw_writes(cli_repo, capsys):
    assert cli.main(["ask", "show our KB ingestion"]) == 0
    assert (cli_repo / "outputs" / "diagrams" / "kb-ingestion" / "kb-ingestion.svg").exists()


def test_cli_icons_absent_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GAC_DIAGRAM_ASSETS_DIR", raising=False)
    monkeypatch.setattr(A, "LOCAL_ASSETS_DIR", tmp_path / "none")
    monkeypatch.setattr(cli, "_REPO", tmp_path)
    # exit 4 since 3.4.0: a gated decline (no file produced); it was 0 before
    assert cli.main(["diagram", REQ]) == 4
    assert "docs/DEPLOY.md" in capsys.readouterr().out and not (tmp_path / "outputs").exists()


def test_cli_check_facts_and_status(capsys):
    assert cli.main(["diagram", "--check-facts"]) == 0
    assert "OK: nothing flagged." in capsys.readouterr().out
    cli.main(["status"])
    assert "diagram: enabled" in capsys.readouterr().out


def test_cli_diagram_fetch_fake_relay(cli_repo, monkeypatch, capsys):
    import mcp_bridge as mb
    envelope = json.loads(mcp_rt.draw_diagram(REQ, include_artifacts=True))

    async def fake(prompt, environment="dev", title="", name="", out_dir="", overwrite=False, **kw):
        return mb.build_draw_result(json.dumps(envelope), out_dir=out_dir, overwrite=overwrite)
    monkeypatch.setattr(mb, "draw_via_gateway", fake)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(cli_repo))
    assert cli.main(["diagram-fetch", REQ]) == 0
    assert "Saved locally:" in capsys.readouterr().out


def test_cli_diagram_fetch_runtime_upstream(cli_repo, monkeypatch, capsys):
    """--upstream runtime goes through the bridge's runtime path, never the gateway relay."""
    import mcp_bridge as mb
    envelope = json.loads(mcp_rt.draw_diagram(REQ, include_artifacts=True))
    calls = {}

    class _Client:
        def invoke(self, prompt, environment="dev", **kw):
            calls.update(kw)
            return envelope

    async def no_gateway(*a, **kw):
        raise AssertionError("gateway relay used for --upstream runtime")
    monkeypatch.setattr(mb, "build_client", lambda *a, **k: _Client())
    monkeypatch.setattr(mb, "draw_via_gateway", no_gateway)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(cli_repo))
    assert cli.main(["diagram-fetch", "--upstream", "runtime", "--overwrite", REQ]) == 0
    assert "Saved locally:" in capsys.readouterr().out
    assert calls["action"] == "diagram" and calls["include_artifacts"] is True


E2E = "draw gac pvt architecture end to end"
DECLINE_REQ = "how does memory get written"


def _upstream(monkeypatch, upstream, mutate=None):
    """Answer diagram-fetch from the real runtime face for `upstream` (the gateway relays the
    MCP runtime's draw_diagram text; the runtime path returns the /invocations envelope)."""
    import diagram_relay
    import mcp_bridge as mb

    def maybe(env):
        if mutate is not None:
            mutate(env)
        return env
    if upstream == "gateway":
        async def call(tool, args, **kw):
            assert tool == "draw_diagram"
            a = {k: v for k, v in args.items() if k != "environment"}
            return json.dumps(maybe(json.loads(mcp_rt.draw_diagram(**a))))
        monkeypatch.setattr(diagram_relay, "call_gateway_tool", call)
    else:
        class _Client:
            def invoke(self, prompt, environment="dev", **kw):
                return maybe(rt.handle_invocation(dict(kw, prompt=prompt,
                                                       environment=environment)))
        monkeypatch.setattr(mb, "build_client", lambda *a, **k: _Client())


def _fetch(capsys, upstream, req):
    code = cli.main(["diagram-fetch", "--upstream", upstream, req])
    return code, capsys.readouterr().out


@pytest.mark.parametrize("upstream", ["gateway", "runtime"])
def test_cli_diagram_fetch_decline_exit_4(cli_repo, monkeypatch, capsys, upstream):
    _upstream(monkeypatch, upstream)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(cli_repo))
    code, out = _fetch(capsys, upstream, DECLINE_REQ)
    assert code == 4
    assert out.startswith("[diagram] I can't draw this from the knowledge base")
    assert "Saved locally" not in out and not (cli_repo / "outputs").exists()


@pytest.mark.parametrize("upstream", ["gateway", "runtime"])
def test_cli_diagram_fetch_saves_four_views_exit_0(cli_repo, monkeypatch, capsys, upstream):
    _upstream(monkeypatch, upstream)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(cli_repo))
    code, out = _fetch(capsys, upstream, E2E)
    assert code == 0 and out.count("Saved locally:") == 4
    folders = [p for p in (cli_repo / "outputs" / "diagrams").iterdir() if p.is_dir()]
    assert len(folders) == 4


@pytest.mark.parametrize("upstream", ["gateway", "runtime"])
def test_cli_diagram_fetch_refused_view_exit_1(cli_repo, monkeypatch, capsys, upstream):
    def tamper(env):
        env["diagram_views"][-1]["artifacts"][0]["sha256"] = "0" * 64
    _upstream(monkeypatch, upstream, tamper)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(cli_repo))
    code, out = _fetch(capsys, upstream, E2E)
    assert code == 1 and out.count("Saved locally:") == 3 and "failed its sha256 check" in out


def test_mcp_result_shapes_decline_and_four_views(local_catalog):
    out = json.loads(mcp_rt.draw_diagram(DECLINE_REQ, include_artifacts=True))
    assert set(out) == {"response"}
    out = json.loads(mcp_rt.draw_diagram(E2E, include_artifacts=True))
    assert set(out) == {"response", "diagram", "diagram_views"} and len(out["diagram_views"]) == 3
    names = [out["diagram"]["name"]] + [b["name"] for b in out["diagram_views"]]
    assert len(set(names)) == 4
    plain = json.loads(mcp_rt.draw_diagram(E2E))
    assert set(plain) == {"response", "diagram_manifest"}
    assert len(plain["diagram_manifest"]["views"]) == 4


def test_invocations_views_and_planner(local_catalog):
    out = rt.handle_invocation({"prompt": E2E, "action": "diagram", "include_artifacts": True,
                                "planner": "deterministic"})
    assert set(out) == {"response", "gated", "diagram", "diagram_views"}
    assert len(out["diagram_views"]) == 3
    assert "Diagram planner: deterministic" in out["response"]
    one = rt.handle_invocation({"prompt": REQ, "action": "diagram", "include_artifacts": True,
                                "planner": "bedrock"})  # not a downgrade: ignored
    assert set(one) == {"response", "gated", "diagram"}
    plain = rt.handle_invocation({"prompt": E2E, "action": "diagram"})
    assert len(plain["diagram_manifest"]["views"]) == 4


def test_cli_diagram_fetch_runtime_unconfigured(cli_repo, monkeypatch, capsys):
    for k in ("GAC_RUNTIME_ARN", "GAC_COGNITO_USER_POOL_ID", "GAC_COGNITO_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["diagram-fetch", "--upstream", "runtime", REQ]) == 2
    assert "Not fetched:" in capsys.readouterr().out


def test_cli_diagram_assets(tmp_path, monkeypatch, capsys):
    from diagram_fixtures import fixture_zip_bytes
    z = tmp_path / "icons.zip"
    z.write_bytes(fixture_zip_bytes())
    repo = tmp_path / "r"
    (repo / "agentcore" / "diagram-icons").mkdir(parents=True)
    monkeypatch.setattr(cli, "_REPO", repo)
    assert cli.main(["diagram-assets", "--zip", str(z)]) == 0
    assert (repo / ".diagram-assets" / "catalog.json").exists()
    assert cli.main(["diagram-assets", "--zip", str(z), "--sha256", "0" * 64]) == 1
