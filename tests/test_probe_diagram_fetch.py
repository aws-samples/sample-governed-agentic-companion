"""`./run.sh diagram-fetch --via-probe <function>` (Change 2, laptop side).

End to end without AWS: a mocked gateway answers the REAL probe function (loaded from
agentcore/probe/private_access_probe.py), a fake Lambda invoker hands the probe's result to the
helper, and the helper saves the files through frontdoor/diagram_writer.py, the same writer the
bridge uses (sha256 check, traversal refusal, no overwrite).
"""
import hashlib
import json
import socket
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "frontdoor", ROOT / "tests"):
    sys.path.insert(0, str(p))

import main as cli  # noqa: E402
import cognito_token  # noqa: E402
import probe_fetch  # noqa: E402
from test_private_access_probe import (  # noqa: E402
    TOKEN, _Ctx, _install, _load, _resp, diagram_answer, diagram_bundle)

FUNCTION = "gactest-private-access-probe"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("real network connection attempted")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.80.1.10", port))])


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "ws"
    w.mkdir()
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(w))
    monkeypatch.delenv("GAC_DIAGRAM_LOCAL_DIR", raising=False)
    monkeypatch.setenv("GAC_PROBE_REGION", "ap-southeast-2")
    monkeypatch.setenv("GAC_COGNITO_PASSWORD", "from-env")
    monkeypatch.setattr(cognito_token.CognitoTokenProvider, "get_token", lambda self: TOKEN)
    # the probe path checks boto3 is importable before prompting (the invoker itself is faked)
    monkeypatch.setitem(sys.modules, "boto3", sys.modules.get("boto3") or types.ModuleType("boto3"))
    return w


def _probe(monkeypatch, answer, sent=None):
    """A Lambda invoker that runs the real probe handler against the mocked gateway."""
    mod = _load(monkeypatch)
    _install(mod, answer)

    def invoker(function, payload):
        event = json.loads(payload)
        if sent is not None:
            sent.append((function, sorted(event)))
        return 200, None, json.dumps(mod.lambda_handler(event, _Ctx())).encode("utf-8")
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: invoker)
    return mod


def _run(capsys, *argv, request="draw our private network path"):
    code = cli.main(["diagram-fetch", request, "--via-probe", FUNCTION, *argv])
    out = capsys.readouterr()
    return code, out.out + out.err


def _clean(text):
    assert "Traceback" not in text and TOKEN not in text and "from-env" not in text


def test_via_probe_writes_verified_files(ws, monkeypatch, capsys):
    bundle = diagram_bundle()
    sent = []
    _probe(monkeypatch, diagram_answer(bundle), sent)
    code, text = _run(capsys)
    assert code == 0
    assert sent == [(FUNCTION, ["diagram", "token"])]
    d = ws / "outputs" / "diagrams" / "probe-test"
    for a in bundle["artifacts"]:
        data = (d / a["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == a["sha256"] and len(data) == a["bytes"]
    assert (ws / "outputs" / "diagrams" / ".gitignore").read_text() == "*\n"
    saved = [ln for ln in text.splitlines() if ln.endswith("(sha256 verified)")]
    assert sorted(saved) == sorted(f"- outputs/diagrams/probe-test/{a['file']} (sha256 verified)"
                                   for a in bundle["artifacts"])
    assert "Saved locally:" in text
    assert "Quality score: 92.5 (threshold 85.0, pass)" in text
    assert str(ws) not in text and '"content"' not in text
    _clean(text)


def test_via_probe_tampered_checksum_refused(ws, monkeypatch, capsys):
    _probe(monkeypatch, diagram_answer(diagram_bundle(tamper=True)))
    code, text = _run(capsys)
    assert code == 1
    assert "Not saved: probe-test.svg failed its sha256 check." in text
    assert "Quality score" not in text
    assert not (ws / "outputs" / "diagrams" / "probe-test").exists()
    _clean(text)


def test_via_probe_no_overwrite(ws, monkeypatch, capsys):
    _probe(monkeypatch, diagram_answer(diagram_bundle()))
    assert _run(capsys)[0] == 0
    code, text = _run(capsys)
    assert code == 1 and "already exists" in text and "overwrite=true" in text
    assert _run(capsys, "--overwrite")[0] == 0


def test_via_probe_path_traversal_refused(ws, monkeypatch, capsys):
    bundle = diagram_bundle()
    bundle["name"] = "../escape"
    _probe(monkeypatch, diagram_answer(bundle))
    code, text = _run(capsys)
    assert code == 1 and "not a valid file name" in text
    assert not (ws.parent / "escape").exists()


def test_via_probe_gateway_denied_exit_3(ws, monkeypatch, capsys):
    deny = json.dumps({"jsonrpc": "2.0", "id": 0,
                       "error": {"code": -32002, "message": "Authorization error - Insufficient permissions"}})
    _probe(monkeypatch, lambda req: _resp(req, 403, deny))
    code, text = _run(capsys)
    assert code == 3
    assert text.strip() == ("Not fetched: gateway (via the probe) denied the request (HTTP 403: "
                            "Authorization error - Insufficient permissions)")
    assert not (ws / "outputs").exists()
    _clean(text)


def test_via_probe_lambda_access_denied_exit_3(ws, monkeypatch, capsys):
    class ClientError(Exception):
        response = {"Error": {"Code": "AccessDeniedException", "Message": "User arn:aws:sts::1:x"},
                    "ResponseMetadata": {"HTTPStatusCode": 403}}

    def invoker(function, payload):
        raise probe_fetch._mapped(ClientError())
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: invoker)
    code, text = _run(capsys)
    assert code == 3
    assert text.strip() == "Not fetched: probe denied the request (HTTP 403: AccessDeniedException)"
    assert "arn:aws" not in text


def test_via_probe_function_missing_is_config(ws, monkeypatch, capsys):
    class ClientError(Exception):
        response = {"Error": {"Code": "ResourceNotFoundException"}, "ResponseMetadata": {"HTTPStatusCode": 404}}

    def invoker(function, payload):
        raise probe_fetch._mapped(ClientError())
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: invoker)
    code, text = _run(capsys)
    assert code == 2 and "probe function was not found" in text


