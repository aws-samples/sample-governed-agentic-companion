"""The shared local writer (design D13.4; acceptance 14)."""

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "frontdoor"))

import diagram_writer as W  # noqa: E402


def art(file, ext, data):
    media, enc = W.MEDIA[ext]
    content = base64.b64encode(data).decode() if enc == "base64" else data.decode()
    return {"file": file, "media_type": media, "encoding": enc, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "content": content}


def payload(name="fig", draft=False):
    base = f"{name}-draft" if draft else name
    rep = json.dumps({"schema": "gac.diagram-report/1"}).encode()
    return {"schema": "gac.diagram/1", "name": name, "draft": draft, "artifacts": [
        art(f"{base}.drawio", "drawio", b"<mxfile/>"), art(f"{base}.svg", "svg", b"<svg/>"),
        art(f"{base}.png", "png", b"\x89PNG\r\n\x1a\n" + b"\0" * 20),
        art(f"{base}.report.json", "report.json", rep)]}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    monkeypatch.delenv("GAC_DIAGRAM_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("GAC_DIAGRAM_LOCAL_DIR", raising=False)
    monkeypatch.delenv("GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE", raising=False)
    return root


def test_writes_all_four_and_gitignore(ws):
    r = W.write_artifacts(payload(), cwd=str(ws))
    assert len(r["written"]) == 4 and not r["reason"]
    d = ws / "outputs" / "diagrams" / "fig"
    assert (d / "fig.png").read_bytes().startswith(b"\x89PNG")
    assert (ws / "outputs" / "diagrams" / ".gitignore").read_text() == "*\n"
    assert all(Path(p).is_absolute() for p in r["written"])
    assert "Saved locally:" in W.front_door_lines(r)


def test_draft_names(ws):
    r = W.write_artifacts(payload(draft=True), cwd=str(ws))
    assert any(p.endswith("fig-draft.drawio") for p in r["written"])


@pytest.mark.parametrize("mutate,msg", [
    (lambda p: p.update(name="../x"), "valid file name"),
    (lambda p: p.update(name="a/b"), "valid file name"),
    (lambda p: p["artifacts"][0].update(file="other.drawio"), "does not match"),
    (lambda p: p["artifacts"][1].update(sha256="0" * 64), "sha256"),
    (lambda p: p["artifacts"][1].update(bytes=999), "size"),
    (lambda p: p["artifacts"][2].update(content="!!notbase64"), "base64"),
    (lambda p: p["artifacts"][2].update(media_type="text/plain"), "media type"),
    (lambda p: p.update(schema="other"), "gac.diagram/1"),
])
def test_validation_refusals_write_nothing(ws, mutate, msg):
    p = payload()
    mutate(p)
    r = W.write_artifacts(p, cwd=str(ws))
    assert r["written"] == [] and msg in r["reason"]
    assert not (ws / "outputs").exists()


def test_existing_without_overwrite_writes_nothing(ws):
    W.write_artifacts(payload(), cwd=str(ws))
    target = ws / "outputs" / "diagrams" / "fig" / "fig.svg"
    target.write_bytes(b"mine")
    (ws / "outputs" / "diagrams" / "fig" / "fig.png").unlink()
    r = W.write_artifacts(payload(), cwd=str(ws))
    assert r["written"] == [] and "already exists" in r["reason"]
    assert target.read_bytes() == b"mine"
    assert not (ws / "outputs" / "diagrams" / "fig" / "fig.png").exists()
    r = W.write_artifacts(payload(), cwd=str(ws), overwrite=True)
    assert len(r["written"]) == 4 and target.read_bytes() == b"<svg/>"


def test_symlink_refused(ws, tmp_path):
    (ws / "outputs").mkdir()
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", ws / "outputs" / "diagrams")
    r = W.write_artifacts(payload(), cwd=str(ws))
    assert r["written"] == [] and ("symlink" in r["reason"] or "outside" in r["reason"])


def test_outside_workspace_needs_env(ws, tmp_path, monkeypatch):
    out = tmp_path / "out"
    r = W.write_artifacts(payload(), cwd=str(ws), out_dir=str(out))
    assert r["written"] == [] and "outside the workspace" in r["reason"]
    monkeypatch.setenv("GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE", "true")
    r = W.write_artifacts(payload(), cwd=str(ws), out_dir=str(out))
    assert len(r["written"]) == 4


def test_traversal_out_dir_refused(ws):
    r = W.write_artifacts(payload(), cwd=str(ws), out_dir="../../etc")
    assert r["written"] == [] and "outside" in r["reason"]


def test_root_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("GAC_DIAGRAM_WORKSPACE_ROOT", raising=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    r = W.write_artifacts(payload(), cwd=str(plain))
    assert "set GAC_DIAGRAM_WORKSPACE_ROOT" in r["reason"]
    for bad in ("/", str(Path.home())):
        r = W.write_artifacts(payload(), workspace_root=bad)
        assert r["written"] == [] and "filesystem root or your home" in r["reason"]
    r = W.write_artifacts(payload(), workspace_root="relative/path")
    assert "absolute" in r["reason"]
    kiro = tmp_path / "k"
    (kiro / ".kiro").mkdir(parents=True)
    assert len(W.write_artifacts(payload(), cwd=str(kiro))["written"]) == 4
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(plain))
    assert len(W.write_artifacts(payload())["written"]) == 4


def test_race_claim_rolls_back(ws, monkeypatch):
    real_open = os.open
    calls = {"n": 0}

    def flaky(path, flags, *a):
        if flags & os.O_EXCL and not os.path.basename(str(path)).startswith(".gac-"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise FileExistsError(path)
        return real_open(path, flags, *a)
    monkeypatch.setattr(os, "open", flaky)
    r = W.write_artifacts(payload(), cwd=str(ws))
    assert r["written"] == [] and "appeared" in r["reason"]
    d = ws / "outputs" / "diagrams" / "fig"
    assert [p.name for p in d.iterdir()] == []
