"""Static tests for the shared and optional Terraform modules (no AWS, no plan).

Covers the ported backend-bootstrap and shared-identity modules and the platform/ stack:
companion neutrality, migration equivalence with the original shared-platform addresses, and the
platform outputs lining up with the companion consumer inputs.

Never opened: backend.tf, *.tfvars, state, plan files, .terraform/, generated/.
"""
import json
import re
from pathlib import Path

import pytest

hcl2 = pytest.importorskip("hcl2")

ROOT = Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "agentcore" / "terraform"
MODULES = TF_DIR / "modules"
PLATFORM = TF_DIR / "platform"
_EXCLUDED = {"backend.tf", "override.tf"}


def _unq(value):
    if not isinstance(value, str):
        return value
    v = value.strip()
    if v.startswith("${") and v.endswith("}"):
        v = v[2:-1]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


def _s(obj):
    return json.dumps(obj) if not isinstance(obj, str) else obj


def _tf_files(d):
    return sorted(p for p in Path(d).glob("*.tf")
                  if p.name not in _EXCLUDED and not p.name.endswith("_override.tf"))


def load(d):
    """Parse every *.tf in d into {resource, data, module, variable, output, locals, check, moved, raw}."""
    m = {"resource": {}, "data": {}, "module": {}, "variable": {}, "output": {}, "locals": {},
         "check": {}, "moved": [], "raw": {}, "files": {}}
    for p in _tf_files(d):
        text = p.read_text(encoding="utf-8")
        m["raw"][p.name] = text
        with p.open(encoding="utf-8") as fh:
            doc = hcl2.load(fh)
        m["files"][p.name] = doc
        for kind in ("resource", "data"):
            for entry in doc.get(kind, []):
                for rtype, named in entry.items():
                    if rtype.startswith("__"):
                        continue
                    for rname, body in named.items():
                        if not rname.startswith("__"):
                            m[kind][(_unq(rtype), _unq(rname))] = dict(body, __file__=p.name)
        for kind in ("module", "variable", "output", "check"):
            for entry in doc.get(kind, []):
                for name, body in entry.items():
                    if not name.startswith("__"):
                        m[kind][_unq(name)] = dict(body, __file__=p.name)
        for entry in doc.get("locals", []):
            for k, v in entry.items():
                if not k.startswith("__"):
                    m["locals"][k] = v
        for entry in doc.get("moved", []):
            m["moved"].append(entry)
    return m


@pytest.fixture(scope="module")
def backend():
    return load(MODULES / "backend-bootstrap")


@pytest.fixture(scope="module")
def identity():
    return load(MODULES / "shared-identity")


@pytest.fixture(scope="module")
def platform():
    return load(PLATFORM)


@pytest.fixture(scope="module")
def root():
    return load(TF_DIR)


def _aws_constraint(model):
    for doc in model["files"].values():
        for tblock in doc.get("terraform", []):
            for rp in tblock.get("required_providers", []):
                if "aws" in rp:
                    return _unq(rp["aws"]["version"])
    return None


def _gate(body):
    return _s(body.get("count", "")) + _s(body.get("for_each", ""))


# ---- Part 1: ported shared modules ---------------------------------------------------------

def test_shared_modules_companion_neutral(backend, identity, platform):
    dirs = [MODULES / "backend-bootstrap", MODULES / "shared-identity", PLATFORM]
    for d in dirs:
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix in (".tf", ".md", ".hcl", ".example") and ".terraform" not in p.parts:
                text = p.read_text(encoding="utf-8")
                # No per-companion resource-server scope (<prefix>-engine): it stays in each
                # companion's own stack. The configuration (.tf) also hardcodes no companion
                # name; companion keys such as `gac` appear only as `apps` examples in docs,
                # tfvars examples and tests.
                assert not re.search(r"-engine\b", text, re.IGNORECASE), p
                if p.suffix == ".tf":
                    assert not re.search(r"\bgac\b", text, re.IGNORECASE), p
                # No account id other than the documented placeholder.
                for acct in re.findall(r"(?<![0-9])[0-9]{12}(?![0-9])", text):
                    assert acct == "123456789012", (p, acct)
    assert "default" not in identity["variable"]["apps"]
    assert "default" not in platform["variable"]["apps"]
    assert "default" not in platform["variable"]["aws_region"]
    assert "default" not in platform["variable"]["state_bucket_name"]


