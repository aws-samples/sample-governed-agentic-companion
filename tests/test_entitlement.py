"""Entitlement-group authorization (D26, AC 24 static half, AC 25).

The interceptor cases load agentcore/interceptor/identity_interceptor.py fresh for each
environment (it reads GAC_REQUIRED_GROUP / GAC_REQUIRED_GROUP_CLAIM at cold start). Tokens are
unsigned test JWTs assembled at run time: the interceptor only decodes the payload (the gateway
authorizer verified the signature). The Terraform shape is asserted with python-hcl2; the
evaluated cases are runs e1..e12 in agentcore/terraform/tests/matrix.tftest.hcl.
"""
import base64
import importlib.util
import json
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INTERCEPTOR = ROOT / "agentcore" / "interceptor" / "identity_interceptor.py"
TF_DIR = ROOT / "agentcore" / "terraform"

GROUP = "gactest-users"
CLAIMS = ("cognito:groups", "groups")


def _load(monkeypatch, group=None, claim=None):
    for name, value in (("GAC_REQUIRED_GROUP", group), ("GAC_REQUIRED_GROUP_CLAIM", claim)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("identity_interceptor_under_test", INTERCEPTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _b64(obj) -> str:
    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _token(claims) -> str:
    # header.payload.signature; the signature part is a placeholder (never verified here).
    return ".".join([_b64({"alg": "none", "typ": "JWT"}), _b64(claims), "sig"])


def _event(token=None, rid=7, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    headers.update(extra_headers or {})
    body = {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": "gac___draw_diagram", "arguments": {}}}
    return {"mcp": {"gatewayRequest": {"headers": headers, "body": body}}}


def _denied(out, rid=7):
    assert out == {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayResponse": {"statusCode": 403, "body": {
            "jsonrpc": "2.0", "id": rid,
            "error": {"code": -32001, "message": "GAC: not entitled"}}}},
    }


def _forwarded_with_actor(out, actor):
    req = out["mcp"]["transformedGatewayRequest"]
    assert req["body"]["params"]["_meta"]["gac/actor-sub"] == actor
    assert req["headers"] == {"X-Gac-Actor-Sub": actor}
    assert "transformedGatewayResponse" not in out["mcp"]


# ---- AC 25 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("claim", CLAIMS)
def test_entitled_token_passes_with_actor(monkeypatch, claim):
    mod = _load(monkeypatch, GROUP, claim)
    out = mod.lambda_handler(_event(_token({"sub": "user-1", claim: ["other", GROUP]})), None)
    _forwarded_with_actor(out, "user-1")


@pytest.mark.parametrize("claim", CLAIMS)
@pytest.mark.parametrize("case", [
    "without-group", "other-claim-name", "not-a-list", "list-of-non-strings", "too-many-groups",
    "group-too-long", "claim-missing", "malformed-payload", "payload-not-object", "not-a-jwt",
    "no-bearer", "empty-bearer",
])
def test_not_entitled_returns_pinned_403(monkeypatch, claim, case):
    mod = _load(monkeypatch, GROUP, claim)
    other = "groups" if claim == "cognito:groups" else "cognito:groups"
    tokens = {
        "without-group": _token({"sub": "u", claim: ["other-users"]}),
        "other-claim-name": _token({"sub": "u", other: [GROUP]}),
        "not-a-list": _token({"sub": "u", claim: GROUP}),
        "list-of-non-strings": _token({"sub": "u", claim: [GROUP, 1]}),
        "too-many-groups": _token({"sub": "u", claim: [GROUP] + [f"g{i}" for i in range(100)]}),
        "group-too-long": _token({"sub": "u", claim: [GROUP, "x" * 129]}),
        "claim-missing": _token({"sub": "u"}),
        "malformed-payload": ".".join([_b64({"alg": "none"}), "%%%not-base64%%%", "sig"]),
        "payload-not-object": _token([GROUP]),
        "not-a-jwt": "opaque-token-without-dots",
        "no-bearer": None,
        "empty-bearer": "",
    }
    _denied(mod.lambda_handler(_event(tokens[case]), None))


def test_request_id_null_when_absent_or_invalid(monkeypatch):
    mod = _load(monkeypatch, GROUP)
    ev = _event(None)
    del ev["mcp"]["gatewayRequest"]["body"]["id"]
    _denied(mod.lambda_handler(ev, None), rid=None)
    _denied(mod.lambda_handler(_event(None, rid={"x": 1}), None), rid=None)
    _denied(mod.lambda_handler(_event(None, rid="req-9"), None), rid="req-9")


def test_default_claim_is_cognito_groups(monkeypatch):
    mod = _load(monkeypatch, GROUP, None)
    assert mod.GROUP_CLAIM == "cognito:groups"
    _forwarded_with_actor(
        mod.lambda_handler(_event(_token({"sub": "u2", "cognito:groups": [GROUP]})), None), "u2")
    _denied(mod.lambda_handler(_event(_token({"sub": "u2", "groups": [GROUP]})), None))


@pytest.mark.parametrize("bad", ["", "bad claim", "x" * 65, "groups\n", "grp$"])
def test_invalid_claim_name_fails_closed_with_one_error(monkeypatch, caplog, bad):
    with caplog.at_level(logging.ERROR):
        mod = _load(monkeypatch, GROUP, bad)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "invalid" in errors[0].getMessage()
    _denied(mod.lambda_handler(_event(_token({"sub": "u", "cognito:groups": [GROUP],
                                               "groups": [GROUP]})), None))


def test_unset_group_keeps_todays_behaviour(monkeypatch):
    mod = _load(monkeypatch, None, None)
    # A token without any group is forwarded with the actor, exactly as before.
    _forwarded_with_actor(mod.lambda_handler(_event(_token({"sub": "user-3"})), None), "user-3")
    # No bearer: passed through unchanged, no actor, no 403.
    out = mod.lambda_handler(_event(None), None)
    assert out == {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {
        "body": _event(None)["mcp"]["gatewayRequest"]["body"]}}}


