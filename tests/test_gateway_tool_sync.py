"""Gateway tool re-sync (D9, AC 15 static half, AC 16).

agentcore/terraform/scripts/sync_gateway_target.sh runs against a stub `aws` on PATH: no AWS
call is made. The stub logs every invocation and answers get-gateway-target from a scripted
status sequence. The Terraform half (instance counts and trigger values) is in
agentcore/terraform/tests/matrix.tftest.hcl (runs s1..s4); the shape is asserted here with
python-hcl2.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "agentcore" / "terraform"
SCRIPT = TF_DIR / "scripts" / "sync_gateway_target.sh"

_STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in
  "bedrock-agentcore-control synchronize-gateway-targets")
    case "$STUB_DETECT" in
      invalid) echo "aws: error: argument operation: Found invalid choice 'synchronize-gateway-targets'" >&2; exit 252 ;;
      supported) echo "aws: error: the following arguments are required: --gateway-identifier, --target-id-list" >&2; exit 252 ;;
      *) echo "Unexpected output" >&2; exit 1 ;;
    esac ;;
  *synchronize-gateway-targets*)
    echo '{"targets": [{"status": "SYNCHRONIZING"}]}'; exit 0 ;;
  *"--query statusReasons"*)
    echo "Tool fetch failed"; exit 0 ;;
  *get-gateway-target*)
    first=$(head -n 1 "$STUB_STATUSES")
    if [ "$(wc -l < "$STUB_STATUSES" | tr -d ' ')" -gt 1 ]; then
      tail -n +2 "$STUB_STATUSES" > "$STUB_STATUSES.next" && mv "$STUB_STATUSES.next" "$STUB_STATUSES"
    fi
    echo "$first"; exit 0 ;;
esac
echo "stub: unexpected call $*" >&2
exit 99
"""

GOOD = {
    "AWS_REGION": "ap-southeast-2",
    "GATEWAY_ID": "gactest-gateway-abc123",
    "TARGET_ID": "ABCDEFGH12",
    "SETTLE_SECONDS": "0",
    "POLL_SECONDS": "0",
}


