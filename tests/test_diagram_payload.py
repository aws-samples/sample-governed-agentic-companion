"""Payload caps, wire formula and degrade path (design D12; acceptance 12)."""

import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.diagram import bundle as B  # noqa: E402
from agents.diagram.limits import Limits, resolve_limits  # noqa: E402
from agents.diagram.report import build_report  # noqa: E402

FILES = {"drawio": b"<mxfile/>", "svg": b"<svg/>", "png": b"\x89PNG" + b"\0" * 100,
         "report.json": b'{"schema":"gac.diagram-report/1"}'}


def _b(files=FILES, limits=Limits(), **kw):
    return B.build_bundle(name="t", draft=False, quality={"score": 90, "pass": True, "threshold": 85,
                                                          "findings": []},
                          traceability={"rendered": 2, "cited": 2, "flagged": 0}, sources=["s"],
                          files=files, limits=limits, text_estimate="x", **kw)


def test_normal_bundle_shape():
    r = _b()
    arts = r.bundle["artifacts"]
    assert [a["file"] for a in arts] == ["t.drawio", "t.svg", "t.png", "t.report.json"]
    png = next(a for a in arts if a["file"] == "t.png")
    data = base64.b64decode(png["content"])
    assert png["bytes"] == len(data) and png["sha256"] == hashlib.sha256(data).hexdigest()
    assert r.bundle["schema"] == "gac.diagram/1" and r.bundle["limits"]["wire_bytes"] > 65536


def test_wire_formula():
    b = {"k": 'a"b\\c'}
    inner = json.dumps({"response": "t", "diagram": b}, separators=(",", ":"))
    assert B.wire_bytes("t", b) == len(json.dumps(inner)) + 65536
    assert B.WIRE_COPIES == 1


def test_degrade_png_1x_then_omit_then_none():
    small = Limits(max_payload_bytes=65536 + 2000 + 4096 + 400,
                   max_artifact_bytes={"drawio": 2 ** 21, "svg": 2 ** 21, "png": 2 ** 20, "report": 2 ** 18})
    big_png = b"\x89PNG" + b"\1" * 3000
    calls = []
    r = _b(files={**FILES, "png": big_png}, limits=small,
           rerender_png_1x=lambda: calls.append(1) or b"\x89PNG" + b"\0" * 10)
    assert calls and "t.png" in [a[0] for a in r.artefacts]
    r = _b(files={**FILES, "png": big_png}, limits=small, rerender_png_1x=lambda: big_png)
    assert ("t.png", "png_over_limit") in r.omitted and r.bundle is not None
    tiny = Limits(max_payload_bytes=65536 + 10)
    r = _b(limits=tiny)
    assert r.bundle is None and r.too_large and len(r.omitted) == 4


def test_per_artefact_cap():
    lim = Limits(max_artifact_bytes={"drawio": 4, "svg": 2 ** 21, "png": 2 ** 20, "report": 2 ** 18})
    r = _b(limits=lim)
    assert ("t.drawio", "drawio_over_limit") in r.omitted


def test_findings_cap_30():
    r = B.build_bundle(name="t", draft=True, quality={"findings": [{"code": "x"}] * 40},
                       traceability={}, sources=[], files=FILES, limits=Limits(), text_estimate="")
    assert len(r.bundle["quality"]["findings"]) == 30 and r.bundle["quality"]["truncated"]
    assert r.bundle["artifacts"][0]["file"] == "t-draft.drawio"


def test_compact_manifest_small():
    r = _b()
    m = B.compact_manifest(r.bundle, name="t", draft=False, score=90, artefacts=r.artefacts,
                           omitted=r.omitted)
    assert len(json.dumps(m)) <= 1024 and "content" not in json.dumps(m)


def test_report_schema_and_cap():
    flagged = [{"action": "dropped", "kb_ref": "tier_b/x/diagram_facts#" + "a" * 48, "reason": "label-policy"}] * 500
    findings = [{"code": "DQ-X101", "severity": "warn", "element": "e1", "message": "edges cross"}] * 200
    text = build_report(topic_id="t", title="T", score=1, passed=False, threshold=85,
                        findings=findings, rendered=2, cited=2, flagged=flagged,
                        generic_icons=[], sources=["s"])
    doc = json.loads(text)
    assert doc["schema"] == "gac.diagram-report/1" and len(doc["flagged"]) == 500
    assert len(text.encode()) <= 256 * 1024