@pytest.mark.parametrize("function,request_text", [
    ("bad name!", "draw it"), ("x" * 65, "draw it"), (FUNCTION, "z" * 501), (FUNCTION, "a\nb")])
def test_via_probe_local_bounds(ws, monkeypatch, capsys, function, request_text):
    called = []
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: called.append(1))
    code = cli.main(["diagram-fetch", request_text, "--via-probe", function])
    out = capsys.readouterr().out
    assert code == 2 and out.startswith("Not fetched: ") and not called


def test_via_probe_oversize_response_refused(ws, monkeypatch, capsys):
    monkeypatch.setattr(probe_fetch, "_boto_invoker",
                        lambda region: lambda f, p: (200, None, b"x" * (probe_fetch.RESPONSE_CAP + 1)))
    code, text = _run(capsys)
    assert code == 3 and "over the 6 MB limit" in text
    assert not (ws / "outputs").exists()


def test_via_probe_function_error(ws, monkeypatch, capsys):
    monkeypatch.setattr(probe_fetch, "_boto_invoker",
                        lambda region: lambda f, p: (200, "Unhandled", b'{"errorMessage": "x"}'))
    code, text = _run(capsys)
    assert code == 3 and "function error Unhandled" in text


def test_via_probe_missing_boto3_before_password_prompt(ws, monkeypatch, capsys):
    # Review DEPS-1: no password is asked for when the Lambda call could not run anyway.
    from upstream_errors import BOTO3_MISSING
    monkeypatch.setitem(sys.modules, "boto3", None)
    asked = []
    monkeypatch.setattr(probe_fetch, "ensure_password", lambda *a, **k: asked.append(1))
    code, text = _run(capsys)
    assert code == 2 and asked == []
    assert f"Not fetched: {BOTO3_MISSING}" in text
    _clean(text)


def test_password_prompt_never_printed(monkeypatch, capsys):
    monkeypatch.setenv("GAC_COGNITO_PASSWORD", "")  # records the prior state for teardown
    monkeypatch.delenv("GAC_COGNITO_PASSWORD")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    typed = "typed-value"
    probe_fetch.ensure_password(prompt=lambda msg: typed)
    import os
    assert os.environ.get("GAC_COGNITO_PASSWORD") == typed
    out = capsys.readouterr()
    assert typed not in out.out + out.err


def _stub_boto(monkeypatch, invoke):
    """Fake boto3 + botocore.config (neither is a test dependency) capturing the client kwargs."""
    import types
    captured = {}

    class Config:
        def __init__(self, **kw):
            self.kw = kw

    class _Client:
        def invoke(self, **kw):
            return invoke(**kw)

    def client(service, **kw):
        captured["service"], captured["kw"] = service, kw
        return _Client()
    botocore = types.ModuleType("botocore")
    config_mod = types.ModuleType("botocore.config")
    config_mod.Config = Config
    botocore.config = config_mod
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", config_mod)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=client))
    return captured