@pytest.fixture
def stub(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(_STUB)
    aws.chmod(0o755)
    log = tmp_path / "aws.log"
    log.write_text("")
    statuses = tmp_path / "statuses"

    def run(statuses_seq=("READY",), detect="supported", **overrides):
        statuses.write_text("\n".join(statuses_seq) + "\n")
        env = {
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
            "STUB_LOG": str(log),
            "STUB_STATUSES": str(statuses),
            "STUB_DETECT": detect,
        }
        env.update(GOOD)
        env.update(overrides)
        env = {k: v for k, v in env.items() if v is not None}
        proc = subprocess.run(["/bin/sh", str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=60)
        calls = [line for line in log.read_text().splitlines() if line]
        return proc, calls

    return run


def _syncs(calls):
    return [c for c in calls if "synchronize-gateway-targets --" in c]


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize("name,value", [
    ("AWS_REGION", "AP-SOUTHEAST-2"),
    ("AWS_REGION", "ap-southeast-2\nus-east-1"),
    ("AWS_REGION", ""),
    ("GATEWAY_ID", "Gateway_Upper"),
    ("GATEWAY_ID", "gw; rm -rf /"),
    ("GATEWAY_ID", "gateway\nother"),
    ("TARGET_ID", "abcdefgh12"),
    ("TARGET_ID", "ABCDEFGH1"),
    ("SETTLE_SECONDS", "121"),
    ("SETTLE_SECONDS", "abc"),
    ("SETTLE_SECONDS", "-1"),
    ("POLL_SECONDS", "11"),
])
def test_bad_input_exits_2_before_any_aws_call(stub, name, value):
    proc, calls = stub(**{name: value})
    assert proc.returncode == 2, proc.stderr
    assert f"invalid {name}" in proc.stderr
    assert calls == []


def test_missing_input_exits_2(stub):
    proc, calls = stub(GATEWAY_ID=None)
    assert proc.returncode == 2 and calls == []


def test_old_cli_invalid_choice_exits_3(stub):
    proc, calls = stub(detect="invalid")
    assert proc.returncode == 3
    assert "AWS CLI too old" in proc.stderr and "sync_gateway_tools = false" in proc.stderr
    assert "unrecognized" not in proc.stderr
    assert _syncs(calls) == []


def test_unrecognized_cli_output_exits_3(stub):
    proc, calls = stub(detect="other")
    assert proc.returncode == 3
    assert "AWS CLI too old" in proc.stderr and "unrecognized AWS CLI output" in proc.stderr
    assert _syncs(calls) == []


def test_ready_exits_0_with_exactly_one_sync(stub):
    proc, calls = stub(statuses_seq=("READY", "SYNCHRONIZING", "READY"))
    assert proc.returncode == 0, proc.stderr
    syncs = _syncs(calls)
    assert syncs == ["bedrock-agentcore-control synchronize-gateway-targets --region ap-southeast-2 "
                     "--gateway-identifier gactest-gateway-abc123 --target-id-list ABCDEFGH12"]
    assert "READY" in proc.stdout


def test_pending_auth_is_waited_out_before_the_sync(stub):
    seq = ("CREATE_PENDING_AUTH", "UPDATE_PENDING_AUTH", "SYNCHRONIZE_PENDING_AUTH", "UPDATING",
           "READY", "SYNCHRONIZING", "READY")
    proc, calls = stub(statuses_seq=seq)
    assert proc.returncode == 0, proc.stderr
    gets = [i for i, c in enumerate(calls) if "get-gateway-target" in c]
    sync_at = [i for i, c in enumerate(calls) if "synchronize-gateway-targets --" in c]
    assert len(sync_at) == 1
    # Five status reads (four pending, then READY) happen before the one sync.
    assert len([i for i in gets if i < sync_at[0]]) == 5


def test_in_flight_sync_is_waited_out_before_the_sync(stub):
    # A target still SYNCHRONIZING (for example after a manual sync) is not synced again until
    # it settles (review SYNC-1).
    proc, calls = stub(statuses_seq=("SYNCHRONIZING", "SYNCHRONIZING", "READY", "SYNCHRONIZING",
                                     "READY"))
    assert proc.returncode == 0, proc.stderr
    gets = [i for i, c in enumerate(calls) if "get-gateway-target" in c]
    sync_at = [i for i, c in enumerate(calls) if "synchronize-gateway-targets --" in c]
    assert len(sync_at) == 1
    assert len([i for i in gets if i < sync_at[0]]) == 3


def test_pending_auth_bounded(stub):
    proc, calls = stub(statuses_seq=("CREATE_PENDING_AUTH",))
    assert proc.returncode == 1
    assert "after 300 s" in proc.stderr
    assert _syncs(calls) == []
    assert len([c for c in calls if "get-gateway-target" in c]) == 30


@pytest.mark.parametrize("final", ["FAILED", "SYNCHRONIZE_UNSUCCESSFUL"])
def test_failed_sync_exits_1_with_reasons(stub, final):
    proc, calls = stub(statuses_seq=("READY", "SYNCHRONIZING", final))
    assert proc.returncode == 1
    assert final in proc.stderr and "Tool fetch failed" in proc.stderr
    assert len(_syncs(calls)) == 1


def test_still_synchronizing_after_bound_exits_1(stub):
    proc, calls = stub(statuses_seq=("READY", "SYNCHRONIZING"))
    assert proc.returncode == 1
    assert "still synchronizing after 300 s" in proc.stderr
    assert len(_syncs(calls)) == 1


def test_script_is_posix_sh_and_executable():
    text = SCRIPT.read_text()
    assert text.startswith("#!/bin/sh\n") and "set -eu" in text
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    # Detection never trips set -e: the no-argument call is captured with || true.
    assert "out=$(aws bedrock-agentcore-control synchronize-gateway-targets 2>&1) || true" in text


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


def _resource(fname, rtype, rname):
    with open(TF_DIR / fname, encoding="utf-8") as fh:
        doc = hcl2.load(fh)
    for entry in doc.get("resource", []):
        for t, named in entry.items():
            if _unq(t) != rtype:
                continue
            for n, body in named.items():
                if _unq(n) == rname:
                    return body
    raise AssertionError(f"{rtype}.{rname} not in {fname}")


def _variables():
    with open(TF_DIR / "variables.tf", encoding="utf-8") as fh:
        doc = hcl2.load(fh)
    return {_unq(k): v for blk in doc["variable"] for k, v in blk.items() if not k.startswith("__")}


def test_gateway_tool_sync_resource_shape():
    body = _resource("gateway.tf", "terraform_data", "gateway_tool_sync")
    assert _unq(body["count"]) == "local.gateway_enabled && var.sync_gateway_tools ? 1 : 0"
    assert [_unq(t) for t in body["triggers_replace"]] == [
        "local.mcp_runtime_image_uri",
        "aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_version",
        "local.mcp_tool_surface_hash",
    ]
    assert {_unq(d) for d in body["depends_on"]} == {
        "aws_bedrockagentcore_gateway_target.mcp_engine",
        "aws_bedrockagentcore_agent_runtime.mcp_runtime",
        "terraform_data.gateway_invoke_propagation",
    }
    prov = body["provisioner"][0]['"local-exec"']
    assert [_unq(i) for i in prov["interpreter"]] == ["/bin/sh"]
    assert _unq(prov["command"]) == "${path.module}/scripts/sync_gateway_target.sh"
    assert set(prov["environment"]) == {"AWS_REGION", "AWS_PAGER", "GATEWAY_ID", "TARGET_ID",
                                        "SETTLE_SECONDS"}
    assert _unq(prov["environment"]["AWS_PAGER"]) == ""


def test_tool_surface_hash_reads_the_mcp_entrypoint():
    raw = (TF_DIR / "gateway.tf").read_text()
    assert ('mcp_tool_surface_hash = filesha1("${local.repo_root}/agentcore/app/mcp_runtime/'
            'runtime_mcp_entrypoint.py")') in raw


def test_sync_variables():
    v = _variables()
    assert v["sync_gateway_tools"]["default"] is True
    assert v["gateway_sync_settle_seconds"]["default"] == 15
    cond = json.dumps(v["gateway_sync_settle_seconds"]["validation"])
    assert ">= 0" in cond and "<= 120" in cond