def test_limits_tighten_only():
    lim = resolve_limits({"diagram": {"max_nodes": 99, "png_scale": 1, "quality_pass_score": 70,
                                      "max_payload_bytes": 10 ** 9, "max_artifact_bytes": {"png": 10}}})
    assert lim.max_nodes == 24 and lim.png_scale == 1 and lim.quality_pass_score == 85
    assert lim.max_payload_bytes == 4 * 1024 * 1024 and lim.max_artifact_bytes["png"] == 10
    assert resolve_limits({"diagram": {"enabled": False}}).enabled is False


def test_env_disable_only(monkeypatch):
    monkeypatch.setenv("GAC_DIAGRAM_ENABLED", "false")
    assert resolve_limits({"diagram": {"enabled": True}}).enabled is False
    monkeypatch.setenv("GAC_DIAGRAM_ENABLED", "true")
    assert resolve_limits({"diagram": {"enabled": False}}).enabled is False


# -- several views, one wire budget (design D7.3) ---------------------------------------------

def _views(n, png=b"\x89PNG" + b"\1" * 3000, rerender=None):
    return [B.ViewFiles(name=f"v{i}", draft=False,
                        quality={"score": 90, "pass": True, "threshold": 85, "findings": []},
                        traceability={"rendered": 2, "cited": 2, "flagged": 0}, sources=["s"],
                        files={**FILES, "png": png}, rerender_png_1x=rerender)
            for i in range(1, n + 1)]


def test_single_view_is_build_bundle():
    v = _views(1)
    one = B.build_bundles(v, limits=Limits(), text_estimate="x")
    ref = B.build_bundle(name="v1", draft=False, quality=v[0].quality,
                         traceability=v[0].traceability, sources=["s"], files=v[0].files,
                         limits=Limits(), text_estimate="x")
    assert one.bundles == [ref.bundle] and one.artefacts == [ref.artefacts]


def test_multi_view_fits_and_shares_the_wire_figure():
    r = B.build_bundles(_views(3), limits=Limits(), text_estimate="x")
    assert [b["name"] for b in r.bundles] == ["v1", "v2", "v3"] and not r.dropped
    assert len({b["limits"]["wire_bytes"] for b in r.bundles}) == 1


def _budget(views_fit, png_bytes):
    """A budget that fits `views_fit` small views (no PNG) but not their PNGs."""
    small = B.build_bundles(_views(views_fit, png=b""), limits=Limits(), text_estimate="x")
    base = B.wire_bytes_multi("x" + " " * B.FOOTER_ALLOWANCE,
                              [{**b, "artifacts": [a for a in b["artifacts"]
                                                   if not a["file"].endswith(".png")]}
                               for b in small.bundles])
    return Limits(max_payload_bytes=base + png_bytes)


def test_degrade_png_1x_in_every_view():
    calls = []
    lim = _budget(3, 3 * 700)
    r = B.build_bundles(_views(3, rerender=lambda: calls.append(1) or b"\x89PNG" + b"\0" * 20),
                        limits=lim, text_estimate="x")
    assert len(calls) == 3 and not r.dropped
    assert all(any(a["file"].endswith(".png") for a in b["artifacts"]) for b in r.bundles)


def test_degrade_drops_pngs_then_trailing_views_never_view_1():
    lim = _budget(3, 400)
    r = B.build_bundles(_views(3), limits=lim, text_estimate="x")
    assert not r.dropped and all(("v%d.png" % i, "png_over_limit") in r.omitted[i - 1]
                                 for i in (1, 2, 3))
    lim = _budget(2, 400)
    r = B.build_bundles(_views(3), limits=lim, text_estimate="x")
    assert r.dropped == ["v3"] and [b["name"] for b in r.bundles] == ["v1", "v2"]
    r = B.build_bundles(_views(3), limits=Limits(max_payload_bytes=65536 + 10),
                        text_estimate="x")
    assert r.too_large and r.bundles == [] and r.dropped == ["v3", "v2"]


def test_no_wire_budget_for_the_cli():
    r = B.build_bundles(_views(3), limits=Limits(max_payload_bytes=65536 + 10),
                        text_estimate="x", wire_budget=False)
    assert len(r.bundles) == 3 and not r.too_large