def test_shared_modules_migration_equivalence(backend, identity, platform):
    # Module names fix the state addresses (module.backend.*, module.identity.*).
    assert _unq(platform["module"]["backend"]["source"]) == "../modules/backend-bootstrap"
    assert _unq(platform["module"]["identity"]["source"]) == "../modules/shared-identity"
    expected_backend = {
        ("aws_s3_bucket", "state"), ("aws_s3_bucket_versioning", "state"),
        ("aws_s3_bucket_server_side_encryption_configuration", "state"),
        ("aws_s3_bucket_public_access_block", "state"), ("aws_s3_bucket_policy", "state_tls_only"),
        ("aws_s3_bucket", "state_logs"), ("aws_s3_bucket_versioning", "state_logs"),
        ("aws_s3_bucket_server_side_encryption_configuration", "state_logs"),
        ("aws_s3_bucket_public_access_block", "state_logs"),
        ("aws_s3_bucket_policy", "state_logs_tls_only"), ("aws_s3_bucket_logging", "state"),
        ("aws_s3_bucket_lifecycle_configuration", "state"), ("aws_dynamodb_table", "locks"),
    }
    # Added in 3.2.2 (Checkov CKV2_AWS_61 / CKV2_AWS_62): new addresses only, so adopting an
    # existing platform state plans these three creates. The 3.2.2 access-log delivery fix
    # updates state_logs encryption and policy in place (no new address, no replacement).
    hardening_additions = {
        ("aws_s3_bucket_lifecycle_configuration", "state_logs"),
        ("aws_s3_bucket_notification", "state"), ("aws_s3_bucket_notification", "state_logs"),
    }
    assert set(backend["resource"]) == expected_backend | hardening_additions
    assert set(identity["resource"]) == {("aws_cognito_user_pool", "shared"),
                                         ("aws_cognito_user_pool_domain", "shared"),
                                         ("aws_cognito_user_group", "app")}
    # Names and templates that would force replacement if changed.
    assert _unq(backend["resource"][("aws_s3_bucket", "state")]["bucket"]) == "var.state_bucket_name"
    assert _unq(backend["resource"][("aws_s3_bucket", "state_logs")]["bucket"]) == "${var.state_bucket_name}-logs"
    assert _unq(identity["resource"][("aws_cognito_user_pool", "shared")]["name"]) == "${var.name_prefix}-pool"
    assert _unq(identity["locals"]["pool_domain"]) == "${var.name_prefix}-${data.aws_caller_identity.current.account_id}"
    grp = identity["resource"][("aws_cognito_user_group", "app")]
    assert _unq(grp["name"]) == "${each.key}-users"
    assert _unq(grp["description"]) == "Entitlement group for the ${each.value} companion flavor"
    # prevent_destroy on both buckets and the pool.
    for model, key in ((backend, ("aws_s3_bucket", "state")), (backend, ("aws_s3_bucket", "state_logs")),
                       (identity, ("aws_cognito_user_pool", "shared"))):
        assert model["resource"][key]["lifecycle"][0]["prevent_destroy"] is True, key
    # All four Block Public Access settings stay on.
    for name in ("state", "state_logs"):
        bpa = backend["resource"][("aws_s3_bucket_public_access_block", name)]
        for flag in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
            assert bpa[flag] is True, (name, flag)
    # State access logs are deliverable: S3 server access logging needs an SSE-S3 target and a
    # bucket-policy grant to the log-delivery service (ACLs are disabled on new buckets).
    logs_sse = _s(backend["resource"][("aws_s3_bucket_server_side_encryption_configuration", "state_logs")])
    assert "AES256" in logs_sse and "aws:kms" not in logs_sse
    logs_policy = _s(backend["resource"][("aws_s3_bucket_policy", "state_logs_tls_only")])
    assert "logging.s3.amazonaws.com" in logs_policy and "aws:SourceArn" in logs_policy
    assert "DenyInsecureTransport" in logs_policy
    # Provider constraints. The lock file is gitignored, so the platform root pins the provider
    # line (never older than the 6.68.0 that wrote an adopted state); modules stay within v6.
    assert _aws_constraint(backend) == ">= 6.0.0, < 7.0.0"
    assert _aws_constraint(identity) == ">= 6.0.0, < 7.0.0"
    assert _aws_constraint(platform) == "~> 6.68"
    # Everything in the platform other than module.backend / module.identity is gated on a toggle.
    toggles = ("var.enable_private_network", "var.enable_resolver_inbound_endpoint",
               "terraform_data.platform_guards", "module.network")
    for kind in ("resource", "data"):
        for key, body in platform[kind].items():
            assert any(t in _gate(body) for t in toggles), (kind, key)
    for name, body in platform["module"].items():
        if name in ("backend", "identity"):
            continue
        assert any(t in _gate(body) for t in toggles), name


