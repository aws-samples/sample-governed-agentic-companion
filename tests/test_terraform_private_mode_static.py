"""Static structure tests for the private network mode Terraform (no AWS, no plan).

They parse agentcore/terraform/*.tf with python-hcl2 and assert the security-relevant SHAPE of
the configuration: private is the default, public needs the acknowledgement, no security group
can carry a /0 CIDR, the resource policies deny anything not arriving through the approved VPC
endpoints, and every private-mode resource is gated so public mode creates none of them.

What these tests do NOT do: evaluate Terraform logic or AWS policy behaviour. `terraform
validate` checks types/references for both branches; the per-mode behaviour is only provable by
the post-deploy tests in docs/DEPLOY.md.

Never opened: backend.tf, override files, *.tfvars, state, plan files, .terraform/, generated/.
"""
import json
import re
from pathlib import Path

import pytest

hcl2 = pytest.importorskip("hcl2")

ROOT = Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "agentcore" / "terraform"
NET_MODULE_DIR = TF_DIR / "modules" / "private-network"
PLATFORM_DIR = TF_DIR / "platform"
_EXCLUDED = {"backend.tf", "override.tf"}


def _tf_files(d=TF_DIR):
    return sorted(
        p for p in Path(d).glob("*.tf")
        if p.name not in _EXCLUDED and not p.name.endswith("_override.tf")
    )


