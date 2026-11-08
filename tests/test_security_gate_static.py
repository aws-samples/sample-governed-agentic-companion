"""Static checks for the standard security gate (scripts/security_gate.sh)."""
import datetime
import re
import shutil
import subprocess  # nosec B404: runs `bash -n` on a tracked script, no user input
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "security_gate.sh"
SCAN_RESULTS = ROOT / "docs" / "security" / "scan-results.md"
ALLOWLIST = ROOT / "security" / "accepted-residuals.yaml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _pin(name):
    m = re.search(rf"^{name}=(\S+)$", GATE.read_text(), re.M)
    assert m, name
    return m.group(1)


def test_gate_parses_as_bash():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    subprocess.run([bash, "-n", str(GATE)], check=True)  # nosec B603: fixed argv


def test_gate_pins_match_scan_results_and_ci():
    doc, ci = SCAN_RESULTS.read_text(), CI.read_text()
    for image in ("KICS_IMAGE", "TRIVY_IMAGE", "GRYPE_IMAGE"):
        ref = _pin(image)
        assert re.fullmatch(r"[a-z/]+@sha256:[0-9a-f]{64}", ref), ref
        assert ref.split("@", 1)[1] in doc, image
    assert _pin("KICS_IMAGE") in ci
    for tool in ("CHECKOV_VERSION", "BANDIT_VERSION", "PIP_AUDIT_VERSION", "DETECT_SECRETS_VERSION"):
        assert f"| {_pin(tool)} |" in doc, tool
    assert "./scripts/security_gate.sh --static" in ci


def test_gate_is_read_only_and_never_uses_the_kit_venv():
    text = GATE.read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for banned in ("docker push", "docker login", "buildx", "--push", "terraform apply",
                   "terraform plan", "terraform init", ".venv/bin", "aws s3", "aws ecr",
                   "--ignore-unfixed", "--severity HIGH", "--exit-code 0"):
        assert banned not in code, banned
    assert "--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL" in code
    assert "refusing to use the kit's .venv" in code


def test_resvg_sbom_scans_the_version_the_images_install():
    pins = {face: re.findall(r"^resvg-py==(\S+)", (ROOT / "agentcore" / "app" / face /
                             "requirements.txt").read_text(), re.M)
            for face in ("orchestrator", "mcp_runtime")}
    assert pins["orchestrator"] and pins["orchestrator"] == pins["mcp_runtime"], pins
    # The gate derives the version from those files; no separate hard-coded pin can drift.
    assert not re.search(r"^RESVG_PY_VERSION=[0-9]", GATE.read_text(), re.M)
    assert 'RESVG_PY_VERSION="$(resvg_pin orchestrator)"' in GATE.read_text()


def test_accepted_residuals_are_complete_scoped_and_listed():
    yaml = pytest.importorskip("yaml")
    entries = yaml.safe_load(ALLOWLIST.read_text())["residuals"]
    doc = SCAN_RESULTS.read_text()
    assert entries
    for e in entries:
        for key in ("id", "package", "version", "status", "reason", "evidence", "approved_by",
                    "approved_on", "review_by"):
            assert e.get(key), (e.get("id"), key)
        assert e["status"] in ("accepted-residual", "false-positive")
        assert isinstance(e["review_by"], datetime.date)
        assert e["id"] in doc and str(e["review_by"]) in doc
