"""Icon catalog builder and runtime readiness (design D7, D9, acceptance 31)."""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

pytest.importorskip("defusedxml")

from diagram_fixtures import fixture_assets_dir, fixture_zip_bytes, square_svg  # noqa: E402
from scripts import build_icon_catalog as B  # noqa: E402
from agents.diagram.assets import Assets  # noqa: E402


def _stage(tmp_path, data=None, name="AWS-Architecture-Icons_Download.zip"):
    st = tmp_path / "staging"
    st.mkdir(exist_ok=True)
    (st / name).write_bytes(data if data is not None else fixture_zip_bytes())
    return st


def _main(st, out, *extra):
    return B.main(["--staging", str(st), "--out", str(out), *extra])


def test_no_zip_exit0_absent(tmp_path, capsys):
    st = tmp_path / "staging"
    st.mkdir()
    assert _main(st, tmp_path / "out") == 0
    assert json.loads((tmp_path / "out" / "STATUS.json").read_text())["icons"] == "absent"
    assert "WARNING: AWS Architecture Icons not staged" in capsys.readouterr().out


def test_one_zip_any_name(tmp_path):
    st = _stage(tmp_path, name="whatever-name.zip")
    assert _main(st, tmp_path / "out") == 0
    status = json.loads((tmp_path / "out" / "STATUS.json").read_text())
    cat_raw = (tmp_path / "out" / "catalog.json").read_bytes()
    assert status["icons"] == "present" and status["package_release"] == "07312026"
    assert status["catalog_sha256"] == hashlib.sha256(cat_raw).hexdigest()
    assert status["zip_sha256"] == hashlib.sha256((st / "whatever-name.zip").read_bytes()).hexdigest()
    cat = json.loads(cat_raw)
    for key in ("AmazonBedrockAgentCore", "User", "Client", "AmazonVPCEndpoints", "AWSCloudLogo",
                "VirtualPrivateCloudVPC", "PrivateSubnet", "AmazonSimpleStorageServiceBucket"):
        e = cat[key]
        body = (tmp_path / "out" / e["file"]).read_bytes()
        assert e["bytes"] == len(body) and e["sha256"] == hashlib.sha256(body).hexdigest()
    assert cat["PublicSubnet"]["color"] == "#7AA116"
    assert cat["AWSAccount"]["color"] == "#E7157B"
    assert not [k for k in cat if "MACOSX" in k]


def test_two_zips_fatal(tmp_path, capsys):
    st = _stage(tmp_path)
    (st / "second.zip").write_bytes(b"PK")
    assert _main(st, tmp_path / "out") == 1
    assert "FATAL: more than one .zip" in capsys.readouterr().err


def test_sha_mismatch(tmp_path, capsys):
    st = _stage(tmp_path)
    assert _main(st, tmp_path / "out", "--expected-sha256", "0" * 64) == 1
    err = capsys.readouterr().err
    assert "FATAL: icons zip sha256 mismatch" in err and "0" * 13 not in err
    good = hashlib.sha256((st / "AWS-Architecture-Icons_Download.zip").read_bytes()).hexdigest()
    assert _main(st, tmp_path / "out2", "--expected-sha256", good) == 0


@pytest.mark.parametrize("name", ["../evil.svg", "/abs/evil.svg", "dir\\evil.svg"])
def test_unsafe_member_paths(tmp_path, name, capsys):
    st = _stage(tmp_path, fixture_zip_bytes(extra={name: b"<svg/>"}))
    assert _main(st, tmp_path / "out") == 1
    assert "FATAL: unsafe member path" in capsys.readouterr().err


def test_ratio_and_size_caps(tmp_path, capsys):
    big = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">' + b" " * 200_000 + b"</svg>"
    name = "Architecture-Service-Icons_07312026/Arch_X/48/Arch_Bomb_48.svg"
    st = _stage(tmp_path, fixture_zip_bytes(extra={name: big}))
    assert _main(st, tmp_path / "out") == 1
    assert "compression ratio" in capsys.readouterr().err


def test_member_count_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(B, "MAX_MEMBERS", 50)
    assert _main(_stage(tmp_path), tmp_path / "out") == 1
    assert "members" in capsys.readouterr().err


def test_floor(tmp_path, capsys):
    assert _main(_stage(tmp_path, fixture_zip_bytes(fillers=0)), tmp_path / "out") == 1
    assert "unexpected zip layout" in capsys.readouterr().err


def test_icon_size_cap_skips(tmp_path):
    out = fixture_assets_dir(tmp_path, pad_bytes=20_000)
    status = json.loads((out / "STATUS.json").read_text())
    cat = json.loads((out / "catalog.json").read_text())
    assert status["skipped"]["size"] > 0 and "AmazonCognito" not in cat