def _unq(value):
    """Normalise a python-hcl2 v8 scalar: strip ${...} and surrounding quotes."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if v.startswith("${") and v.endswith("}"):
        v = v[2:-1]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


def _s(obj):
    """Expression/structure as one searchable string."""
    return json.dumps(obj) if not isinstance(obj, str) else obj


def _model(d):
    parsed = {}
    for p in _tf_files(d):
        with p.open(encoding="utf-8") as fh:
            parsed[p.name] = hcl2.load(fh)
    model = {"files": parsed, "resource": {}, "data": {}, "variable": {}, "locals": {},
             "check": {}, "module": {},
             "raw": {p.name: p.read_text(encoding="utf-8") for p in _tf_files(d)}}
    for fname, doc in parsed.items():
        for kind in ("resource", "data"):
            for entry in doc.get(kind, []):
                for rtype, named in entry.items():
                    if rtype.startswith("__"):
                        continue
                    for rname, body in named.items():
                        if rname.startswith("__"):
                            continue
                        model[kind][(_unq(rtype), _unq(rname))] = dict(body, __file__=fname)
        for entry in doc.get("variable", []):
            for vname, body in entry.items():
                if not vname.startswith("__"):
                    model["variable"][_unq(vname)] = body
        for entry in doc.get("locals", []):
            for k, v in entry.items():
                if not k.startswith("__"):
                    model["locals"][k] = v
        for entry in doc.get("check", []):
            for cname, body in entry.items():
                if not cname.startswith("__"):
                    model["check"][_unq(cname)] = body
        for entry in doc.get("module", []):
            for mname, body in entry.items():
                if not mname.startswith("__"):
                    model["module"][_unq(mname)] = dict(body, __file__=fname)
    return model


@pytest.fixture(scope="module")
def tf():
    model = _model(TF_DIR)
    # The private network module, and the platform stack when present, as second models.
    model["mod"] = _model(NET_MODULE_DIR)
    model["platform"] = _model(PLATFORM_DIR) if PLATFORM_DIR.is_dir() else None
    return model


def _preconditions(body):
    out = []
    for lc in body.get("lifecycle", []):
        out.extend(lc.get("precondition", []))
    return out


def _validations(var_body):
    return var_body.get("validation", [])


def _gate(body):
    return _s(body.get("count", "")) + _s(body.get("for_each", ""))


def _wrapped_in(expr, target, wrappers=("can", "try")):
    """True when every `target(` call in expr sits inside one of the wrapper calls."""
    stack = []
    i = 0
    found_any = False
    while i < len(expr):
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*\(", expr[i:])
        if m:
            name = m.group(0)[:-1]
            if name == target:
                found_any = True
                if not any(w in stack for w in wrappers):
                    return False
            stack.append(name)
            i += len(m.group(0))
            continue
        if expr[i] == "(":
            stack.append("")
        elif expr[i] == ")" and stack:
            stack.pop()
        i += 1
    return found_any


# ---- Fixtures mirrored from variables.tf validations (Python re == RE2 for these) ---------

_ADMIN_NARROW = r"^arn:aws[a-z-]*:iam::[0-9]{12}:(role|user)/[^*?]{3,}"
_ACCT = "123456789012"  # placeholder account id used only inside this test


# 1
def test_network_access_default_private(tf):
    v = tf["variable"]["network_access"]
    assert _unq(v["default"]) == "private"
    cond = _validations(v)[0]["condition"]
    assert re.findall(r'"(\w+)"', cond) == ["private", "public"]
    assert _unq(tf["variable"]["acknowledge_public_endpoint"]["default"]) is False


_OPEN = ("0.0.0.0/0", "::/0")
# CIDR sources a cidr_ipv4 rule may iterate: validated variables or locals built from them.
_CIDR_GATES = ("local.client_cidrs_effective", "var.client_cidrs", "local.resolver_cidrs")


def _strip_validations(raw):
    """raw HCL text with every `validation { ... }` block removed (brace-balanced)."""
    out, i = [], 0
    while True:
        m = re.search(r"\bvalidation\s*\{", raw[i:])
        if not m:
            out.append(raw[i:])
            return "".join(out)
        start = i + m.start()
        out.append(raw[i:start])
        depth, j = 0, i + m.end() - 1
        while j < len(raw):
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1


def _sg_rules(model):
    return {k: b for k, b in model["resource"].items()
            if k[0] in ("aws_vpc_security_group_ingress_rule", "aws_vpc_security_group_egress_rule")}


# 6
def test_no_open_cidr_in_network_or_policies(tf):
    raws = [("root/" + n, tf["raw"][n]) for n in ("network.tf", "resource_policies.tf", "moved.tf", "probe.tf")
            if n in tf["raw"]]
    raws += [("module/" + n, r) for n, r in tf["mod"]["raw"].items() if n != "variables.tf"]
    if tf["platform"]:
        raws += [("platform/" + n, r) for n, r in tf["platform"]["raw"].items() if n != "variables.tf"]
    for name, raw in raws:
        for o in _OPEN:
            assert o not in raw, (name, o)
    # Variables files may hold /0 only inside validation conditions.
    var_raws = [("module/variables.tf", tf["mod"]["raw"]["variables.tf"])]
    if tf["platform"]:
        var_raws.append(("platform/variables.tf", tf["platform"]["raw"]["variables.tf"]))
    for name, raw in var_raws:
        rest = _strip_validations(raw)
        for o in _OPEN:
            assert o not in rest, (name, o)
    for model in (tf, tf["mod"]) + ((tf["platform"],) if tf["platform"] else ()):
        for (rtype, rname), body in _sg_rules(model).items():
            has_ref = "referenced_security_group_id" in body or "prefix_list_id" in body
            if "cidr_ipv4" in body:
                assert _unq(body["cidr_ipv4"]) in ("each.value", "each.value[0]"), rname
                assert any(g in _gate(body) for g in _CIDR_GATES), rname
            else:
                assert has_ref, rname
            assert "cidr_ipv6" not in body, rname
    cond = _validations(tf["variable"]["private_client_cidrs"])[0]["condition"]
    assert "0.0.0.0/0" in cond and "::/0" in cond
    mcond = _validations(tf["mod"]["variable"]["client_cidrs"])[0]["condition"]
    assert "0.0.0.0/0" in mcond and "::/0" in mcond


# 7
def test_network_guards_public_ack_and_no_auth_refused(tf):
    pre = _preconditions(tf["resource"][("terraform_data", "network_guards")])
    conds = [_s(p["condition"]) for p in pre]
    assert any("var.acknowledge_public_endpoint" in c for c in conds)
    assert any("local.auth_is_none" in c and "local.network_private" in c
               and "allow_unauthenticated" not in c for c in conds)


# 8 (network.tf + resource_policies.tf)
_PRIVATE_GATES = ("local.network_private", "local.create_vpc", "local.managed_endpoint_keys",
                  "local.vpce_sg_ids", "aws_security_group.vpce_client", "local.existing_vpc_lookups",
                  "local.reuse_s3_endpoint", "local.mcp_runtime_iam_inbound", "local.probe_enabled")
# Inside the module, blocks are gated on module inputs (the module itself is private-gated).
_MODULE_GATES = ("var.create_vpc", "var.interface_endpoint_services", "var.create_s3_gateway_endpoint",
                 "var.enable_resolver_inbound_endpoint", "aws_security_group.vpce_client")


def test_every_new_block_is_private_gated(tf):
    for kind in ("resource", "data"):
        for key, body in tf[kind].items():
            if body["__file__"] not in ("network.tf", "resource_policies.tf", "probe.tf"):
                continue
            gate = _gate(body)
            assert gate, f"{kind} {key} has no count/for_each"
            assert any(g in gate for g in _PRIVATE_GATES), f"{kind} {key} not private-gated: {gate}"
    call = tf["module"]["private_network"]
    assert _unq(call["source"]) == "./modules/private-network"
    assert "local.network_private" in _gate(call)
    for kind in ("resource", "data"):
        for key, body in tf["mod"][kind].items():
            if key == ("aws_region", "current"):
                continue
            gate = _gate(body)
            assert gate, f"module {kind} {key} has no count/for_each"
            assert any(g in gate for g in _MODULE_GATES), f"module {kind} {key} not gated: {gate}"


# 10
def test_no_internet_or_nat_gateway(tf):
    types = {rtype for (rtype, _) in tf["resource"]} | {rtype for (rtype, _) in tf["mod"]["resource"]}
    assert not types & {"aws_internet_gateway", "aws_nat_gateway", "aws_eip",
                        "aws_egress_only_internet_gateway"}
    rt = tf["mod"]["resource"][("aws_route_table", "private")]
    assert "route" not in rt
    assert ("aws_route", "private") not in tf["mod"]["resource"]


# 14
def test_guards_knowledge_admins_route_tables_and_runner_check(tf):
    pre = _preconditions(tf["resource"][("terraform_data", "network_guards")])
    conds = " ".join(_s(p["condition"]) for p in pre)
    assert "var.knowledge_admin_principal_arns" in conds
    assert "local.subnet_route_table" in conds
    assert "runner_is_knowledge_admin" in tf["check"]
    assert len(pre) >= 18


# 15
_RULE_SG_REFS = ("aws_security_group.runtime", "aws_security_group.probe", "each.value",
                 "local.vpce_sg_ids", "aws_security_group.vpce_client")
_CIDR_RULE_SGS = ("aws_security_group.vpce_client", "aws_security_group.resolver_inbound")


def test_security_group_rules_cidr_only_on_client_sg_and_described(tf):
    for model in (tf, tf["mod"]):
        rules = _sg_rules(model)
        assert rules
        for (rtype, rname), body in rules.items():
            assert _unq(body.get("description", "")).strip(), f"{rname} has no description"
            if "cidr_ipv4" in body:
                assert rtype == "aws_vpc_security_group_ingress_rule"
                assert any(r in _s(body["security_group_id"]) for r in _CIDR_RULE_SGS), rname
            else:
                assert any(r in _s(body["security_group_id"]) for r in _RULE_SG_REFS), rname
    # No inline rules on the SGs themselves.
    sgs = [tf["resource"][("aws_security_group", "runtime")]]
    sgs += [b for (t, _), b in tf["mod"]["resource"].items() if t == "aws_security_group"]
    sgs += [b for (t, n), b in tf["resource"].items() if t == "aws_security_group" and n == "probe"]
    for sg in sgs:
        assert "ingress" not in sg and "egress" not in sg


# 18
@pytest.mark.parametrize("var", ["private_client_cidrs", "vpc_cidr"])
def test_cidr_validations_ipv4_and_guarded_parses(tf, var):
    cond = _s(_validations(tf["variable"][var])[0]["condition"])
    assert "cidrnetmask" in cond
    assert _wrapped_in(cond, "tonumber"), cond


# 19 (endpoint half; the Memory resource-policy half is asserted with the resource policies)
def test_dataplane_endpoint_policy_is_explicit(tf):
    doc = tf["data"][("aws_iam_policy_document", "vpce_agentcore")]
    (stmt,) = doc["statement"]
    actions = _s(stmt["actions"])
    assert "local.dataplane_base_actions" in actions
    assert "local.memory_enabled ? local.memory_dataplane_actions" in actions
    assert "var.dataplane_endpoint_extra_actions" in actions
    base = [_unq(a) for a in tf["locals"]["dataplane_base_actions"]]
    assert "bedrock-agentcore:InvokeAgentRuntime" in base
    for a in base + [_unq(a) for a in tf["locals"]["memory_dataplane_actions"]]:
        assert "*" not in a
        assert "InvokeAgentRuntimeCommand" not in a and "WebSocket" not in a
    assert len(tf["locals"]["memory_dataplane_actions"]) == 15
    cond = _validations(tf["variable"]["dataplane_endpoint_extra_actions"])[0]["condition"]
    rx = re.search(r'regex\("([^"]+)"', cond).group(1).replace("\\\\", "\\")
    assert re.fullmatch(rx, "bedrock-agentcore:InvokeHarness")
    for bad in ("bedrock-agentcore:*", "*", "s3:GetObject", "bedrock-agentcore:Invoke*"):
        assert not re.search(rx, bad), bad


# 22
def test_knowledge_admin_narrowness(tf):
    vals = _validations(tf["variable"]["knowledge_admin_principal_arns"])
    assert any("[^*?]{3,}" in v["condition"] for v in vals)
    pre = f"arn:aws:iam::{_ACCT}:"
    for bad in ("role/*", "role/*admin*", "role/ab*"):
        assert not re.match(_ADMIN_NARROW, pre + bad), bad
    for good in ("role/gac-deployer", "role/ci-*",
                 "role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_Admin_*"):
        assert re.match(_ADMIN_NARROW, pre + good), good


# 23 (variable half)
def test_control_plane_endpoint_rejected(tf):
    conds = [v["condition"] for v in _validations(tf["variable"]["extra_interface_endpoint_services"])]
    assert any("bedrock-agentcore-control" in c for c in conds)

    def rejected(s):
        return "bedrock-agentcore-control" in s

    assert rejected("bedrock-agentcore-control")
    assert rejected("com.amazonaws.us-east-1.bedrock-agentcore-control")
    assert not rejected("sts") and not rejected("monitoring")
    key_cond = _validations(tf["variable"]["existing_interface_endpoint_ids"])[0]["condition"]
    keys = set(re.findall(r'"([a-z0-9.-]+)"', key_cond))
    assert keys == {"bedrock-agentcore", "bedrock-agentcore.gateway", "bedrock-runtime",
                    "ecr.api", "ecr.dkr", "logs", "xray", "comprehend"}
    assert "bedrock-agentcore-control" not in keys


def _statements(doc_body):
    """Static statements plus dynamic-statement contents of an aws_iam_policy_document."""
    out = list(doc_body.get("statement", []))
    for dyn in doc_body.get("dynamic", []):
        for label, body in dyn.items():
            if _unq(label) == "statement":
                for content in body.get("content", []):
                    out.append(dict(content, __for_each__=_s(body.get("for_each", ""))))
    return out


def _by_sid(doc_body):
    return {_unq(st.get("sid", "")): st for st in _statements(doc_body)}


def _conds(stmt):
    """[(test, variable, values_str)] incl. dynamic conditions (marked with their for_each)."""
    out = [(_unq(c["test"]), _unq(c["variable"]), _s(c.get("values", "")), "")
           for c in stmt.get("condition", [])]
    for dyn in stmt.get("dynamic", []):
        for label, body in dyn.items():
            if _unq(label) == "condition":
                for c in body.get("content", []):
                    out.append((_unq(c["test"]), _unq(c["variable"]), _s(c.get("values", "")),
                                _s(body.get("for_each", ""))))
    return out


def _principal(stmt):
    p = stmt["principals"][0]
    return _unq(p["type"]), [_unq(i) for i in p["identifiers"]]


def _runtime(tf, name):
    return tf["resource"][("aws_bedrockagentcore_agent_runtime", name)]


# 2
def test_runtimes_use_vpc_mode_in_private(tf):
    assert _s(tf["locals"]["network_private"]) == '${var.network_access == "private"}'
    assert _unq(tf["locals"]["runtime_network_mode"]) == 'local.network_private ? "VPC" : "PUBLIC"'
    for name in ("orchestrator", "mcp_runtime"):
        net = _runtime(tf, name)["network_configuration"][0]
        assert _unq(net["network_mode"]) == "local.runtime_network_mode"
        dyn = net["dynamic"][0]['"network_mode_config"']
        assert "local.network_private" in _s(dyn["for_each"])
        content = dyn["content"][0]
        assert "local.private_subnet_ids_effective" in _s(content["subnets"])
        assert "aws_security_group.runtime[0].id" in _s(content["security_groups"])
        deps = _s(_runtime(tf, name)["depends_on"])
        for dep in ("terraform_data.network_guards", "module.private_network",
                    "aws_vpc_security_group_egress_rule.runtime_to_s3",
                    "aws_vpc_security_group_ingress_rule.vpce_from_runtime"):
            assert dep in deps, (name, dep)
        assert "aws_vpc_endpoint." not in deps, name


def _assert_vpce_allow_deny(doc, action, deny_actions_ref=None):
    sts = _statements(doc)
    allows = [s for s in sts if _unq(s["effect"]) == "Allow"]
    denies = [s for s in sts if _unq(s["effect"]) == "Deny"]
    assert allows and denies
    for st in allows + denies:
        assert _principal(st) == ("*", ["*"])
    assert any(action in _s(s["actions"]) and
               ("StringEquals", "aws:SourceVpce") in [(t, v) for t, v, _, _ in _conds(s)]
               for s in allows)
    deny = denies[0]
    assert ("StringNotEquals", "aws:SourceVpce") in [(t, v) for t, v, _, _ in _conds(deny)]
    if deny_actions_ref:
        assert _unq(deny["actions"]) == deny_actions_ref
    else:
        assert action in _s(deny["actions"])
    return allows, denies


# 3
def test_gateway_resource_policy_denies_non_vpce(tf):
    pol = tf["resource"][("aws_bedrockagentcore_resource_policy", "gateway")]
    assert "local.network_private" in _gate(pol) and "local.gateway_enabled" in _gate(pol)
    doc = tf["data"][("aws_iam_policy_document", "gateway_resource")]
    _assert_vpce_allow_deny(doc, "bedrock-agentcore:InvokeGateway")
    for st in _statements(doc):
        assert "local.gateway_vpce_ids" in _s(st["condition"])


# 4
def test_orchestrator_resource_policy_denies_non_vpce(tf):
    pol = tf["resource"][("aws_bedrockagentcore_resource_policy", "orchestrator")]
    assert "local.network_private" in _gate(pol)
    assert "orchestrator.agent_runtime_arn" in _s(pol["resource_arn"])
    doc = tf["data"][("aws_iam_policy_document", "orchestrator_resource")]
    _assert_vpce_allow_deny(doc, "bedrock-agentcore:InvokeAgentRuntime",
                            deny_actions_ref="local.runtime_policy_actions")


# 5
def test_mcp_runtime_policy_allows_only_gateway_role(tf):
    doc = tf["data"][("aws_iam_policy_document", "mcp_runtime_resource")]
    sids = _by_sid(doc)
    allow, deny = sids["AllowOnlyGatewayRole"], sids["DenyOtherPrincipals"]
    assert _principal(allow) == ("AWS", ["aws_iam_role.gateway_exec[0].arn"])
    assert _principal(deny) == ("AWS", ["*"])
    assert [(t, v, vals) for t, v, vals, _ in _conds(deny)] == [
        ("ArnNotEquals", "aws:PrincipalArn", '["${aws_iam_role.gateway_exec[0].arn}"]')]
    assert "local.mcp_runtime_iam_inbound" in _gate(
        tf["resource"][("aws_bedrockagentcore_resource_policy", "mcp_runtime")])
    target = tf["resource"][("aws_bedrockagentcore_gateway_target", "mcp_engine")]
    creds = [d['"credential_provider_configuration"'] for d in target["dynamic"]
             if '"credential_provider_configuration"' in d]
    iam_blocks = [c for c in creds
                  if _s(c["for_each"]).startswith("${local.mcp_runtime_iam_inbound ?")]
    assert len(iam_blocks) == 1
    role = iam_blocks[0]["content"][0]["gateway_iam_role"][0]
    assert _unq(role["service"]) == "bedrock-agentcore"
    auth = _runtime(tf, "mcp_runtime")["dynamic"][0]['"authorizer_configuration"']
    assert "!local.mcp_runtime_iam_inbound" in _s(auth["for_each"])
    assert _unq(tf["locals"]["mcp_runtime_iam_inbound"]) == "local.network_private && local.gateway_enabled"


# 13
def test_ordering_policies_before_tools_and_frontdoor_files(tf):
    target = tf["resource"][("aws_bedrockagentcore_gateway_target", "mcp_engine")]
    deps = _s(target["depends_on"])
    assert "aws_bedrockagentcore_resource_policy.gateway" in deps
    assert "aws_bedrockagentcore_resource_policy.mcp_runtime" in deps
    for (rtype, rname), body in tf["resource"].items():
        if rtype == "local_file" and body["__file__"] == "frontdoor_config.tf":
            assert "terraform_data.private_access_policies" in _s(body.get("depends_on", "")), rname
    agg = _s(tf["resource"][("terraform_data", "private_access_policies")]["depends_on"])
    for dep in ("aws_bedrockagentcore_resource_policy.gateway",
                "aws_bedrockagentcore_resource_policy.orchestrator",
                "aws_bedrockagentcore_resource_policy.mcp_runtime",
                "aws_bedrockagentcore_resource_policy.mcp_runtime_vpce",
                "aws_bedrockagentcore_resource_policy.memory",
                "aws_s3_bucket_policy.kb",
                "aws_ecr_repository_policy.orchestrator",
                "aws_ecr_repository_policy.mcp_runtime"):
        assert dep in agg, dep


# 16
def test_runtime_deny_lists_use_shared_local(tf):
    expected = {"bedrock-agentcore:" + a for a in (
        "InvokeAgentRuntime", "InvokeAgentRuntimeForUser", "InvokeAgentRuntimeCommand",
        "InvokeAgentRuntimeCommandShell", "InvokeAgentRuntimeWithWebSocketStream",
        "InvokeAgentRuntimeWithWebSocketStreamForUser", "StopRuntimeSession", "GetAgentCard")}
    assert {_unq(a) for a in tf["locals"]["runtime_policy_actions"]} == expected
    for name in ("orchestrator_resource", "mcp_runtime_resource", "mcp_runtime_vpce_resource"):
        doc = tf["data"][("aws_iam_policy_document", name)]
        denies = [s for s in _statements(doc) if _unq(s["effect"]) == "Deny"]
        assert denies and all(_unq(d["actions"]) == "local.runtime_policy_actions" for d in denies), name


# 17
def test_m2m_client_not_created_in_private_mode(tf):
    gate = _gate(tf["resource"][("aws_cognito_user_pool_client", "m2m")])
    assert "local.gateway_outbound_oauth" in gate and "local.network_private" in gate
    assert "!local.network_private" in _s(tf["locals"]["gateway_outbound_oauth"])
    assert "local.gateway_outbound_oauth" in _s(tf["locals"]["mcp_runtime_allowed_clients"])
    assert "m2m" not in _s(tf["locals"]["orchestrator_allowed_clients"])
    assert "local.gateway_outbound_oauth" in _gate(
        tf["resource"][("aws_bedrockagentcore_oauth2_credential_provider", "gateway_m2m")])


# 19 (Memory half)
def test_memory_policy_uses_shared_action_local(tf):
    doc = tf["data"][("aws_iam_policy_document", "memory_resource")]
    deny = _by_sid(doc)["DenyDataPlaneNotViaEndpoint"]
    assert len(doc["statement"]) == 1  # the one static statement; the shared-endpoint one is dynamic
    assert _unq(deny["effect"]) == "Deny"
    assert _unq(deny["actions"]) == "local.memory_dataplane_actions"
    conds = {(t, v) for t, v, _, _ in _conds(deny)}
    assert ("StringNotEquals", "aws:SourceVpce") in conds and ("Bool", "aws:ViaAWSService") in conds


# 20
def test_principal_account_condition_only_for_auth_none(tf):
    for name in ("orchestrator_resource", "mcp_runtime_vpce_resource"):
        doc = tf["data"][("aws_iam_policy_document", name)]
        (allow,) = [s for s in _statements(doc) if _unq(s["effect"]) == "Allow"]
        acct = [c for c in _conds(allow) if c[1] == "aws:PrincipalAccount"]
        assert len(acct) == 1 and "local.auth_is_none" in acct[0][3], name
    # No static aws:PrincipalAccount condition on any AgentCore resource-policy document.
    for (dtype, dname), body in tf["data"].items():
        if body["__file__"] != "resource_policies.tf" or dname.startswith("vpce_"):
            continue
        for st in _statements(body):
            for t, v, vals, dyn in _conds(st):
                if v == "aws:PrincipalAccount":
                    assert "local.auth_is_none" in dyn, dname


def _json_statements(expr):
    """Split a normalised jsonencode({... Statement = concat([...], [for _ in range(g) : {...}])})
    expression into {sid: (statement_text, gate_text)}. gate_text is the range() condition of a
    `[for _ in range(...)` wrapper directly before the statement, or "" for a static one."""
    out = {}
    starts = [m.start() for m in re.finditer(r'\{Sid = "', expr)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(expr)
        text = expr[start:end]
        sid = re.match(r'\{Sid = "([^"]+)"', text).group(1)
        prefix = expr[:start]
        m = re.search(r"\[for _ in range\(([^\]]*?)\) : $", prefix)
        out[sid] = (text, m.group(1) if m else "")
    return out


def _kb_bucket_policy(tf):
    return _s(tf["resource"][("aws_s3_bucket_policy", "kb")]["policy"])


# 9
def test_kb_bucket_denies_non_vpce_and_bpa_stays_on(tf):
    sts = _json_statements(_kb_bucket_policy(tf))
    assert set(sts) == {"DenyInsecureTransport", "DenyDataAccessNotViaS3Endpoint"}
    assert sts["DenyInsecureTransport"][1] == ""  # unchanged in both modes
    text, gate = sts["DenyDataAccessNotViaS3Endpoint"]
    assert "local.network_private" in gate
    assert 'Effect = "Deny"' in text and 'Principal = "*"' in text
    assert 'StringNotEquals = {"aws:SourceVpce" = local.s3_gateway_vpce_id}' in text
    assert 'ArnNotLike = {"aws:PrincipalArn" = local.knowledge_admin_arns_effective}' in text
    assert 'BoolIfExists = {"aws:PrincipalIsAWSService" = "false"}' in text
    bpa = tf["resource"][("aws_s3_bucket_public_access_block", "kb")]
    for flag in ("block_public_acls", "block_public_policy", "ignore_public_acls",
                 "restrict_public_buckets"):
        assert bpa[flag] is True, flag


# 9a: the evidence behind KICS suppression K4 (docs/security/scan-results.md)
@pytest.mark.parametrize("name", ["kb", "kb_logs"])
def test_kb_bucket_policies_deny_insecure_transport(tf, name):
    policy = _s(tf["resource"][("aws_s3_bucket_policy", name)]["policy"])
    text = _json_statements(policy)["DenyInsecureTransport"][0]
    assert 'Effect = "Deny"' in text and 'Principal = "*"' in text
    assert 'Action = "s3:*"' in text
    assert f"aws_s3_bucket.{name}.arn" in text and f'"${{aws_s3_bucket.{name}.arn}}/*"' in text
    assert 'Condition = {Bool = {"aws:SecureTransport" = "false"}}' in text
# 9b
def test_kb_bucket_deny_never_lists_management_actions(tf):
    for sid, (text, _) in _json_statements(_kb_bucket_policy(tf)).items():
        if 'Effect = "Deny"' not in text or sid == "DenyInsecureTransport":
            continue
        action_list = re.search(r"Action = (\[[^\]]*\]|\"[^\"]*\")", text).group(1)
        actions = re.findall(r'"([^"]+)"', action_list)
        assert actions
        for bad in ("s3:PutBucketPolicy", "s3:GetBucketPolicy", "s3:DeleteBucketPolicy",
                    "s3:PutBucketPublicAccessBlock", "s3:GetBucketPublicAccessBlock", "s3:*", "*"):
            assert bad not in actions, (sid, bad)


# 11
def test_ecr_pull_deny_in_private_mode(tf):
    expr = _s(tf["locals"]["ecr_repo_policy"])
    sts = _json_statements(expr)
    assert set(sts) == {"AllowRuntimePull", "DenyPullNotViaEcrEndpointsOrAdmins"}
    assert sts["AllowRuntimePull"][1] == ""
    text, gate = sts["DenyPullNotViaEcrEndpointsOrAdmins"]
    assert "local.network_private" in gate and "var.ecr_pull_restriction" in gate
    assert 'Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]' in text
    assert 'StringNotEquals = {"aws:SourceVpce" = local.ecr_vpce_ids}' in text
    assert "local.knowledge_admin_arns_effective" in text and "aws_iam_role.runtime_exec.arn" in text
    assert "ArnNotLike" in text
    assert re.findall(r"BoolIfExists = \{([^}]*)\}", text) == ['"aws:PrincipalIsAWSService" = "false"']
    for push in ("PutImage", "InitiateLayerUpload", "UploadLayerPart", "CompleteLayerUpload"):
        assert push not in text
    assert "aws:ViaAWSService" not in tf["raw"]["ecr.tf"]
    for name in ("orchestrator", "mcp_runtime"):
        repo_pol = tf["resource"][("aws_ecr_repository_policy", name)]
        assert _unq(repo_pol["policy"]) == "local.ecr_repo_policy"
        assert f"aws_ecr_repository_policy.{name}" in _s(_runtime(tf, "orchestrator")["depends_on"])


# 21
def test_kb_key_knowledge_admin_statement(tf):
    expr = _s(tf["resource"][("aws_kms_key", "kb")]["policy"])
    sts = _json_statements(expr)
    assert set(sts) == {"KeyAdministration", "AllowS3Use", "AllowRuntimeRoleUse", "KnowledgeAdminS3Use"}
    for sid in ("KeyAdministration", "AllowS3Use", "AllowRuntimeRoleUse"):
        assert sts[sid][1] == "", sid  # unchanged, both modes
    text, gate = sts["KnowledgeAdminS3Use"]
    assert "local.network_private" in gate
    assert 'ArnLike = {"aws:PrincipalArn" = local.knowledge_admin_arns_effective}' in text
    assert 'StringEquals = {"kms:ViaService" = "s3.${local.region}.amazonaws.com"}' in text
    assert ":root" in text


# 23 (IAM half)
_BOUNDARY_ACTIONS = {"bedrock-agentcore:" + a for a in (
    "PutResourcePolicy", "DeleteResourcePolicy", "CreateAgentRuntime", "UpdateAgentRuntime",
    "CreateAgentRuntimeEndpoint", "UpdateAgentRuntimeEndpoint", "CreateGateway", "UpdateGateway",
    "CreateGatewayTarget", "UpdateGatewayTarget", "UpdateMemory", "CreateHarness", "UpdateHarness",
    "CreateHarnessEndpoint", "UpdateHarnessEndpoint", "CreateCodeInterpreter", "CreateBrowser",
    # 3.4.0 (D11.4 item 3): the engine can neither re-sync nor tear down its boundary.
    "SynchronizeGatewayTargets", "DeleteGateway", "DeleteGatewayTarget", "DeleteAgentRuntime",
    "DeleteAgentRuntimeEndpoint", "DeleteMemory")}


def test_runtime_role_boundary_self_modification_deny(tf):
    doc = tf["data"][("aws_iam_policy_document", "runtime_exec_inline")]
    st = _by_sid(doc)["DenyBoundarySelfModification"]
    # Unconditional since 3.4.0: a static statement, so it renders in BOTH modes.
    assert "__for_each__" not in st
    assert st in doc["statement"]
    assert _unq(st["effect"]) == "Deny"
    assert [_unq(r) for r in st["resources"]] == ["*"]
    assert {_unq(a) for a in st["actions"]} == _BOUNDARY_ACTIONS
    # No Allow in iam.tf gained a bedrock-agentcore control-plane action.
    for s in _statements(doc):
        if _unq(s["effect"]) == "Allow":
            assert not {_unq(a) for a in s.get("actions", [])} & _BOUNDARY_ACTIONS


# 23b (3.4.0, D11.4 items 1-2): the opt-in diagram planner grants are conditional and scoped.
def test_diagram_planner_iam_is_opt_in(tf):
    doc = tf["data"][("aws_iam_policy_document", "runtime_exec_inline")]
    sts = _by_sid(doc)
    gr = sts["DiagramPlannerGuardrail"]
    assert gr["__for_each__"] == '${var.diagram_planner == "bedrock" ? [1] : []}'
    assert [_unq(a) for a in gr["actions"]] == ["bedrock:ApplyGuardrail"]
    assert "guardrail/${var.guardrail_id}" in _s(gr["resources"])
    xr = sts["DiagramPlannerCrossRegionModels"]
    assert "length(var.diagram_planner_model_regions) > 0" in xr["__for_each__"]
    assert [_unq(a) for a in xr["actions"]] == ["bedrock:InvokeModel"]
    assert "::foundation-model/*" in _s(xr["resources"])
    conds = _conds(xr)
    assert [(t, v) for t, v, _, _ in conds] == [("StringLike", "bedrock:InferenceProfileArn")]
    assert ":inference-profile/*" in conds[0][2]
    # BedrockModelInvocation is unchanged (this Region only).
    bmi = sts["BedrockModelInvocation"]
    assert "${local.region}" in _s(bmi["resources"]) and len(bmi["resources"]) == 2


# 24
def test_trace_tool_content_disables_aws_mcp_in_private_mode(tf):
    env = _s(tf["locals"]["runtime_env"])
    assert "local.observability_env" in env
    obs = _s(tf["locals"]["observability_env"])
    assert "local.network_private" in obs and "var.trace_tool_content" in obs
    assert "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS" in obs and "aws_mcp" in obs
    assert _unq(tf["variable"]["trace_tool_content"]["default"]) is False


# 25 (3.2.2): the VPC the module creates has flow logs (CKV2_AWS_11), encrypted and scoped.
def test_created_vpc_has_encrypted_flow_logs(tf):
    mod = tf["mod"]["resource"]
    fl = mod[("aws_flow_log", "this")]
    assert "var.create_vpc" in _gate(fl)
    assert _unq(fl["traffic_type"]) == "ALL" and _unq(fl["log_destination_type"]) == "cloud-watch-logs"
    assert "aws_vpc.this[0].id" in _s(fl["vpc_id"])
    lg = mod[("aws_cloudwatch_log_group", "flow_logs")]
    assert _unq(lg["retention_in_days"]) == 365 and "aws_kms_key.flow_logs[0].arn" in _s(lg["kms_key_id"])
    assert _unq(mod[("aws_kms_key", "flow_logs")]["enable_key_rotation"]) is True
    trust = _s(tf["mod"]["data"][("aws_iam_policy_document", "flow_logs_assume")])
    assert "vpc-flow-logs.amazonaws.com" in trust and "aws:SourceAccount" in trust and "aws:SourceArn" in trust
    pol = _s(tf["mod"]["data"][("aws_iam_policy_document", "flow_logs")])
    assert "logs:CreateLogGroup" not in pol and '"*"' not in pol
    # No checkov flow-log skip remains on the VPC.
    assert "checkov:skip=CKV2_AWS_11" not in (TF_DIR / "modules" / "private-network" / "main.tf").read_text(encoding="utf-8")
