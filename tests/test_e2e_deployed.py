"""scripts/e2e_deployed.py with stub transports (design D24, AC 28). No AWS, no network.

The fixture secret values (client secrets, passwords, tokens) are assembled at run time, and
every test that runs the harness searches the captured stdout/stderr and the written result
files for them.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import e2e_deployed as E  # noqa: E402

REGION = "us-east-1"
ACCOUNT = "1" * 12


def _secret(tag: str) -> str:
    return "fixture-" + tag + "-" + "x9" * 8


CLIENT_SECRET = _secret("client-secret")
M2M_SECRET = _secret("m2m-secret")
OTHER_SECRET = _secret("other-secret")
PASSWORDS = {"entitled-user": _secret("pw-entitled"), "u0-user": _secret("pw-u0"),
             "u1-user": _secret("pw-u1")}
SECRETS = [CLIENT_SECRET, M2M_SECRET, OTHER_SECRET, *PASSWORDS.values()]


def _token(name: str) -> str:
    return "tok" + name.replace("@", "at").replace("-", "") + "." + "q" * 24 + ".sig"


def _outputs(tmp_path, stack="public", **over):
    arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/"
    vals = {"aws_region": REGION, "network_access": stack,
            "gateway_url": f"https://gac-gw-abc.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp",
            "orchestrator_runtime_arn": arn + "gac_orch-AbC123",
            "mcp_runtime_arn": arn + "gac_mcp_runtime-XyZ789",
            "cognito_user_pool_id": f"{REGION}_AbCdEf123",
            "cognito_frontdoor_client_id": "frontdoorclient01",
            "cognito_m2m_client_id": "m2mclient01" if stack == "public" else "",
            "cognito_hosted_ui_domain": f"https://gac-auth.auth.{REGION}.amazoncognito.com",
            "memory_id": "gac_memory-Abc123",
            "private_access_probe_function_name": "gac-private-access-probe"
            if stack == "private" else ""}
    vals.update(over)
    path = tmp_path / f"outputs-{stack}.json"
    path.write_text(json.dumps({k: {"value": v, "type": "string", "sensitive": False}
                                for k, v in vals.items()}))
    return path


def _env(**over):
    env = {"GAC_COGNITO_USERNAME": "entitled-user", "GAC_E2E_UNENTITLED_USERNAME": "u0-user",
           "GAC_COGNITO_CLIENT_SECRET": CLIENT_SECRET, "GAC_E2E_M2M_CLIENT_SECRET": M2M_SECRET}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def _report(view_id, name, views, score=97.0):
    return {"topic": view_id, "quality": {"score": score, "pass": True, "findings": []},
            "plan": {"catalogue_sha256": "c" * 64, "planner": "deterministic",
                     "selected": ["n:b", "n:a"],
                     "views": [{"id": v, "name": n} for v, n in views]}}


class Stub:
    """Records every call; deny targets answer 401/403 unless told otherwise."""

    def __init__(self, allow=(), drift_door="", status=None):
        self.calls = []
        self.allow = set(allow)
        self.drift_door = drift_door
        self.status = status or {}

    def post(self, url, headers, body):
        auth = headers.get("Authorization")
        target = ("gateway" if "gateway.bedrock-agentcore" in url else
                  "mcp-runtime" if "mcp_runtime" in url else "orchestrator")
        self.calls.append(("post", target, auth))
        if (target, auth) in self.allow:
            return 200, {}, '{"result": {}}'
        status = self.status.get(target, 403)
        if status == 401:
            return 401, {"www-authenticate": "Bearer"}, ""
        return 403, {}, json.dumps({"jsonrpc": "2.0", "id": 1, "error": {
            "code": -32001, "message": E.NOT_ENTITLED + " " + (auth or "")}})

    def mint_user(self, cfg, client_id, secret, username, password):
        assert secret in (CLIENT_SECRET, OTHER_SECRET) and password == PASSWORDS[username]
        self.calls.append(("mint_user", username, client_id))
        return _token(f"{username}@{client_id}")

    def mint_client(self, cfg, client_id, secret):
        assert secret == M2M_SECRET
        self.calls.append(("mint_client", client_id))
        return _token("m2m")

    def probe(self, cfg, payload):
        self.calls.append(("probe", payload))
        if payload.get("anonymous") or "diagram" not in payload and payload.get("token") in (
                E.MALFORMED_TOKEN, _token("u0-user@frontdoorclient01"),
                _token("u1-user@frontdoorclient01")):
            if ("probe", payload.get("token")) in self.allow:
                return {"ok": True, "steps": [{"step": "tools/list", "status": 200}]}
            return {"ok": False, "steps": [{"step": "initialize", "status": 403,
                                            "body": "Authorization error - Insufficient permissions"}]}
        return {"ok": True, "steps": [{"step": "tools/call", "status": 200}]}

    def sigv4(self, cfg, check):
        self.calls.append(("sigv4", check))
        return False, "AccessDeniedException", True

    def ask(self, cfg, door, tool, prompt, token):
        self.calls.append(("ask", door, tool))
        return "probe ok" if door == "probe" else "answer\n  Gate status: ✓ PASS"

    def draw(self, cfg, door, prompt, out_dir, token, password):
        self.calls.append(("draw", door, prompt))
        if "memory" in prompt:
            return E.DoorResult(4, "decline text")
        views = [("private-request-path", "x-1-private-request-path"),
                 ("kb-ingestion", "x-2-kb-ingestion")]
        for vid, name in views:
            d = out_dir / name
            d.mkdir(parents=True, exist_ok=True)
            drift = "drift" if door == self.drift_door else ""
            for ext in ("drawio", "svg", "png"):
                (d / f"{name}.{ext}").write_text(f"{vid}-{ext}{drift}")
            (d / f"{name}.report.json").write_text(json.dumps(_report(vid, name, views)))
        return E.DoorResult(0, "rendered", 1234)


PROMPTS = [{"id": 2, "prompt": "show how a developer request reaches the knowledge base",
            "expect": "render", "views": ["private-request-path", "kb-ingestion"]},
           {"id": 4, "prompt": "how does memory get written", "expect": "decline", "views": []}]


def _run(tmp_path, stub, *extra, stack="public", env=None, outputs=None, prompts=None,
         monkeypatch=None):
    prompts_file = tmp_path / "prompts.yaml"
    prompts_file.write_text(json.dumps({"rows": prompts if prompts is not None else PROMPTS}))
    argv = ["--stack", stack, "--outputs", str(outputs or _outputs(tmp_path, stack)),
            "--out-dir", str(tmp_path / "out"), "--prompts", str(prompts_file), *extra]
    return E.main(argv, transport=stub, prompt=lambda msg: PASSWORDS[msg.split(" for ")[1]
                                                                      .split(" ")[0]],
                  env=env if env is not None else _env())


def _assert_no_secrets(capsys, tmp_path, stub):
    cap = capsys.readouterr()
    tokens = [_token(n) for n in ("entitled-user@frontdoorclient01", "u0-user@frontdoorclient01",
                                  "u1-user@frontdoorclient01", "m2m",
                                  "entitled-user@otherclient01")]
    written = "".join(p.read_text(errors="replace") for p in (tmp_path / "out").glob("e2e-*"))
    for value in SECRETS + tokens:
        assert value not in cap.out and value not in cap.err, "secret printed"
        assert value not in written, "secret written"
    return cap


@pytest.fixture(autouse=True)
def _no_password_env(monkeypatch):
    monkeypatch.delenv("GAC_COGNITO_PASSWORD", raising=False)


@pytest.mark.parametrize("env", [_env(GAC_E2E_UNENTITLED_USERNAME=None),
                                 _env(GAC_E2E_UNENTITLED_USERNAME="entitled-user")])
def test_u0_missing_or_equal_exits_2_before_any_request(tmp_path, capsys, env):
    stub = Stub()
    assert _run(tmp_path, stub, env=env) == 2
    assert stub.calls == []
    assert "GAC_E2E_UNENTITLED_USERNAME" in capsys.readouterr().out


def test_bad_output_shape_exits_2(tmp_path):
    stub = Stub()
    out = _outputs(tmp_path, gateway_url="https://evil.example.com/mcp")
    assert _run(tmp_path, stub, outputs=out) == 2 and stub.calls == []


def test_stack_mismatch_exits_2(tmp_path):
    stub = Stub()
    assert _run(tmp_path, stub, outputs=_outputs(tmp_path, "private")) == 2


def test_public_deny_first_token_table(tmp_path, capsys):
    stub = Stub()
    code = _run(tmp_path, stub, "--deny-only")
    assert code == 0
    posts = [c for c in stub.calls if c[0] == "post"]
    u0 = "Bearer " + _token("u0-user@frontdoorclient01")
    m2m = "Bearer " + _token("m2m")
    user = "Bearer " + _token("entitled-user@frontdoorclient01")
    assert posts == [
        ("post", "gateway", None), ("post", "orchestrator", None),
        ("post", "gateway", "Bearer " + E.MALFORMED_TOKEN),
        ("post", "orchestrator", "Bearer " + E.MALFORMED_TOKEN),
        ("post", "gateway", u0), ("post", "orchestrator", u0),
        ("post", "gateway", m2m), ("post", "orchestrator", m2m),
        ("post", "mcp-runtime", user)]
    assert not [c for c in stub.calls if c[0] in ("draw", "ask")]
    doc = json.loads((tmp_path / "out" / "e2e-public.json").read_text())
    not_run = {(r["test"], r["target"]) for r in doc["deny"] if r["verdict"] == "not-run"}
    assert {("D3b other group", "gateway"), ("D4b other flavor", "orchestrator")} <= not_run
    assert any("D3b other group" in n for n in doc["not_run"])
    assert all(r["verdict"] in ("denied", "not-run") for r in doc["deny"])
    md = (tmp_path / "out" / "e2e-public.md").read_text()
    assert "Not run:" in md and "D4b other flavor" in md
    _assert_no_secrets(capsys, tmp_path, stub)


def test_optional_users_run_when_configured(tmp_path, capsys):
    stub = Stub()
    env = _env(GAC_E2E_OTHERGROUP_USERNAME="u1-user", GAC_E2E_OTHER_CLIENT_SECRET=OTHER_SECRET)
    assert _run(tmp_path, stub, "--deny-only", "--other-client-id", "otherclient01",
                env=env) == 0
    auths = [c[2] for c in stub.calls if c[0] == "post"]
    assert "Bearer " + _token("u1-user@frontdoorclient01") in auths
    assert "Bearer " + _token("entitled-user@otherclient01") in auths
    _assert_no_secrets(capsys, tmp_path, stub)


@pytest.mark.parametrize("allowed", [("gateway", None),
                                     ("orchestrator", "Bearer " + _token("m2m"))])
def test_any_deny_200_is_stop_ship(tmp_path, capsys, allowed):
    stub = Stub(allow=[allowed])
    assert _run(tmp_path, stub) == 5
    assert not [c for c in stub.calls if c[0] in ("draw", "ask")]
    last = [c for c in stub.calls if c[0] == "post"][-1]
    assert last[1:] == allowed  # nothing ran after the allowed request
    cap = _assert_no_secrets(capsys, tmp_path, stub)
    assert "STOP-SHIP" in cap.out
    assert json.loads((tmp_path / "out" / "e2e-public.json").read_text())["exit"] == 5


def test_unidentified_denial_fails(tmp_path):
    class Bare(Stub):
        def post(self, url, headers, body):
            super().post(url, headers, body)
            return 403, {}, "no marker here"
    assert _run(tmp_path, Bare(), "--deny-only") == 1


def test_full_run_parity_and_outputs(tmp_path, capsys):
    stub = Stub()
    assert _run(tmp_path, stub) == 0
    draws = [c for c in stub.calls if c[0] == "draw"]
    assert {c[1] for c in draws} == set(E.DOORS["public"])
    asks = {(c[1], c[2]) for c in stub.calls if c[0] == "ask"}
    assert ("bridge-gateway", "companion_kb") in asks and ("bridge-runtime", "ask_companion") in asks
    doc = json.loads((tmp_path / "out" / "e2e-public.json").read_text())
    rec = {r["prompt_id"]: r for r in doc["prompts"]}
    art = rec[2]["artefacts"]["bridge-gateway"]
    assert [v["id"] for v in art["views"]] == ["private-request-path", "kb-ingestion"]
    assert art["selected"] == ["n:a", "n:b"] and len(art["catalogue_sha256"]) == 64
    assert set(art["views"][0]["sha256"]) == {"drawio", "svg", "png", "report"}
    assert rec[4]["artefacts"]["fetch-runtime"]["status"] == "declined"
    assert "decline_sha256" in rec[4]["artefacts"]["bridge-runtime"]
    assert all(p["ok"] for p in doc["parity"])
    assert (tmp_path / "out" / "e2e-public.md").read_text().startswith("# E2E results: public")
    _assert_no_secrets(capsys, tmp_path, stub)


def test_door_parity_mismatch_exits_1(tmp_path, capsys):
    stub = Stub(drift_door="fetch-gateway")
    assert _run(tmp_path, stub) == 1
    doc = json.loads((tmp_path / "out" / "e2e-public.json").read_text())
    bad = [p for p in doc["parity"] if not p["ok"]]
    assert bad and bad[0]["prompt_id"] == 2
    _assert_no_secrets(capsys, tmp_path, stub)


def test_private_stack_probe_deny_and_n11(tmp_path, capsys):
    stub = Stub()
    assert _run(tmp_path, stub, "--deny-only", stack="private") == 0
    probes = [c[1] for c in stub.calls if c[0] == "probe"]
    assert probes[0] == {"anonymous": True}
    assert probes[1] == {"token": E.MALFORMED_TOKEN}
    assert probes[2] == {"token": _token("u0-user@frontdoorclient01")}
    assert ("sigv4", "mcp-runtime") in stub.calls and ("sigv4", "memory") in stub.calls
    outside = [c for c in stub.calls if c[0] == "post"]
    assert [c[1] for c in outside] == ["gateway", "orchestrator"]
    doc = json.loads((tmp_path / "out" / "e2e-private.json").read_text())
    d4a = [r for r in doc["deny"] if r["test"] == "D4a M2M"]
    assert d4a[0]["verdict"] == "not-run" and d4a[0]["reason"] == "no M2M client in private mode"
    assert not [c for c in stub.calls if c[0] == "mint_client"]
    _assert_no_secrets(capsys, tmp_path, stub)


def test_private_outside_401_is_inconclusive(tmp_path):
    stub = Stub(status={"gateway": 401})
    assert _run(tmp_path, stub, "--deny-only", stack="private") == 1


def test_private_probe_allowed_is_stop_ship(tmp_path):
    stub = Stub(allow=[("probe", E.MALFORMED_TOKEN)])
    assert _run(tmp_path, stub, stack="private") == 5
    assert not [c for c in stub.calls if c[0] in ("draw", "ask")]


def test_private_full_run_uses_probe_door(tmp_path, capsys):
    stub = Stub()
    assert _run(tmp_path, stub, "--doors", "probe", stack="private") == 0
    assert {c[1] for c in stub.calls if c[0] == "draw"} == {"probe"}
    _assert_no_secrets(capsys, tmp_path, stub)


def test_unknown_door_is_config_error(tmp_path):
    assert _run(tmp_path, Stub(), "--doors", "probe") == 2


def _run_doc(arts):
    return {"prompts": [{"prompt_id": 2, "artefacts": {"bridge-gateway": arts}}]}


def test_compare_runs_rules():
    base = {"status": "rendered", "catalogue_sha256": "a" * 64, "selected": ["n:a"],
            "views": [{"id": "v", "quality": {"score": 97.0, "pass": True, "hard": 0},
                       "sha256": {"drawio": "1", "svg": "2", "png": "3", "report": "4"}}]}
    png_only = json.loads(json.dumps(base))
    png_only["views"][0]["sha256"]["png"] = "other"
    png_only["views"][0]["sha256"]["report"] = "other"
    assert E.compare_runs(_run_doc(base), _run_doc(png_only))[0]["ok"] is True
    svg = json.loads(json.dumps(base))
    svg["views"][0]["sha256"]["svg"] = "other"
    assert E.compare_runs(_run_doc(base), _run_doc(svg))[0]["ok"] is False
    kb = json.loads(json.dumps(svg))
    kb["catalogue_sha256"] = "b" * 64
    row = E.compare_runs(_run_doc(base), _run_doc(kb))[0]
    assert row["ok"] is True and row["note"] == "KB differs"


def test_compare_flag_fails_on_cross_stack_difference(tmp_path, capsys):
    stub = Stub()
    other = {"prompts": [{"prompt_id": 2, "artefacts": {"bridge-gateway": {
        "status": "rendered", "catalogue_sha256": "c" * 64, "selected": ["n:a", "n:b"],
        "views": []}}}]}
    path = tmp_path / "other.json"
    path.write_text(json.dumps(other))
    assert _run(tmp_path, stub, "--compare", str(path)) == 1
    doc = json.loads((tmp_path / "out" / "e2e-public.json").read_text())
    assert doc["compare"] and doc["compare"][0]["ok"] is False


def test_load_prompts_keeps_example_kb_rows_and_assembles_key():
    rows = E.load_prompts(ROOT / "tests" / "golden" / "diagram_prompts.yaml")
    assert rows and all(not r.get("fixture") for r in rows)
    assert all("__ACCESS_KEY__" not in r["prompt"] for r in rows)
    assert any(r["prompt"] == "draw gac pvt architecture end to end" for r in rows)


def test_classify_http():
    assert E.classify_http(200, {}, "") == ("allowed", "")
    assert E.classify_http(401, {"www-authenticate": "Bearer"}, "") == ("denied",
                                                                        "www-authenticate")
    assert E.classify_http(403, {}, '{"error":{"message":"GAC: not entitled"}}')[0] == "denied"
    assert E.classify_http(403, {}, "nothing")[0] == "denied-unidentified"
    assert E.classify_http(500, {}, "")[0] == "error"
