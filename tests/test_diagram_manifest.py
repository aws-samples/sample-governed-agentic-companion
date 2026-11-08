"""The pinned manifest text and its bounds (design D11; acceptance 32, 33)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.diagram import manifest as M  # noqa: E402
from agents.diagram.facts import Flag, validate_fact  # noqa: E402
from agents.diagram.style import CAPTION  # noqa: E402
from agents.governance_gate import get_gate  # noqa: E402

KEY = "tier_b/platform/diagram_facts"


def _mi(**kw):
    base = dict(title="Private request path", topic_id="private-request-path", score=92.5,
                passed=True, threshold=85, rendered=18, cited=18, flagged_total=0,
                label_lines=['- node "AgentCore Gateway"'], unlabelled_edges=3, flagged_lines=[],
                finding_lines=[], report_file="private-request-path.report.json",
                artefacts=[("private-request-path.drawio", 74000, "1a2b3c4d5e6f")])  # pragma: allowlist secret
    base.update(kw)
    return M.ManifestInput(**base)


def test_pinned_layout():
    text, ok = M.build_text(_mi())
    lines = text.split("\n")
    assert ok and lines[0] == '[diagram] "Private request path" (topic private-request-path)'
    assert lines[1] == CAPTION
    assert lines[2].startswith("Quality 92.5 of 100, pass (threshold 85). Traceability 18 of 18")
    assert "- 3 unlabelled edges" in lines
    assert M.DELIVER_HEAD in lines and "- private-request-path.drawio (74000 B, sha256 1a2b3c4d5e6f)" in lines


def test_no_deliver_wording_and_omitted():
    text, _ = M.build_text(_mi(deliver=False, omitted=[("x.png", "png_over_limit")]))
    assert M.NO_DELIVER_HEAD in text and M.NO_DELIVER_HINT in text
    assert "Not delivered: x.png (png_over_limit)" in text


def test_caps_and_more_lines():
    fl = [Flag("dropped", "label-policy", f"{KEY}#f{i}").line() for i in range(37)]
    ql = [M.finding_line("warn", "DQ-L126", f"e{i}", "short arrow") for i in range(14)]
    text, ok = M.build_text(_mi(flagged_lines=fl, finding_lines=ql, flagged_total=37))
    assert ok
    assert "Flagged (10 of 37 shown; full list in private-request-path.report.json):" in text
    assert "- ... and 27 more (run ./run.sh diagram --check-facts)" in text
    assert "Quality findings (10 of 14 shown; full list in the report):" in text
    assert "- ... and 4 more" in text


def test_bounds_at_every_cap():
    labels = ([f'- node "{"N" * 47}{i % 10}"' for i in range(24)]
              + [f'- group "{"G" * 47}{i % 10}"' for i in range(12)]
              + [f'- edge "{"E" * 47}{i % 10}" (red dashed)' for i in range(40)])
    assert len("\n".join(labels).encode()) <= 5500
    fl = [Flag("dropped", "gate-detector:T6_SECRET_LEAK", "tier_b/" + "k" * 90 + "#" + "i" * 48).line()
          for _ in range(500)]
    ql = [M.finding_line("fail", "DQ-LG101", "e40", "line style missing from legend")] * 200
    text, ok = M.build_text(_mi(label_lines=labels, flagged_lines=fl, finding_lines=ql,
                                flagged_total=500, generic_icons=[("A", "DQ-I101")] * 9,
                                notes=[M.LOCAL_PREVIEW, M.TEST_FIXTURE], draft=True,
                                hard_lines=["- FAIL DQ-L101 at n1 (nodes overlap)"] * 12))
    assert ok and len(text.encode()) <= 10240
    for lab in labels:
        assert lab in text
    assert "private-request-path.drawio" in text


def test_truncation_tail_only(monkeypatch):
    monkeypatch.setattr(M, "ENGINE_TEXT_CAP", 1200)
    ql = [M.finding_line("warn", "DQ-X101", f"e{i}", "edges cross") for i in range(10)]
    fl = [Flag("dropped", "label-policy", f"{KEY}#f{i}").line() for i in range(10)]
    text, ok = M.build_text(_mi(flagged_lines=fl, finding_lines=ql, flagged_total=10))
    assert M.TRUNCATED_LINE in text and '- node "AgentCore Gateway"' in text
    assert "private-request-path.drawio" in text


def test_adversarial_labels():
    gate = get_gate()
    ok_labels = ["Bearer token", "Secret store", "Token"]
    lines = []
    for i, lab in enumerate(ok_labels + ["token: x", "pass" + "word=hunter2"]):  # pragma: allowlist secret
        fact, flag = validate_fact({"kind": "node", "id": f"n{i}", "label": lab, "topics": ["t"]},
                                   specialist="platform", kb_key=KEY)
        if lab in ok_labels:
            assert fact is not None
            lines.append(M.line_for(fact))
        else:
            assert fact is None and flag is not None
    text, ok = M.build_text(_mi(label_lines=lines))
    assert ok
    assert not [f for f in gate.content_findings(text) + gate.content_findings_t11(text)
                if f.severity.value == "block"]


def test_fail_closed(monkeypatch):
    monkeypatch.setattr(M, "CAPTION", "I have deployed the stack.")
    text, ok = M.build_text(_mi())
    assert not ok and text == M.FAIL_CLOSED