def test_boto_client_waits_past_function_timeout_without_retries(monkeypatch):
    import io
    calls = []

    def invoke(**kw):
        calls.append(kw)
        return {"StatusCode": 200, "Payload": io.BytesIO(b'{"ok": true}')}
    captured = _stub_boto(monkeypatch, invoke)
    status, ferr, body = probe_fetch._boto_invoker("ap-southeast-2")(FUNCTION, b"{}")
    assert (status, ferr, body) == (200, None, b'{"ok": true}') and len(calls) == 1
    assert captured["service"] == "lambda" and captured["kw"]["region_name"] == "ap-southeast-2"
    cfg = captured["kw"]["config"].kw
    assert cfg["retries"] == {"total_max_attempts": 1}
    assert cfg["read_timeout"] > probe_fetch.PROBE_FUNCTION_TIMEOUT_S
    assert cfg["connect_timeout"] == probe_fetch.LAMBDA_CONNECT_TIMEOUT_S


def test_probe_function_timeout_matches_terraform():
    import re
    tf = (ROOT / "agentcore" / "terraform" / "probe.tf").read_text(encoding="utf-8")
    assert re.search(r"^\s*timeout\s*=\s*(\d+)", tf, re.M).group(1) == str(
        probe_fetch.PROBE_FUNCTION_TIMEOUT_S)


def test_via_probe_read_timeout_is_not_a_network_error(ws, monkeypatch, capsys):
    class ReadTimeoutError(Exception):
        pass
    calls = []

    def invoke(**kw):
        calls.append(1)
        raise ReadTimeoutError("Read timeout on endpoint URL: https://lambda.example/" + TOKEN)
    _stub_boto(monkeypatch, invoke)
    code, text = _run(capsys)
    assert code == 3 and len(calls) == 1
    assert text.strip() == (f"Not fetched: probe did not answer in time (no answer within "
                            f"{probe_fetch.LAMBDA_READ_TIMEOUT_S} s; check the probe's logs)")
    assert "not reachable" not in text and "https://" not in text
    assert not (ws / "outputs").exists()
    _clean(text)


# ---- decline, withheld, multi-view (design D7.2, D7.4; AC 11, AC 12) ------------------------

DECLINE = ("[diagram] I can't draw this from the knowledge base, so no file was produced.\n"
           "Missing: no diagram facts mention \"memory\"")
E2E = "draw gac pvt architecture end to end"


def test_via_probe_gated_decline_exit_4(ws, monkeypatch, capsys):
    _probe(monkeypatch, diagram_answer(None, response_text=DECLINE))
    code, text = _run(capsys, request="how does memory get written")
    assert code == 4
    assert text.strip() == DECLINE
    assert "Saved locally" not in text and "Not fetched" not in text
    assert not (ws / "outputs").exists()
    _clean(text)


def test_via_probe_withheld_artefacts_stay_exit_3(ws, monkeypatch, capsys):
    _probe(monkeypatch, diagram_answer(diagram_bundle(extra_text=TOKEN)))
    code, text = _run(capsys)
    assert code == 3
    assert "artefacts withheld: token-shaped content in the result" in text
    assert not (ws / "outputs").exists()
    _clean(text)


def test_decline_needs_every_condition():
    ok_steps = [{"step": "initialize", "status": 200}, {"step": "x", "status": 202}]
    base = {"ok": False, "error": probe_fetch.NO_ARTEFACTS, "steps": ok_steps,
            "result": {"response": "declined"}}
    assert probe_fetch._is_decline(base)
    assert not probe_fetch._is_decline(dict(base, error="artefacts withheld: token-shaped "
                                                       "content in the result"))
    assert not probe_fetch._is_decline(dict(base, steps=ok_steps + [{"status": 403}]))
    assert not probe_fetch._is_decline(dict(base, steps=[{"status": "skipped_deadline"}]))
    assert not probe_fetch._is_decline(dict(base, steps=[{"status": True}]))
    assert not probe_fetch._is_decline(dict(base, result={"response": 1}))
    assert not probe_fetch._is_decline(dict(base, result={"response": "x", "diagram": None}))
    assert not probe_fetch._is_decline(dict(base, result=None))


@pytest.fixture(scope="module")
def e2e_inner(tmp_path_factory):
    """The real MCP runtime result for the end-to-end prompt (4 views, fixture icons)."""
    pytest.importorskip("defusedxml")
    sys.path.insert(0, str(ROOT / "agentcore" / "app" / "mcp_runtime"))
    import os
    import runtime_mcp_entrypoint as mrt
    from diagram_fixtures import fixture_assets_dir
    from agents.diagram import assets as A
    saved, env = A.LOCAL_ASSETS_DIR, os.environ.pop("GAC_DIAGRAM_ASSETS_DIR", None)
    A.LOCAL_ASSETS_DIR = fixture_assets_dir(tmp_path_factory.mktemp("probe-icons"))
    try:
        inner = json.loads(mrt.draw_diagram(E2E, include_artifacts=True))
    finally:
        A.LOCAL_ASSETS_DIR = saved
        if env is not None:
            os.environ["GAC_DIAGRAM_ASSETS_DIR"] = env
    assert len(inner["diagram_views"]) == 3
    return inner


