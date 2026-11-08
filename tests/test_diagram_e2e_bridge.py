"""Unit e2e: the bridge, through the runtime client's _post seam into the real
handle_invocation, writes the artefacts locally with matching checksums (acceptance 15, 35)."""

import asyncio
import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "tests", ROOT / "frontdoor", ROOT / "agentcore" / "app" / "orchestrator"):
    sys.path.insert(0, str(p))

pytest.importorskip("defusedxml")
pytest.importorskip("anyio")

import mcp_bridge as bridge  # noqa: E402
import runtime_entrypoint as rt  # noqa: E402
from diagram_fixtures import fixture_assets_dir  # noqa: E402
from runtime_client import CompanionRuntimeClient  # noqa: E402
from agents.diagram import assets as A  # noqa: E402


class Tokens:
    def get_token(self):
        return "t"

    def invalidate(self):
        pass


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("GAC_DIAGRAM_ASSETS_DIR", raising=False)
    monkeypatch.setattr(A, "LOCAL_ASSETS_DIR", fixture_assets_dir(tmp_path / "c"))
    monkeypatch.setenv("GAC_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:" + "1" * 12
                       + ":runtime/x")
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(ws))
    seen = {}

    def post(url, headers, data):
        payload = json.loads(data)
        seen["payload"] = payload
        envelope = rt.handle_invocation(payload)
        seen["envelope"] = envelope
        return 200, json.dumps(envelope)
    return ws, CompanionRuntimeClient(Tokens(), _post=post), seen


def test_bridge_writes_verified_files(setup):
    ws, client, seen = setup
    assert inspect.iscoroutinefunction(bridge.draw_via_runtime)
    text = asyncio.run(bridge.draw_via_runtime(client, "draw our private network path")).text
    assert seen["payload"]["action"] == "diagram" and seen["payload"]["include_artifacts"] is True
    arts = seen["envelope"]["diagram"]["artifacts"]
    d = ws / "outputs" / "diagrams" / "private-request-path"
    files = sorted(p.name for p in d.iterdir())
    expected = {"private-request-path.drawio", "private-request-path.svg",
                "private-request-path.report.json"}
    try:
        import resvg_py  # noqa: F401
        expected.add("private-request-path.png")
    except ImportError:
        pass
    assert set(files) == expected
    for a in arts:
        data = (d / a["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == a["sha256"] and len(data) == a["bytes"]
    saved = [ln[2:].split(" (sha256")[0] for ln in text.splitlines()
             if ln.endswith("(sha256 verified)")]
    # workspace-relative: the text never carries an absolute workstation path
    assert sorted(saved) == sorted(f"outputs/diagrams/private-request-path/{f}" for f in files)
    assert str(ws) not in text
    assert '"content"' not in text and "base64" not in text


E2E = "draw gac pvt architecture end to end"


def _verified(ws, bundle):
    d = ws / "outputs" / "diagrams" / bundle["name"]
    for a in bundle["artifacts"]:
        data = (d / a["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == a["sha256"] and len(data) == a["bytes"]


def test_bridge_saves_all_four_views(setup):
    ws, client, seen = setup
    res = asyncio.run(bridge.draw_via_runtime(client, E2E))
    env = seen["envelope"]
    assert set(env) == {"response", "gated", "diagram", "diagram_views"}
    bundles = [env["diagram"]] + env["diagram_views"]
    assert len(bundles) == 4 and len({b["name"] for b in bundles}) == 4
    for b in bundles:
        _verified(ws, b)
    assert res.declined is False and res.refused == []
    assert len(res.saved) == sum(len(b["artifacts"]) for b in bundles)
    assert res.text.count("Saved locally:") == 4
    assert str(ws) not in res.text and '"content"' not in res.text


def test_older_writer_saves_view_1_only(setup):
    """The wire format is additive: a bridge that only knows `diagram` (3.3.x) saves view 1."""
    ws, client, seen = setup
    from diagram_writer import write_artifacts
    result = client.invoke(E2E, action="diagram", include_artifacts=True)
    assert len(result["diagram_views"]) == 3
    write_artifacts(result["diagram"])  # what the 3.3.x build_draw_result did
    _verified(ws, result["diagram"])
    folders = [p.name for p in (ws / "outputs" / "diagrams").iterdir() if p.is_dir()]
    assert folders == [result["diagram"]["name"]]


def test_bridge_decline_has_no_saved_lines(setup):
    ws, client, seen = setup
    res = asyncio.run(bridge.draw_via_runtime(client, "how does memory get written"))
    assert set(seen["envelope"]) == {"response", "gated"}
    assert res.declined is True and res.saved == [] and res.refused == []
    assert res.text == seen["envelope"]["response"] and "Saved locally" not in res.text
    assert not (ws / "outputs").exists()


def test_bridge_one_refused_view_others_land(setup, monkeypatch):
    ws, client, seen = setup
    real = rt.handle_invocation

    def tampered(payload):
        env = real(payload)
        env["diagram_views"][0]["artifacts"][0]["sha256"] = "0" * 64
        return env
    monkeypatch.setattr(rt, "handle_invocation", tampered)
    res = asyncio.run(bridge.draw_via_runtime(client, E2E))
    env = seen["envelope"]
    bad = env["diagram_views"][0]["name"]
    assert res.refused == [bad] and "failed its sha256 check" in res.text
    assert not (ws / "outputs" / "diagrams" / bad).exists()
    for b in [env["diagram"]] + env["diagram_views"][1:]:
        _verified(ws, b)


def test_bridge_passes_planner_only_when_set(setup):
    ws, client, seen = setup
    asyncio.run(bridge.draw_via_runtime(client, "draw our private network path", overwrite=True))
    assert "planner" not in seen["payload"]
    asyncio.run(bridge.draw_via_runtime(client, "draw our private network path", overwrite=True,
                                        planner="deterministic"))
    assert seen["payload"]["planner"] == "deterministic"


@pytest.mark.parametrize("declared", [True, False])
def test_oversize_upstream_refused_unparsed(tmp_path, monkeypatch, declared):
    import runtime_client as RC
    monkeypatch.setenv("GAC_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:" + "1" * 12
                       + ":runtime/x")
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(ws))
    big = "{" + " " * (RC.UPSTREAM_CAP + 10) + "}"

    class Resp:
        status_code = 200
        headers = {"Content-Length": str(len(big))} if declared else {}

        def iter_content(self, n):
            for i in range(0, len(big), n):
                yield big[i:i + n].encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import types
    fake = types.ModuleType("requests")
    fake.post = lambda *a, **k: Resp()
    fake.ConnectionError = type("ConnectionError", (Exception,), {})
    fake.Timeout = type("Timeout", (Exception,), {})
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("parsed an oversize body"))
    client = CompanionRuntimeClient(Tokens())
    text = asyncio.run(bridge.draw_via_runtime(client, "draw x")).text
    assert "exceeded the size limit" in text
    assert list(ws.iterdir()) == []


def test_seam_oversize_refused(tmp_path, monkeypatch):
    import runtime_client as RC
    monkeypatch.setenv("GAC_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:" + "1" * 12
                       + ":runtime/x")
    client = CompanionRuntimeClient(Tokens(), _post=lambda *a: (200, "x" * (RC.UPSTREAM_CAP + 1)))
    assert "exceeded the size limit" in asyncio.run(bridge.draw_via_runtime(client, "draw x")).text
