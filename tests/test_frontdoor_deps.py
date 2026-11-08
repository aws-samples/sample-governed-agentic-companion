"""Front-door dependencies (design D10, M9; AC 13, AC 14).

A missing boto3, requests or mcp gives ONE install line,
`<pkg> is not installed; run: pip install -r requirements-frontdoor.txt`, as a config error
(exit 2) before any network work, never a traceback or a bare ModuleNotFoundError. Missing
modules are simulated with monkeypatch.setitem(sys.modules, name, None). No AWS, no network.
"""
import asyncio
import fnmatch
import re
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "frontdoor"):
    sys.path.insert(0, str(p))

import main as cli  # noqa: E402
import cognito_token  # noqa: E402
import diagram_relay as relay  # noqa: E402
import probe_fetch  # noqa: E402
import runtime_client as rc  # noqa: E402
from upstream_errors import BOTO3_MISSING, MCP_MISSING, REQUESTS_MISSING  # noqa: E402

INSTALL = "pip install -r requirements-frontdoor.txt"
REGION = "us-east-1"
GW_URL = "https://examplegw-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
ARN = "arn:aws:bedrock-agentcore:us-east-1:" + "1" * 12 + ":runtime/example-abc123"
FUNCTION = "gactest-private-access-probe"
# Placeholders assembled at run time (not credentials), so secret scanners stay quiet.
PW_STANDIN = "-".join(("pw", "placeholder"))
CS_STANDIN = "-".join(("cs", "placeholder"))


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real network connection attempted")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Every front-door setting present (placeholders only), so only the missing package fails."""
    ws = tmp_path / "ws"
    ws.mkdir()
    values = {"GAC_DIAGRAM_WORKSPACE_ROOT": str(ws), "GAC_GATEWAY_URL": GW_URL,
              "GAC_GATEWAY_REGION": REGION, "GAC_RUNTIME_ARN": ARN, "GAC_RUNTIME_REGION": REGION,
              "GAC_PROBE_REGION": REGION, "GAC_COGNITO_REGION": REGION,
              "GAC_COGNITO_USER_POOL_ID": "us-east-1_example", "GAC_COGNITO_CLIENT_ID": "client",
              "GAC_COGNITO_USERNAME": "user", "GAC_COGNITO_PASSWORD": PW_STANDIN,
              "GAC_COGNITO_CLIENT_SECRET": CS_STANDIN}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GAC_AGENTCORE_ENDPOINT", raising=False)
    return ws


def _fetch(capsys, *argv):
    code = cli.main(["diagram-fetch", "draw our private network path", *argv])
    out = capsys.readouterr()
    return code, (out.out + out.err).strip()


def _no_mint(monkeypatch):
    minted = []
    monkeypatch.setattr(cognito_token.CognitoTokenProvider, "get_token",
                        lambda self: minted.append(1) or "t")
    return minted


def test_messages_share_one_install_line():
    for pkg, msg in (("boto3", BOTO3_MISSING), ("requests", REQUESTS_MISSING),
                     ("mcp", MCP_MISSING)):
        assert msg == f"{pkg} is not installed; run: {INSTALL}"


# ---- boto3 ---------------------------------------------------------------------------------