def test_client_supplied_actor_is_still_overwritten(monkeypatch):
    mod = _load(monkeypatch, GROUP)
    ev = _event(_token({"sub": "real", "cognito:groups": [GROUP]}),
                extra_headers={"X-Gac-Actor-Sub": "spoofed"})
    ev["mcp"]["gatewayRequest"]["body"]["params"]["_meta"] = {"gac/actor-sub": "spoofed"}
    _forwarded_with_actor(mod.lambda_handler(ev, None), "real")


def test_logs_only_entitlement_outcome(monkeypatch, caplog):
    mod = _load(monkeypatch, GROUP)
    token = _token({"sub": "secret-subject-value", "cognito:groups": [GROUP]})
    with caplog.at_level(logging.INFO):
        mod.lambda_handler(_event(token), None)
        mod.lambda_handler(_event(_token({"sub": "secret-subject-value"})), None)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "identity-interceptor: entitled" in text and "identity-interceptor: not entitled" in text
    assert "secret-subject-value" not in text and token not in text and GROUP not in text


# ---- Terraform shape (python-hcl2) -----------------------------------------------------------

hcl2 = pytest.importorskip("hcl2")


def _unq(v):
    if not isinstance(v, str):
        return v
    v = v.strip()
    if v.startswith("${") and v.endswith("}"):
        v = v[2:-1]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


def _doc(name):
    with open(TF_DIR / name, encoding="utf-8") as fh:
        return hcl2.load(fh)


def _block(doc, kind, rtype, rname):
    for entry in doc.get(kind, []):
        for t, named in entry.items():
            if _unq(t) != rtype:
                continue
            for n, body in named.items():
                if _unq(n) == rname:
                    return body
    raise AssertionError(f"{kind} {rtype}.{rname} not found")


def _locals(*names):
    out = {}
    for n in names:
        for blk in _doc(n).get("locals", []):
            out.update({k: _unq(v) for k, v in blk.items() if not k.startswith("__")})
    return out


def _claim_blocks(authorizer_body):
    jwt = authorizer_body["custom_jwt_authorizer"][0]
    out = []
    for dyn in jwt.get("dynamic", []):
        for label, body in dyn.items():
            if _unq(label) == "custom_claim":
                out.append(body)
    return jwt, out