def test_gitignore_covers_platform_secrets():
    lines = [ln.strip() for ln in (TF_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()]
    for pat in ("terraform.tfvars", "backend.tf", "*.tfstate", "*.tfstate.*", ".terraform/", "*.zip",
                ".terraform.lock.hcl"):
        assert pat in lines, pat
        assert ("/" + pat) not in lines, pat


# ---- Part 2: modules/private-network and the moved blocks ----------------------------------

@pytest.fixture(scope="module")
def net():
    return load(MODULES / "private-network")


# The 15 managed resources declared in agentcore/terraform/network.tf at e1bd2c8 (frozen).
_NETWORK_TF_BASELINE = [
    "aws_vpc.companion", "aws_default_security_group.companion", "aws_subnet.private",
    "aws_route_table.private", "aws_route_table_association.private",
    "aws_vpc_endpoint.interface", "aws_vpc_endpoint.s3", "aws_security_group.runtime",
    "aws_security_group.vpce_client", "aws_security_group.vpce_runtime",
    "aws_vpc_security_group_egress_rule.runtime_to_vpce",
    "aws_vpc_security_group_egress_rule.runtime_to_s3",
    "aws_vpc_security_group_egress_rule.runtime_to_existing_endpoints",
    "aws_vpc_security_group_ingress_rule.vpce_from_runtime",
    "aws_vpc_security_group_ingress_rule.vpce_client_from_cidrs",
]


def test_moved_blocks_cover_network_tf_baseline(root, net):
    moves = [(_unq(m["from"]), _unq(m["to"])) for m in root["moved"]]
    froms = [f for f, _ in moves]
    assert len(froms) == len(set(froms)), "duplicate moved.from"
    by_from = dict(moves)
    for addr in _NETWORK_TF_BASELINE:
        rtype, rname = addr.split(".")
        if (rtype, rname) in root["resource"]:
            assert addr not in by_from, f"{addr} is still declared and also moved"
            continue
        assert addr in by_from, f"{addr} left network.tf without a moved block"
        to = by_from[addr]
        m = re.fullmatch(r"module\.private_network\[0\]\.([a-z0-9_]+)\.([a-z0-9_]+)", to)
        assert m and m.group(1) == rtype, (addr, to)
        assert (m.group(1), m.group(2)) in net["resource"], f"{to} not declared in the module"
    # Every moved block is one of the baseline addresses.
    assert set(froms) <= set(_NETWORK_TF_BASELINE)
    assert len(froms) == 10


def test_moved_targets_keep_replacement_sensitive_strings(net):
    sg = net["resource"]
    assert _unq(sg[("aws_security_group", "vpce_client")]["name"]) == "${var.name_prefix}-vpce-client"
    assert _unq(sg[("aws_security_group", "vpce_client")]["description"]) == \
        "AgentCore gateway/data-plane endpoints: HTTPS from the runtime SG and approved client CIDRs"
    assert _unq(sg[("aws_security_group", "vpce_runtime")]["name"]) == "${var.name_prefix}-vpce-runtime"
    assert _unq(sg[("aws_security_group", "vpce_runtime")]["description"]) == \
        "Runtime-only endpoints (ECR, Logs, X-Ray, Bedrock, Comprehend): HTTPS from the runtime SG only"
    rule = sg[("aws_vpc_security_group_ingress_rule", "vpce_client_from_cidrs")]
    assert _unq(rule["description"]) == "IDE / developer network"
    assert "toset(var.client_cidrs)" in _s(rule["for_each"])
    assert _unq(sg[("aws_vpc", "this")]["tags"]["Name"]) == "${var.name_prefix}-private"
    assert _unq(sg[("aws_vpc", "this")]["cidr_block"]) == "var.vpc_cidr"
    assert _unq(sg[("aws_subnet", "private")]["tags"]["Name"]) == "${var.name_prefix}-private-${count.index}"
    assert _unq(sg[("aws_subnet", "private")]["cidr_block"]) == "cidrsubnet(var.vpc_cidr, 4, count.index)"
    assert _unq(sg[("aws_subnet", "private")]["count"]) == "var.create_vpc ? 2 : 0"
    assert _unq(sg[("aws_route_table", "private")]["tags"]["Name"]) == "${var.name_prefix}-private"
    ep = sg[("aws_vpc_endpoint", "interface")]
    assert _unq(ep["for_each"]) == "var.interface_endpoint_services"
    assert _unq(ep["tags"]["Name"]) == "${var.name_prefix}-${each.key}"
    assert _unq(sg[("aws_vpc_endpoint", "s3")]["tags"]["Name"]) == "${var.name_prefix}-s3"
    # No module-level depends_on on the companion call (it would defer the module's data sources).
    assert "depends_on" not in load(TF_DIR)["module"]["private_network"]


def _platform_and_probe_models(platform, root):
    out = [("platform", platform)]
    probe = {k: v for k, v in root["resource"].items() if v["__file__"] == "probe.tf"}
    out.append(("probe", {"resource": probe, "raw": {"probe.tf": root["raw"].get("probe.tf", "")}}))
    return out


def test_no_internet_path_in_modules_platform_and_probe(net, platform, root):
    banned = {"aws_internet_gateway", "aws_nat_gateway", "aws_eip", "aws_egress_only_internet_gateway",
              "aws_lambda_function_url", "aws_route"}
    for label, model in [("module", net)] + _platform_and_probe_models(platform, root):
        types = {t for (t, _) in model["resource"]}
        assert not types & banned, (label, types & banned)
        for (t, n), body in model["resource"].items():
            if t == "aws_route_table":
                assert "route" not in body, (label, n)
    for name, raw in net["raw"].items():
        if name != "variables.tf":
            assert "0.0.0.0/0" not in raw and "::/0" not in raw, name


def test_private_network_rejects_control_plane(net):
    conds = " ".join(_s(v.get("validation", "")) for v in
                     (net["variable"]["interface_endpoint_services"], net["variable"]["client_endpoint_services"]))
    assert conds.count("bedrock-agentcore-control") >= 2


# ---- Shared-network isolation (commit 3) ---------------------------------------------------

def _preconditions(body):
    out = []
    for lc in body.get("lifecycle", []):
        out.extend(lc.get("precondition", []))
    return out


def _dyn_statements(doc):
    out = []
    for dyn in doc.get("dynamic", []):
        for label, body in dyn.items():
            if _unq(label) == "statement":
                for c in body.get("content", []):
                    out.append((c, _s(body.get("for_each", ""))))
    return out


def test_memory_shared_endpoint_isolation(root):
    doc = root["data"][("aws_iam_policy_document", "memory_resource")]
    static = {_unq(st.get("sid", "")) for st in doc.get("statement", [])}
    assert static == {"DenyDataPlaneNotViaEndpoint"}
    dyn = {_unq(c.get("sid", "")): (c, fe) for c, fe in _dyn_statements(doc)}
    stmt, for_each = dyn["DenyOtherPrincipalsOnSharedEndpoint"]
    assert "local.dataplane_endpoint_shared" in for_each
    assert _unq(stmt["effect"]) == "Deny"
    assert _unq(stmt["actions"]) == "local.memory_dataplane_actions"
    conds = {(_unq(c["test"]), _unq(c["variable"])): _s(c["values"]) for c in stmt["condition"]}
    assert "aws_iam_role.runtime_exec.arn" in conds[("ArnNotLike", "aws:PrincipalArn")]
    assert "aws_iam_role.memory_exec[0].arn" in conds[("ArnNotLike", "aws:PrincipalArn")]
    assert "local.knowledge_admin_arns_effective" in conds[("ArnNotLike", "aws:PrincipalArn")]
    assert "false" in conds[("Bool", "aws:ViaAWSService")]
    reused = _s(root["locals"]["dataplane_endpoint_reused"])
    assert "local.network_private" in reused and "existing_interface_endpoint_ids" in reused
    shared = _s(root["locals"]["dataplane_endpoint_shared"])
    assert "local.dataplane_endpoint_reused" in shared and "additional_dataplane_vpce_ids" in shared


def test_auth_none_refused_on_shared_dataplane_endpoint(root):
    pre = _preconditions(root["resource"][("terraform_data", "network_guards")])
    hits = [p for p in pre if "local.auth_is_none" in _s(p["condition"])
            and "local.dataplane_endpoint_shared" in _s(p["condition"])]
    assert len(hits) == 1
    assert "refused on a reused or shared bedrock-agentcore endpoint" in _s(hits[0]["error_message"])


def test_shared_endpoint_extras_check_is_warning_only(root):
    chk = root["check"]["shared_endpoint_extras"]
    asserts = [_s(a["condition"]) for a in chk["assert"]]
    assert len(asserts) == 3
    assert any("var.dataplane_endpoint_extra_actions" in a for a in asserts)
    assert any("var.extra_interface_endpoint_services" in a for a in asserts)
    assert any("var.kb_bucket_name" in a for a in asserts)
    # Never a precondition: these must not block existing deployments.
    for body in root["resource"].values():
        for p in _preconditions(body):
            assert "var.dataplane_endpoint_extra_actions" not in _s(p["condition"])


def test_resolver_inbound_endpoint_shape(net, root):
    r = net["resource"]
    ep = r[("aws_route53_resolver_endpoint", "inbound")]
    assert _unq(ep["direction"]) == "INBOUND"
    assert [_unq(p) for p in ep["protocols"]] == ["Do53"]
    assert "var.enable_resolver_inbound_endpoint" in _gate(ep)
    sg = r[("aws_security_group", "resolver_inbound")]
    assert "ingress" not in sg and "egress" not in sg
    rule = r[("aws_vpc_security_group_ingress_rule", "resolver_dns")]
    assert _unq(rule["from_port"]) == 53 and _unq(rule["to_port"]) == 53
    assert "local.resolver_cidrs" in _gate(rule)
    assert "var.client_cidrs" in _s(net["locals"]["resolver_cidrs"])
    assert _unq(root["variable"]["enable_resolver_inbound_endpoint"]["default"]) is False
    pre = _preconditions(root["resource"][("terraform_data", "network_guards")])
    assert any("var.enable_resolver_inbound_endpoint" in _s(p["condition"]) for p in pre)


# ---- Platform shared network (commit 4) ----------------------------------------------------

_CONSUMER_OUTPUTS = ["existing_user_pool_id", "existing_oidc_issuer", "vpc_id", "private_subnet_ids",
                     "private_route_table_ids", "existing_s3_gateway_endpoint_id",
                     "existing_interface_endpoint_ids", "existing_endpoint_security_group_ids",
                     "existing_client_endpoint_security_group_ids", "private_client_cidrs"]


def _consumer_input_keys(platform):
    val = platform["output"]["companion_consumer_inputs"]["value"]
    return {k for k in val if not k.startswith("__")}


def test_platform_outputs_match_companion_inputs(platform, root):
    keys = _consumer_input_keys(platform)
    assert set(_CONSUMER_OUTPUTS) <= keys
    for k in keys:
        assert k in root["variable"], f"companion_consumer_inputs.{k} is not a companion input"
    for name in _CONSUMER_OUTPUTS:
        assert name in platform["output"], name
        assert name in root["variable"], name
    # The endpoint-id filter equals the companion's accepted key set.
    filt = {_unq(k) for k in platform["locals"]["consumer_endpoint_keys"]}
    key_cond = root["variable"]["existing_interface_endpoint_ids"]["validation"][0]["condition"]
    assert filt == set(re.findall(r'"([a-z0-9.-]+)"', key_cond))
    assert "local.consumer_endpoint_keys" in _s(platform["output"]["existing_interface_endpoint_ids"]["value"])
    # The client SG output indexes the client SG only.
    client = _s(platform["output"]["existing_client_endpoint_security_group_ids"]["value"])
    assert 'endpoint_security_group_ids["client"]' in client and "runtime" not in client
    assert "endpoint_security_group_id_list" in _s(platform["output"]["existing_endpoint_security_group_ids"]["value"])


def test_platform_dataplane_actions_match_companion(platform, root):
    for name in ("dataplane_base_actions", "memory_dataplane_actions"):
        assert [_unq(a) for a in platform["locals"][name]] == [_unq(a) for a in root["locals"][name]], name


def test_platform_s3_kb_statements_have_resource_account(platform):
    doc = platform["data"][("aws_iam_policy_document", "network_s3_gateway")]
    sts = {_unq(st["sid"]): st for st in doc["statement"]}
    assert set(sts) == {"EcrLayerBucket", "KbBucketObjects", "KbBucketList"}
    for sid in ("KbBucketObjects", "KbBucketList"):
        conds = [(_unq(c["test"]), _unq(c["variable"])) for c in sts[sid]["condition"]]
        assert ("StringEquals", "aws:ResourceAccount") in conds, sid
    assert "kb_bucket_arn_patterns_effective" in _s(sts["KbBucketList"]["resources"])
    # No endpoint policy names the control plane.
    assert "bedrock-agentcore-control" not in platform["raw"]["network_policies.tf"]


def _kb_pattern_ok(p):
    """Python mirror of the platform kb_bucket_arn_patterns validation (RE2 == re for this)."""
    if not re.fullmatch(r"arn:aws[a-z-]*:s3:::[a-z0-9.*-]{3,63}", p):
        return False
    parts = p.split(":::")
    if len(parts) < 2 or parts[1].startswith("*"):
        return False
    return "*" not in p or "-kb-" in p


def test_kb_bucket_arn_patterns_validation(platform):
    cond = _s(platform["variable"]["kb_bucket_arn_patterns"]["validation"][0]["condition"])
    assert "^arn:aws[a-z-]*:s3:::[a-z0-9.*-]{3,63}$" in cond
    assert 'try(!startswith(split(\\":::\\", p)[1], \\"*\\"), false)' in cond or \
        'try(!startswith(split(":::", p)[1], "*"), false)' in cond
    assert "strcontains(p, \\\"-kb-\\\")" in cond or 'strcontains(p, "-kb-")' in cond
    for bad in ("arn:aws:s3:::*", "arn:aws:s3:::*-kb-*", "arn:aws:s3:::a*", "not-an-arn"):
        assert not _kb_pattern_ok(bad), bad
    for good in ("arn:aws:s3:::team-knowledge-store", "arn:aws:s3:::gac-kb-123456789012-ap-southeast-2-*"):
        assert _kb_pattern_ok(good), good


def test_client_sg_subset_guard(root):
    pre = _preconditions(root["resource"][("terraform_data", "network_guards")])
    assert any("var.existing_client_endpoint_security_group_ids" in _s(p["condition"])
               and "var.existing_endpoint_security_group_ids" in _s(p["condition"]) for p in pre)


# ---- Private access probe (commit 5) -------------------------------------------------------

def _probe_blocks(root):
    out = {}
    for kind in ("resource", "data"):
        for key, body in root[kind].items():
            if body["__file__"] == "probe.tf":
                out[(kind,) + key] = body
    return out


def test_probe_disabled_by_default_and_gated(root):
    assert _unq(root["variable"]["enable_private_access_probe"]["default"]) is False
    blocks = _probe_blocks(root)
    assert blocks
    for key, body in blocks.items():
        assert "local.probe_enabled" in _gate(body), key
    gate = _s(root["locals"]["probe_enabled"])
    for part in ("var.enable_private_access_probe", "local.network_private", "local.gateway_enabled"):
        assert part in gate
    types = {k[1] for k in blocks}
    assert "aws_lambda_permission" not in types and "aws_lambda_function_url" not in types
    pre = _preconditions(root["resource"][("terraform_data", "network_guards")])
    conds = [_s(p["condition"]) for p in pre]
    assert any("var.enable_private_access_probe" in c and "local.network_private" in c and "client_endpoints_reused" not in c for c in conds)
    assert any("var.enable_private_access_probe" in c and "local.gateway_enabled" in c for c in conds)


def test_probe_network_reach(root):
    blocks = _probe_blocks(root)
    raw = root["raw"]["probe.tf"]
    assert "var.existing_endpoint_security_group_ids" not in raw
    for name in ("probe_to_existing_endpoints",):
        assert "var.existing_client_endpoint_security_group_ids" in _gate(
            blocks[("resource", "aws_vpc_security_group_egress_rule", name)])
    assert "var.existing_client_endpoint_security_group_ids" in _gate(
        blocks[("resource", "aws_vpc_security_group_ingress_rule", "existing_endpoint_from_probe")])
    for key, body in blocks.items():
        if key[1].startswith("aws_vpc_security_group_"):
            text = _s(body)
            assert 'vpce_sg_ids[\\"runtime\\"]' not in text and "endpoint_runtime_security_group_id" not in text, key
            assert "cidr_ipv4" not in body and "cidr_ipv6" not in body, key
            assert _unq(body["from_port"]) == 443 and _unq(body["to_port"]) == 443, key
    sg = blocks[("resource", "aws_security_group", "probe")]
    assert "ingress" not in sg and "egress" not in sg
    # No ingress rule names the probe SG as its target.
    for key, body in blocks.items():
        if key[1] == "aws_vpc_security_group_ingress_rule":
            assert "aws_security_group.probe" in _s(body["referenced_security_group_id"])
    pre = _preconditions(root["resource"][("terraform_data", "network_guards")])
    conds = [_s(p["condition"]) for p in pre]
    assert any("local.client_endpoints_reused" in c and "var.existing_client_endpoint_security_group_ids" in c for c in conds)
    assert any("contains(var.existing_endpoint_security_group_ids" in c for c in conds)


_ENI = {"ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets",
        "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"}
_XRAY = {"xray:PutTraceSegments", "xray:PutTelemetryRecords"}


def test_probe_iam_least_privilege(root):
    raw = root["raw"]["probe.tf"]
    # IAM policies come from policy documents; the only jsonencode is the KMS key policy (inline,
    # like the KB and ECR keys).
    assert raw.count("jsonencode") == 1
    assert "jsonencode" in _s(root["resource"][("aws_kms_key", "probe")]["policy"])
    stmts = root["locals"]["probe_statements"]
    by_sid = {_unq(s["sid"]): s for s in stmts}
    assert set(by_sid) == {"ProbeLogs", "VpcEni", "XRayTracing", "ProbeDeadLetterQueue"}
    for s in stmts:
        assert "not_actions" not in s and "actions" in s
    acts = {sid: {_unq(a) for a in s["actions"]} for sid, s in by_sid.items()}
    assert acts["ProbeLogs"] == {"logs:CreateLogStream", "logs:PutLogEvents"}
    assert acts["VpcEni"] == _ENI and acts["XRayTracing"] == _XRAY
    # The DLQ grant is SendMessage on the probe's own queue only.
    assert acts["ProbeDeadLetterQueue"] == {"sqs:SendMessage"}
    assert [_unq(r) for r in by_sid["ProbeDeadLetterQueue"]["resources"]] == ["local.probe_dlq_arn"]
    prefixes = {a.split(":")[0] for v in acts.values() for a in v}
    assert prefixes == {"logs", "ec2", "xray", "sqs"}
    for sid in ("VpcEni", "XRayTracing"):
        assert [_unq(r) for r in by_sid[sid]["resources"]] == ["*"]
        assert by_sid[sid]["condition"] is None or _unq(by_sid[sid]["condition"]) is None
    logs = by_sid["ProbeLogs"]
    assert "${local.probe_log_group_arn}:*" in _s(logs["resources"])
    assert "log-group:${local.probe_log_group_name}" in _s(root["locals"]["probe_log_group_arn"])
    assert _unq(root["locals"]["probe_log_group_name"]) == "/aws/lambda/${local.probe_name}"
    cond = logs["condition"]
    assert _unq(cond["test"]) == "ArnEquals" and _unq(cond["variable"]) == "lambda:SourceFunctionArn"
    # Both the inline policy and the boundary render the same shared list.
    for name in ("probe", "probe_boundary"):
        doc = root["data"][("aws_iam_policy_document", name)]
        fe = [_s(body.get("for_each")) for d in doc["dynamic"] for label, body in d.items() if _unq(label) == "statement"]
        assert fe and all("local.probe_statements" in f for f in fe), name
    role = root["resource"][("aws_iam_role", "probe")]
    assert "data.aws_iam_policy_document.probe_assume[0].json" in _s(role["assume_role_policy"])
    assert "aws_iam_policy.probe_boundary[0].arn" in _s(role["permissions_boundary"])
    assert "data.aws_iam_policy_document.probe[0].json" in _s(root["resource"][("aws_iam_role_policy", "probe")]["policy"])
    assume = root["data"][("aws_iam_policy_document", "probe_assume")]
    (st,) = assume["statement"]
    assert "condition" not in st
    assert [_unq(a) for a in st["actions"]] == ["sts:AssumeRole"]
    assert [_unq(i) for i in st["principals"][0]["identifiers"]] == ["lambda.amazonaws.com"]
    # The probe role is never a knowledge admin.
    assert "aws_iam_role.probe" not in _s(root["locals"]["knowledge_admin_arns_effective"])


def test_probe_function_settings(root):
    fn = root["resource"][("aws_lambda_function", "private_access_probe")]
    assert _unq(fn["timeout"]) == 120
    lc = fn["logging_config"][0]
    assert _unq(lc["log_format"]) == "JSON" and _unq(lc["application_log_level"]) == "INFO"
    assert _unq(fn["tracing_config"][0]["mode"]) == "Active"
    assert "vpc_config" in fn
    # Scanner hardening (3.2.2): CMK on the environment and the log group, a DLQ, a year of logs.
    assert "aws_kms_key.probe[0].arn" in _s(fn["kms_key_arn"])
    assert "aws_sqs_queue.probe_dlq[0].arn" in _s(fn["dead_letter_config"])
    lg = root["resource"][("aws_cloudwatch_log_group", "probe")]
    assert _unq(lg["retention_in_days"]) == 365 and "aws_kms_key.probe[0].arn" in _s(lg["kms_key_id"])
    assert _unq(root["resource"][("aws_kms_key", "probe")]["enable_key_rotation"]) is True
    assert _unq(root["resource"][("aws_sqs_queue", "probe_dlq")]["sqs_managed_sse_enabled"]) is True
    env = fn["environment"][0]["variables"]
    assert set(env) == {"GAC_PROBE_GATEWAY_URL", "GAC_PROBE_RUNTIME_ARN", "GAC_PROBE_REGION",
                        "GAC_PROBE_VPC_CIDR", "GAC_PROBE_TOOL_NAME", "GAC_PROBE_DIAGRAM_TOOL_NAME"}
    # Diagram mode calls one tool, fixed here (never caller-chosen).
    assert _unq(env["GAC_PROBE_DIAGRAM_TOOL_NAME"]) == "gac___draw_diagram"
    assert "token" not in _s(env).lower()
    types = {t for (t, _) in root["resource"]}
    assert "aws_lambda_function_url" not in types
    perms = [n for (t, n) in root["resource"] if t == "aws_lambda_permission"]
    assert perms == ["interceptor_gateway_invoke"]
    cmd = _s(root["locals"]["private_access_probe_invoke_command"])
    assert "env.GAC_GATEWAY_TOKEN" in cmd and "umask 077" in cmd and "rm -f" in cmd
    assert "$GAC_GATEWAY_TOKEN" not in cmd


def test_engine_resource_server_identifier(root):
    v = root["variable"]["engine_resource_server_identifier"]
    assert _unq(v["default"]) == "gac-engine"
    assert _unq(root["locals"]["m2m_resource_server"]) == "var.engine_resource_server_identifier"
    assert "local.m2m_resource_server" in _s(root["resource"][("aws_cognito_resource_server", "engine")]["identifier"])
