"""The AWS Architecture Icons never enter git (acceptance 19)."""

import shutil
import subprocess  # nosec B404 - test-only, fixed git argv, no shell
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="git unavailable"
)


def _git(*args):
    return subprocess.run(  # nosec B603 B607 - fixed git argv, no shell
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )


def test_no_icon_zip_or_catalog_tracked():
    files = _git("ls-files").stdout.splitlines()
    assert not [f for f in files if f.lower().endswith(".zip")]
    staged = [f for f in files if f.startswith("agentcore/diagram-icons/")]
    assert staged == ["agentcore/diagram-icons/README.md"]
    assert not [f for f in files if f.startswith(".diagram-assets")]


@pytest.mark.parametrize(
    "path", ["agentcore/diagram-icons/x.zip", "agentcore/diagram-icons/icons/a.svg", ".diagram-assets/a"]
)
def test_staging_paths_are_ignored(path):
    assert _git("check-ignore", "-q", path).returncode == 0


def test_staging_readme_not_ignored():
    assert _git("check-ignore", "-q", "agentcore/diagram-icons/README.md").returncode == 1
