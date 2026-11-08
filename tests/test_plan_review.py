"""scripts/plan_review.py on fixture `terraform show -json` documents (D27.3, AC 27, AC 30).

The fixtures are built here as minimal plan documents (format_version, planned_values,
resource_changes); no Terraform or AWS is involved.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "plan_review.py"
sys.path.insert(0, str(ROOT / "scripts"))
import plan_review  # noqa: E402

SENTINEL = "sentinel-attribute-value-never-printed"


def _rc(address, rtype, name, actions, before=None, after=None, mode="managed", after_unknown=None):
    return {"address": address, "mode": mode, "type": rtype, "name": name,
            "change": {"actions": actions, "before": before, "after": after,
                       "after_unknown": after_unknown or {}}}


def _plan(*changes):
    return {"format_version": "1.2", "terraform_version": "1.16.4",
            "planned_values": {"root_module": {}}, "resource_changes": list(changes)}


def _run(tmp_path, doc, *flags, raw=None):
    path = tmp_path / "plan.json"
    path.write_text(raw if raw is not None else json.dumps(doc))
    proc = subprocess.run([sys.executable, str(SCRIPT), *flags, str(path)],
                          capture_output=True, text=True, timeout=60)
    return proc


SAFE = _plan(
    _rc("aws_bedrockagentcore_agent_runtime.mcp_runtime", "aws_bedrockagentcore_agent_runtime",
        "mcp_runtime", ["update"], before={"environment_variables": {"A": SENTINEL}, "role_arn": "r"},
        after={"environment_variables": {"A": "new-" + SENTINEL}, "role_arn": "r"}),
    _rc("terraform_data.gateway_tool_sync[0]", "terraform_data", "gateway_tool_sync", ["create"],
        after={"triggers_replace": [SENTINEL]}),
    _rc("aws_iam_role_policy.runtime_exec_inline", "aws_iam_role_policy", "runtime_exec_inline",
        ["update"], before={"policy": SENTINEL}, after={"policy": None},
        after_unknown={"policy": True}),
    _rc("aws_s3_bucket.kb", "aws_s3_bucket", "kb", ["no-op"], before={"bucket": SENTINEL},
        after={"bucket": SENTINEL}),
    _rc("null_resource.build_push_mcp_runtime[0]", "null_resource", "build_push_mcp_runtime",
        ["delete", "create"]),
    _rc("data.aws_cognito_user_groups.entitlement[0]", "aws_cognito_user_groups", "entitlement",
        ["read"], mode="data"),
)


def test_lists_every_change_by_action_and_hides_no_op(tmp_path):
    proc = _run(tmp_path, SAFE)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert "update   aws_bedrockagentcore_agent_runtime.mcp_runtime  (environment_variables)" in lines
    assert "create   terraform_data.gateway_tool_sync[0]" in lines
    assert "update   aws_iam_role_policy.runtime_exec_inline  (policy)" in lines
    assert "replace  null_resource.build_push_mcp_runtime[0]" in lines
    assert "read     data.aws_cognito_user_groups.entitlement[0]" in lines
    assert not any("aws_s3_bucket.kb" in line for line in lines)
    assert "Totals: 1 create, 2 update, 0 delete, 1 replace, 1 read" in lines
    assert "plan_review: PASS" in proc.stdout


def test_never_prints_attribute_values(tmp_path):
    proc = _run(tmp_path, SAFE)
    assert SENTINEL not in proc.stdout + proc.stderr


@pytest.mark.parametrize("rtype,name,address", [
    ("aws_cognito_user_pool", "this", "aws_cognito_user_pool.this[0]"),
    ("aws_cognito_user_pool_client", "frontdoor", "aws_cognito_user_pool_client.frontdoor[0]"),
    ("aws_cognito_user_pool_domain", "this", "aws_cognito_user_pool_domain.this[0]"),
    ("aws_cognito_resource_server", "engine", "aws_cognito_resource_server.engine[0]"),
    ("aws_cognito_user_group", "entitlement", "aws_cognito_user_group.entitlement[0]"),
    ("aws_s3_bucket", "kb", "aws_s3_bucket.kb"),
    ("aws_s3_bucket", "kb_logs", "aws_s3_bucket.kb_logs"),
    ("aws_s3_bucket_public_access_block", "kb", "aws_s3_bucket_public_access_block.kb"),
    ("aws_kms_key", "kb", "aws_kms_key.kb"),
    ("aws_kms_alias", "kb", "aws_kms_alias.kb"),
    ("aws_ecr_repository", "orchestrator", "aws_ecr_repository.orchestrator"),
    ("aws_bedrockagentcore_memory", "this", "aws_bedrockagentcore_memory.this[0]"),
    ("aws_vpc", "this", "module.private_network[0].aws_vpc.this[0]"),
    ("aws_subnet", "private", "module.private_network[0].aws_subnet.private[0]"),
    ("aws_vpc_endpoint", "interface", 'module.private_network[0].aws_vpc_endpoint.interface["logs"]'),
    ("aws_bedrockagentcore_gateway", "this", "aws_bedrockagentcore_gateway.this[0]"),
    ("aws_bedrockagentcore_gateway_target", "mcp_engine", "aws_bedrockagentcore_gateway_target.mcp_engine[0]"),
    ("aws_bedrockagentcore_agent_runtime", "orchestrator", "aws_bedrockagentcore_agent_runtime.orchestrator"),
    ("aws_bedrockagentcore_oauth2_credential_provider", "gateway_m2m",
     "aws_bedrockagentcore_oauth2_credential_provider.gateway_m2m[0]"),
])
@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"]])
def test_protected_delete_or_replace_exits_1_and_names_address(tmp_path, rtype, name, address, actions):
    doc = _plan(*SAFE["resource_changes"], _rc(address, rtype, name, actions))
    proc = _run(tmp_path, doc)
    assert proc.returncode == 1
    assert address in [line.split(": ", 1)[1] for line in proc.stdout.splitlines()
                       if line.startswith("PROTECTED ")]
    assert "plan_review: FAIL" in proc.stdout


def test_non_kb_bucket_is_not_protected(tmp_path):
    doc = _plan(_rc("module.state.aws_s3_bucket.state", "aws_s3_bucket", "state", ["delete"]))
    assert _run(tmp_path, doc).returncode == 0


def test_protected_update_or_create_is_listed_not_failed(tmp_path):
    doc = _plan(
        _rc("aws_bedrockagentcore_gateway.this[0]", "aws_bedrockagentcore_gateway", "this", ["update"],
            before={"authorizer_configuration": [SENTINEL]}, after={"authorizer_configuration": []}),
        _rc("aws_s3_bucket_public_access_block.kb", "aws_s3_bucket_public_access_block", "kb", ["update"],
            before={"block_public_acls": True}, after={"block_public_acls": False}),
    )
    proc = _run(tmp_path, doc)
    assert proc.returncode == 0
    assert "update   aws_bedrockagentcore_gateway.this[0]  (authorizer_configuration)" in proc.stdout
    assert "update   aws_s3_bucket_public_access_block.kb  (block_public_acls)" in proc.stdout


@pytest.mark.parametrize("actions", [["create"], ["update"], ["delete"], ["delete", "create"]])
@pytest.mark.parametrize("address", [
    "aws_cognito_user_group.entitlement[0]",
    "module.x.aws_cognito_user_group.y",
])
def test_forbid_cognito_groups_fails_any_change(tmp_path, actions, address):
    doc = _plan(*SAFE["resource_changes"],
                _rc(address, "aws_cognito_user_group", address.rsplit(".", 1)[-1], actions))
    proc = _run(tmp_path, doc, "--forbid-cognito-groups")
    assert proc.returncode == 1
    assert address in proc.stdout
    flagged = [line for line in proc.stdout.splitlines()
               if (line.startswith("COGNITO GROUP ") or line.startswith("PROTECTED ")) and address in line]
    assert len(flagged) == 1


def test_cognito_group_create_passes_without_flag(tmp_path):
    doc = _plan(_rc("module.x.aws_cognito_user_group.y", "aws_cognito_user_group", "y", ["create"]))
    assert _run(tmp_path, doc).returncode == 0


def test_cognito_group_no_op_passes_with_flag(tmp_path):
    doc = _plan(_rc("module.x.aws_cognito_user_group.y", "aws_cognito_user_group", "y", ["no-op"]))
    proc = _run(tmp_path, doc, "--forbid-cognito-groups")
    assert proc.returncode == 0 and "aws_cognito_user_group" not in proc.stdout


def test_module_nested_kms_key_is_caught_by_type(tmp_path):
    doc = _plan(_rc("module.x.aws_kms_key.y", "aws_kms_key", "y", ["delete", "create"]))
    proc = _run(tmp_path, doc)
    assert proc.returncode == 1 and "PROTECTED replace: module.x.aws_kms_key.y" in proc.stdout


@pytest.mark.parametrize("raw", [
    "not json at all",
    "[]",
    json.dumps({"resource_changes": []}),
    json.dumps({"format_version": "1.0", "values": {"root_module": {}}}),
    json.dumps({"format_version": "1.2", "planned_values": {}, "resource_changes": {"a": 1}}),
    json.dumps({"format_version": "1.2", "planned_values": {}, "resource_changes": ["x"]}),
])
def test_non_plan_input_exits_2(tmp_path, raw):
    proc = _run(tmp_path, None, raw=raw)
    assert proc.returncode == 2
    assert proc.stderr.startswith("plan_review: ")


def test_missing_file_exits_2(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "absent.json")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2


def test_empty_plan_passes(tmp_path):
    proc = _run(tmp_path, {"format_version": "1.2", "planned_values": {}})
    assert proc.returncode == 0 and "Totals: 0 create" in proc.stdout


def test_action_mapping():
    assert plan_review.action_of(["no-op"]) is None
    assert plan_review.action_of(["create", "delete"]) == "replace"
    assert plan_review.action_of(["delete", "create"]) == "replace"
    assert plan_review.action_of(["read"]) == "read"


def test_script_is_stdlib_only():
    text = SCRIPT.read_text()
    imports = {line.split()[1].split(".")[0] for line in text.splitlines()
               if line.startswith(("import ", "from ")) and not line.startswith("from __future__")}
    assert imports <= {"argparse", "json", "sys", "typing"}