def _assert_claim(body, for_each):
    assert _unq(body["for_each"]) == for_each
    content = body["content"][0]
    assert _unq(content["inbound_token_claim_name"]) == 'local.entitlement_claim["name"]'
    assert _unq(content["inbound_token_claim_value_type"]) == 'local.entitlement_claim["value_type"]'
    match = content["authorizing_claim_match_value"][0]
    assert _unq(match["claim_match_operator"]) == "CONTAINS"
    assert _unq(match["claim_match_value"][0]["match_value_string"]) == "local.entitlement_group"


def test_gateway_claim_rule():
    gw = _block(_doc("gateway.tf"), "resource", "aws_bedrockagentcore_gateway", "this")
    jwt, claims = _claim_blocks(gw["authorizer_configuration"][0])
    assert _unq(jwt["allowed_clients"]) == "local.jwt_allowed_clients"
    assert len(claims) == 1
    _assert_claim(claims[0], "local.entitlement_enforced ? [1] : []")


def _runtime_authorizer(name):
    rt = _block(_doc("runtimes.tf"), "resource", "aws_bedrockagentcore_agent_runtime", name)
    for dyn in rt["dynamic"]:
        for label, body in dyn.items():
            if _unq(label) == "authorizer_configuration":
                return body
    raise AssertionError(name)


def test_orchestrator_claim_rule_and_clients():
    auth = _runtime_authorizer("orchestrator")
    jwt, claims = _claim_blocks(auth["content"][0])
    assert _unq(jwt["allowed_clients"]) == "local.orchestrator_allowed_clients"
    assert len(claims) == 1
    _assert_claim(claims[0], "local.entitlement_enforced ? [1] : []")


def test_mcp_runtime_claim_only_without_gateway_oauth():
    auth = _runtime_authorizer("mcp_runtime")
    assert _unq(auth["for_each"]) == "local.inbound_jwt_enabled && !local.mcp_runtime_iam_inbound ? [1] : []"
    jwt, claims = _claim_blocks(auth["content"][0])
    assert _unq(jwt["allowed_clients"]) == "local.mcp_runtime_allowed_clients"
    assert len(claims) == 1
    _assert_claim(claims[0], "local.entitlement_enforced && !local.gateway_outbound_oauth ? [1] : []")


def test_allowed_clients_split():
    loc = _locals("locals.tf")
    orch = loc["orchestrator_allowed_clients"]
    assert "local.jwt_allowed_clients" in orch and "m2m" not in orch
    mcp = loc["mcp_runtime_allowed_clients"]
    assert mcp == ("local.gateway_outbound_oauth ? aws_cognito_user_pool_client.m2m[*].id : "
                   "local.orchestrator_allowed_clients")
    assert "runtime_allowed_clients" not in loc


def test_entitlement_locals_n12_n16():
    loc = _locals("cognito.tf")
    assert loc["entitlement_group"] == ('var.entitlement_group_name != "" ? var.entitlement_group_name '
                                        ': "${var.name_prefix}-users"')
    claim = _doc("cognito.tf")["locals"][0]["entitlement_claim"]
    assert _unq(claim["name"]) == "var.entitlement_claim_name" and _unq(claim["value_type"]) == "STRING_ARRAY"
    assert loc["entitlement_enforced"] == "local.inbound_jwt_enabled && var.require_entitlement_group"
    assert loc["manage_entitlement_group"] == ("var.manage_entitlement_group == null ? "
                                               "var.manage_identity_pool : var.manage_entitlement_group")
    assert "local.manage_entitlement_group" in loc["entitlement_group_count"]
    assert "local.entitlement_unmanaged" in loc["entitlement_check_count"]
    check = loc["entitlement_unmanaged"] + " && " + loc["entitlement_check_count"]
    for term in ("local.entitlement_enforced", "local.auth_is_cognito", "!local.manage_entitlement_group",
                 "!var.manage_identity_pool", "var.check_entitlement_group_exists"):
        assert term in check


