"""Release consistency: the CHANGELOG and the shipped config agree on the kit version."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.M)


def _headings():
    return HEADING.findall((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))


def test_latest_changelog_heading_matches_config_version():
    headings = _headings()
    assert headings, "CHANGELOG.md has no '## [X.Y.Z]' heading"
    cfg = yaml.safe_load((ROOT / "config.yaml.example").read_text(encoding="utf-8"))
    assert str(cfg["system"]["version"]) == headings[0]


def test_readme_badge_matches_config_version():
    badge = re.search(r"badge/version-(\d+\.\d+\.\d+)-",
                      (ROOT / "README.md").read_text(encoding="utf-8"))
    assert badge, "README.md has no version badge"
    cfg = yaml.safe_load((ROOT / "config.yaml.example").read_text(encoding="utf-8"))
    assert badge.group(1) == str(cfg["system"]["version"])


def test_changelog_headings_are_unique():
    headings = _headings()
    assert len(headings) == len(set(headings)), "duplicate CHANGELOG version heading"


def test_no_private_mode_reachability_overclaim():
    # In private mode the hostnames still resolve publicly and the resource policies return
    # HTTP 403; nothing ships that says the endpoints are "reachable only" from approved networks.
    stale = re.compile(r"reachable only from your approved", re.I)
    shipped = [p for p in ROOT.rglob("*") if p.is_file()
               and p.suffix in {".md", ".sh", ".ps1", ".json", ".py", ".tf", ".yaml", ".example"}
               and not any(part in {".venv", ".git", ".terraform", "node_modules", "__pycache__"}
                           for part in p.parts)
               and p.name != "test_release_consistency.py"]
    hits = [str(p.relative_to(ROOT)) for p in shipped
            if stale.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not hits, f"private-mode reachability overclaim in: {hits}"
