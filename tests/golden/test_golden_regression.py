"""Golden regression set over the shipped knowledge base.

A REGRESSION CHECK that routing, sources, and declines do not change unexpectedly. It is NOT a
measure of answer accuracy. The question set lives in golden_questions.yaml next to this file.

Per entry it asserts:
  1. the Orchestrator routes the question to `expected_route`;
  2. the routed specialist's knowledge lookup matches exactly `expected_sources`;
  3. the gated response has the expected outcome: `grounded` cites its first source and is not a
     decline; `declined` carries the honest decline; `guidance` is the external-tool plan;
  4. every response passes the gate (no block notice) and carries the governance footer, and no
     response claims an executed deployment.

No AWS, no LLM. Audit and feedback stores are redirected to tmp_path, so the run writes no repo
state.
"""
import re
from pathlib import Path

import pytest
import yaml

_SET = yaml.safe_load((Path(__file__).parent / "golden_questions.yaml").read_text(
    encoding="utf-8"))["questions"]

_DECLINE_MARKER = "can't ground an answer"
_GUIDANCE_MARKER = "GUIDANCE ONLY"
_DEPLOY_CLAIM = re.compile(
    r"\bI (?:have )?(?:deployed|applied|promoted|restarted)\b|\bhas been deployed\b")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAC_AUDIT_PATH", str(tmp_path / "trail.jsonl"))
    monkeypatch.setenv("GAC_FEEDBACK_PATH", str(tmp_path / "candidates.jsonl"))
    import agents.audit as audit
    import agents.feedback as feedback
    from agents.governance_gate import reset_gate
    audit.reset_audit()
    feedback.reset_feedback()
    reset_gate()
    yield
    audit.reset_audit()
    feedback.reset_feedback()
    reset_gate()


@pytest.fixture(scope="module")
def orchestrator():
    from agents.orchestrator import Orchestrator
    return Orchestrator()


def test_golden_set_shape():
    ids = [q["id"] for q in _SET]
    assert len(ids) == len(set(ids)), "golden ids must be unique"
    assert 10 <= len(_SET) <= 15
    cats = {q["category"] for q in _SET}
    assert {"grounded", "out_of_scope"} <= cats


@pytest.mark.parametrize("case", _SET, ids=[q["id"] for q in _SET])
def test_golden_question(case, orchestrator):
    q = case["question"]

    route = orchestrator.route(q)
    assert route == case["expected_route"]

    agent = orchestrator._specialists[route][0]
    _score, sources = agent.assess_confidence_detail(q, q)
    assert set(sources) == set(case["expected_sources"])

    out = orchestrator.handle(q)
    assert "Response blocked" not in out
    assert "Governance Outcome" in out
    assert not _DEPLOY_CLAIM.search(out)

    outcome = case["expected_outcome"]
    if outcome == "grounded":
        assert _DECLINE_MARKER not in out
        assert f"(Source: {sorted(case['expected_sources'])[0]})" in out
    elif outcome == "declined":
        assert _DECLINE_MARKER in out
        assert "Sources: none matched" in out
    elif outcome == "guidance":
        assert _GUIDANCE_MARKER in out
    else:  # pragma: no cover - guards a typo in the YAML
        pytest.fail(f"unknown expected_outcome {outcome!r}")