def test_group_resource_and_check_data_source():
    doc = _doc("cognito.tf")
    grp = _block(doc, "resource", "aws_cognito_user_group", "entitlement")
    assert _unq(grp["count"]) == "local.entitlement_group_count"
    assert _unq(grp["name"]) == "local.entitlement_group"
    ds = _block(doc, "data", "aws_cognito_user_groups", "entitlement")
    assert _unq(ds["count"]) == "local.entitlement_check_count"
    post = ds["lifecycle"][0]["postcondition"][0]
    assert _unq(post["condition"]) == "contains([for g in self.groups : g.group_name], local.entitlement_group)"
    assert _unq(post["error_message"]) == (
        "entitlement group ${local.entitlement_group} not found in the shared user pool; the pool's "
        "owner creates it (platform/ apps map or a manual step), see docs/DEPLOY.md")


def test_entitlement_guards():
    guards = _block(_doc("guards.tf"), "resource", "terraform_data", "auth_guards")
    pre = {_unq(p["condition"]): _unq(p["error_message"]) for p in guards["lifecycle"][0]["precondition"]}
    msg = pre["!(var.manage_entitlement_group == true && !var.manage_identity_pool)"]
    assert msg.endswith("the shared pool's owner creates the group (platform/ apps map or a manual step)")
    assert "local.network_private || var.require_entitlement_group" in pre


def test_interceptor_env_when_enforced():
    fn = _block(_doc("interceptor.tf"), "resource", "aws_lambda_function", "identity_interceptor")
    env = [body for dyn in fn["dynamic"] for label, body in dyn.items() if _unq(label) == "environment"]
    assert len(env) == 1 and _unq(env[0]["for_each"]) == "local.entitlement_enforced ? [1] : []"
    variables = env[0]["content"][0]["variables"]
    assert {k: _unq(v) for k, v in variables.items()} == {
        "GAC_REQUIRED_GROUP": "local.entitlement_group",
        "GAC_REQUIRED_GROUP_CLAIM": "var.entitlement_claim_name",
    }


def test_interceptor_env_uses_its_own_cmk():
    # Approved deviation from D26.2 (option B): fix CKV_AWS_173 with a CMK, not a suppression.
    doc = _doc("interceptor.tf")
    raw = (TF_DIR / "interceptor.tf").read_text()
    assert "CKV_AWS_173" in raw and "checkov:skip=CKV_AWS_173" not in raw
    key = _block(doc, "resource", "aws_kms_key", "interceptor")
    alias = _block(doc, "resource", "aws_kms_alias", "interceptor")
    pol = _block(doc, "resource", "aws_iam_role_policy", "interceptor_env_key")
    for body in (key, alias, pol):
        assert _unq(body["count"]) == "local.interceptor_enabled ? 1 : 0"
    assert key["enable_key_rotation"] is True and key["deletion_window_in_days"] == 7
    assert _unq(alias["name"]) == "alias/${var.name_prefix}-interceptor"
    fn = _block(doc, "resource", "aws_lambda_function", "identity_interceptor")
    assert _unq(fn["kms_key_arn"]) == "aws_kms_key.interceptor[0].arn"
    key_policy = json.dumps(key["policy"])
    assert "kms:EncryptionContext:aws:lambda:FunctionArn" in key_policy
    assert "lambda.${local.region}.amazonaws.com" in key_policy
    assert 'Action = ["kms:Decrypt"], Resource = aws_kms_key.interceptor[0].arn' in pol["policy"]
    boundary = json.dumps(_block(doc, "resource", "aws_iam_policy", "interceptor_boundary")["policy"])
    assert "EnvKeyDecryptCeiling" in boundary


def test_entitlement_variables():
    doc = _doc("variables.tf")
    v = {_unq(k): b for blk in doc["variable"] for k, b in blk.items() if not k.startswith("__")}
    assert v["require_entitlement_group"]["default"] is True
    assert _unq(v["entitlement_group_name"]["default"]) == ""
    assert v["manage_entitlement_group"]["default"] is None
    assert v["check_entitlement_group_exists"]["default"] is True
    assert _unq(v["entitlement_claim_name"]["default"]) == "cognito:groups"
    assert "^[A-Za-z0-9:_-]{1,64}$" in json.dumps(v["entitlement_claim_name"]["validation"])
    assert "^[A-Za-z0-9_-]{1,128}$" in json.dumps(v["entitlement_group_name"]["validation"])