def test_not_a_zip(tmp_path, capsys):
    assert _main(_stage(tmp_path, b"not a zip"), tmp_path / "out") == 1


def test_sanitizer():
    evil = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            b'viewBox="0 0 10 10" onload="x()"><script>alert(1)</script>'
            b'<foreignObject><p/></foreignObject><image href="file:///etc/passwd"/>'
            b'<use xlink:href="http://169.254.169.254/x"/><use href="#ok"/>'
            b'<rect id="ok" fill="#FF0000" style="fill:url(http://x);stroke:#000" onclick="y"/>'
            b'<path d="M0 0" fill="url(#g)"/><g clip-path="url(http://e)"/></svg>')
    text, vb, color = B.sanitize_svg(evil)
    low = text.split(">", 1)[1].lower()
    for bad in ("script", "foreignobject", "<image", "onload", "onclick", "http:", "file:"):
        assert bad not in low
    assert 'href="#ok"' in text and 'fill="url(#g)"' in text and "stroke:#000" in text
    assert vb == "0 0 10 10" and color == "#FF0000"
    with pytest.raises(ValueError):
        B.sanitize_svg(b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>')


@pytest.mark.parametrize("stem,key", [
    ("Amazon-Cognito", "AmazonCognito"), ("Amazon-VPC_Endpoints", "AmazonVPCEndpoints"),
    ("Amazon-Bedrock-AgentCore", "AmazonBedrockAgentCore"), ("User", "User"),
    ("AWS-Cloud-logo", "AWSCloudLogo"), ("Virtual-private-cloud-VPC", "VirtualPrivateCloudVPC"),
])
def test_key_derivation(stem, key):
    assert B.derive_key(stem) == key


def test_light_general_icons_selected_dark_excluded():
    assert B._select("Resource-Icons_07312026/Res_General-Icons/Res_48_Light/Res_User_48_Light.svg")[1] == "User"
    assert B._select("Resource-Icons_07312026/Res_General-Icons/Res_48_Dark/Res_User_48_Dark.svg") is None
    assert B._select("Architecture-Group-Icons_07312026/AWS-Cloud_32_Dark.svg") is None


def test_deterministic_output(tmp_path):
    a = fixture_assets_dir(tmp_path / "a")
    b = fixture_assets_dir(tmp_path / "b")
    assert (a / "catalog.json").read_bytes() == (b / "catalog.json").read_bytes()


# --- runtime --------------------------------------------------------------------------------

def test_runtime_present_and_icon_checks(tmp_path):
    out = fixture_assets_dir(tmp_path)
    a = Assets(str(out))
    r = a.readiness()
    assert r.icons == "present" and r.source == "test-fixture"
    assert r.icon_manifest_source.startswith("icon-manifest:sha256:")
    ok = a.icon("AmazonBedrockAgentCore")
    assert not ok.generic and ok.warn is None and "<rect" in ok.body
    assert a.icon("NoSuchIcon").warn == "DQ-I101"


def test_tampered_icon_gives_generic_i103(tmp_path):
    out = fixture_assets_dir(tmp_path)
    p = out / "icons" / "AmazonCognito.svg"
    p.write_bytes(p.read_bytes().replace(b"64", b"65", 1))
    ref = Assets(str(out)).icon("AmazonCognito")
    assert ref.generic and ref.warn == "DQ-I103"


def test_tampered_catalog_invalid(tmp_path):
    out = fixture_assets_dir(tmp_path)
    c = out / "catalog.json"
    c.write_bytes(c.read_bytes().replace(b"service", b"servicf", 1))
    a = Assets(str(out))
    assert a.readiness().icons == "invalid" and a.catalog_failed


def test_missing_agentcore_icon_generic_i101(tmp_path):
    out = fixture_assets_dir(tmp_path, omit=["AmazonBedrockAgentCore"])
    assert Assets(str(out)).icon("AmazonBedrockAgentCore").warn == "DQ-I101"


def test_absent_dir(tmp_path):
    assert Assets(str(tmp_path / "none")).readiness().icons == "absent"


def test_image_mode_fonts_missing(tmp_path, monkeypatch):
    out = fixture_assets_dir(tmp_path)
    monkeypatch.setenv("GAC_DIAGRAM_ASSETS_DIR", str(out))
    import agents.diagram.assets as A
    monkeypatch.setattr(A, "fonts_present", lambda: False)
    r = Assets().readiness()
    assert r.mode == "image" and r.rasterizer == "absent"


def test_square_fixture_has_no_aws_artwork():
    assert b"<path" not in square_svg("#123456")
    with zipfile.ZipFile(io.BytesIO(fixture_zip_bytes())) as z:
        assert len(z.namelist()) >= 300