def _inner_answer(inner):
    def answer(req):
        body = json.loads(req.data.decode("utf-8"))
        m = body.get("method")
        if m == "initialize":
            return _resp(req, 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}))
        if m == "notifications/initialized":
            return _resp(req, 202, "")
        text = json.dumps(inner, separators=(",", ":"))
        return _resp(req, 200, json.dumps({"jsonrpc": "2.0", "id": 3, "result": {
            "content": [{"type": "text", "text": text}], "isError": False}}))
    return answer


def _check_views(ws, bundles):
    for b in bundles:
        d = ws / "outputs" / "diagrams" / b["name"]
        for a in b["artifacts"]:
            data = (d / a["file"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == a["sha256"] and len(data) == a["bytes"]


def test_via_probe_saves_all_four_views(ws, monkeypatch, capsys, e2e_inner):
    _probe(monkeypatch, _inner_answer(e2e_inner))
    code, text = _run(capsys, request=E2E)
    assert code == 0
    bundles = [e2e_inner["diagram"]] + e2e_inner["diagram_views"]
    _check_views(ws, bundles)
    folders = sorted(p.name for p in (ws / "outputs" / "diagrams").iterdir() if p.is_dir())
    assert folders == sorted(b["name"] for b in bundles) and len(folders) == 4
    assert text.count("Saved locally:") == 4 and "not delivered by this probe version" not in text
    _clean(text)


def test_via_probe_one_refused_view_others_land(ws, monkeypatch, capsys, e2e_inner):
    import copy
    inner = copy.deepcopy(e2e_inner)
    inner["diagram_views"][1]["artifacts"][0]["sha256"] = "0" * 64
    _probe(monkeypatch, _inner_answer(inner))
    code, text = _run(capsys, request=E2E)
    assert code == 1 and "failed its sha256 check" in text
    bad = inner["diagram_views"][1]["name"]
    assert not (ws / "outputs" / "diagrams" / bad).exists()
    _check_views(ws, [inner["diagram"], inner["diagram_views"][0], inner["diagram_views"][2]])


def test_older_probe_delivers_view_1_and_says_so(ws, monkeypatch, capsys, e2e_inner):
    mod = _load(monkeypatch)
    _install(mod, _inner_answer(e2e_inner))

    def old_probe(function, payload):
        doc = mod.lambda_handler(json.loads(payload), _Ctx())
        doc["result"].pop("diagram_views")  # an older probe drops the extra views
        return 200, None, json.dumps(doc).encode("utf-8")
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: old_probe)
    code, text = _run(capsys, request=E2E)
    assert code == 0
    _check_views(ws, [e2e_inner["diagram"]])
    assert len([p for p in (ws / "outputs" / "diagrams").iterdir() if p.is_dir()]) == 1
    assert "3 more views were listed but not delivered by this probe version" in text


def test_probe_lambda_caps_extra_views(monkeypatch, e2e_inner):
    mod = _load(monkeypatch)
    inner = dict(e2e_inner, diagram_views=e2e_inner["diagram_views"] + [e2e_inner["diagram"]])
    _install(mod, _inner_answer(inner))
    res = mod.lambda_handler({"token": TOKEN, "diagram": E2E}, _Ctx())
    assert res["ok"] is False and "diagram" not in (res["result"] or {})
    assert res["error"] == "diagram_views is not a list of at most 3 views"


@pytest.mark.parametrize("extra", [["--upstream", "gateway"], ["--upstream", "runtime"]])
def test_via_probe_rejects_upstream(ws, monkeypatch, capsys, extra):
    called = []
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: called.append(1))
    code, text = _run(capsys, *extra)
    assert code == 2 and not called
    assert text.strip() == ("Not fetched: --via-probe always uses the gateway and environment "
                            "'dev'; drop --upstream and --environment")


def test_via_probe_rejects_environment(ws, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(probe_fetch, "_boto_invoker", lambda region: called.append(1))
    code = cli.main(["--environment", "uat", "diagram-fetch", "draw it", "--via-probe", FUNCTION])
    out = capsys.readouterr().out
    assert code == 2 and not called and "drop --upstream and --environment" in out