def test_boto3_missing_cognito_get_token(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(cognito_token.CognitoConfigError) as e:
        cognito_token.CognitoTokenProvider().get_token()
    assert str(e.value) == "boto3 is not installed; run: pip install -r requirements-frontdoor.txt"


def test_boto3_missing_via_probe_exit_2(env, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "boto3", None)
    code, text = _fetch(capsys, "--via-probe", FUNCTION)
    assert code == 2 and text == f"Not fetched: {BOTO3_MISSING}"
    assert "Traceback" not in text and PW_STANDIN not in text


def test_boto3_missing_probe_invoker(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(probe_fetch.ProbeConfigError) as e:
        probe_fetch._boto_invoker(REGION)(FUNCTION, b"{}")
    assert str(e.value) == BOTO3_MISSING


# ---- requests ------------------------------------------------------------------------------

def test_requests_missing_runtime_client(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)

    class Tok:
        def get_token(self):
            return "t"

        def invalidate(self):
            pass
    with pytest.raises(rc.RuntimeConfigError) as e:
        rc.CompanionRuntimeClient(Tok()).invoke("hello")
    assert str(e.value) == REQUESTS_MISSING


def test_requests_missing_runtime_upstream_exit_2(env, monkeypatch, capsys):
    pytest.importorskip("anyio")
    _no_mint(monkeypatch)
    monkeypatch.setitem(sys.modules, "requests", None)
    code, text = _fetch(capsys, "--upstream", "runtime")
    assert code == 2 and text == f"Not fetched: {REQUESTS_MISSING}"


# ---- mcp -----------------------------------------------------------------------------------

def test_mcp_missing_gateway_upstream_exit_2_before_any_token(env, monkeypatch, capsys):
    pytest.importorskip("anyio")
    minted = _no_mint(monkeypatch)
    monkeypatch.setitem(sys.modules, "mcp", None)
    code, text = _fetch(capsys, "--upstream", "gateway")
    assert code == 2
    assert text == "Not fetched: mcp is not installed; run: pip install -r requirements-frontdoor.txt"
    assert minted == []  # raised before any network work
    assert not (env / "outputs").exists()


def test_mcp_missing_default_upstream_is_gateway(env, monkeypatch, capsys):
    pytest.importorskip("anyio")
    _no_mint(monkeypatch)
    monkeypatch.setitem(sys.modules, "mcp", None)
    assert _fetch(capsys) == (2, f"Not fetched: {MCP_MISSING}")


def test_mcp_missing_relay_raises_config_error(env, monkeypatch):
    minted = _no_mint(monkeypatch)
    monkeypatch.setitem(sys.modules, "mcp", None)
    for call in (lambda: relay.call_gateway_tool("draw_diagram", {}),
                 lambda: relay.list_gateway_tools()):
        with pytest.raises(relay.RelayConfigError) as e:
            asyncio.run(call())
        assert str(e.value) == MCP_MISSING
    assert minted == []


@pytest.mark.parametrize("module", ["anyio", "mcp_bridge"])
def test_cli_guards_bridge_imports(env, monkeypatch, capsys, module):
    monkeypatch.setitem(sys.modules, module, None)
    assert _fetch(capsys) == (2, f"Not fetched: {MCP_MISSING}")


def test_mcp_missing_bridge_gateway_draw_diagram(env, monkeypatch):
    """The bridge's gateway-upstream draw_diagram tool returns the install line as a clean
    isError result (the server object was built while mcp was importable)."""
    pytest.importorskip("mcp")
    import mcp_bridge as bridge
    from mcp import types as mt
    minted = _no_mint(monkeypatch)
    server = bridge.build_server("gateway", ["draw_diagram"])
    handler = server._mcp_server.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(method="tools/call", params=mt.CallToolRequestParams(
        name="draw_diagram", arguments={"prompt": "draw our private network path"}))
    for name in ("mcp.client.streamable_http", "mcp.shared._httpx_utils"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "mcp", None)
    res = asyncio.run(handler(req)).root
    assert res.isError is True
    assert res.content[0].text.endswith(f"Not fetched: {MCP_MISSING}")  # SDK adds a prefix
    assert minted == []


def test_bridge_safe_error_keeps_the_install_line():
    import mcp_bridge as bridge
    assert str(bridge.safe_error(relay.RelayConfigError(MCP_MISSING), "gateway")) == \
        f"Not fetched: {MCP_MISSING}"


def test_server_hints_name_the_install_line(monkeypatch):
    import mcp_bridge as bridge
    import gate_mcp_server
    for name in ("mcp", "mcp.server", "mcp.server.mcpserver", "mcp.server.fastmcp"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(SystemExit) as e:
        bridge._server_class()
    assert INSTALL in str(e.value)
    with pytest.raises(SystemExit) as e:
        gate_mcp_server.main()
    assert INSTALL in str(e.value)
    for path in (ROOT / "frontdoor" / "mcp_bridge.py", ROOT / "frontdoor" / "gate_mcp_server.py"):
        assert "mcp>=1.28.0,<2" not in path.read_text(encoding="utf-8")


# ---- requirements-frontdoor.txt ------------------------------------------------------------

PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?==[0-9][A-Za-z0-9.]*$")


def _pins(path: Path) -> dict:
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, version = line.partition("==")
        out[name.split("[")[0].lower()] = (line, version)
    return out


def test_frontdoor_requirements_exact_pins():
    path = ROOT / "requirements-frontdoor.txt"
    assert path.is_file()
    pins = _pins(path)
    assert set(pins) == {"pyyaml", "boto3", "botocore", "mcp", "httpx", "requests"}
    for line, _v in pins.values():
        assert PIN_RE.match(line), line
        assert not line.startswith("-") and ";" not in line and "@" not in line
    image = _pins(ROOT / "agentcore" / "app" / "mcp_runtime" / "requirements.txt")
    for name in ("boto3", "botocore", "mcp"):
        assert pins[name][1] == image[name][1], name
    header = path.read_text(encoding="utf-8")
    assert "Python >= 3.10" in header


def test_security_gate_glob_matches_frontdoor_requirements():
    gate = (ROOT / "scripts" / "security_gate.sh").read_text(encoding="utf-8")
    m = re.search(r"git -C \"\$ROOT\" ls-files '([^']+)'", gate)
    assert m, "the pip-audit loop over tracked requirements files moved"
    assert fnmatch.fnmatch("requirements-frontdoor.txt", m.group(1))


def test_requirements_points_to_frontdoor_file():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pip install -r requirements-frontdoor.txt" in text
