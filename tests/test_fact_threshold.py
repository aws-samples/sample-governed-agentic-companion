"""Tests for the configurable, tighten-only grounding threshold (grounding.fact_threshold).

The 0.95 floor comes from PRINCIPLES.md Tenet 4. Configuration may raise the bar, never lower it.
No AWS, no LLM. Stores are isolated under tmp_path so the suite writes no repo state."""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.config import FACT_THRESHOLD_FLOOR, resolve_fact_threshold  # noqa: E402
from agents.governance_gate import GovernanceGate, reset_gate  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAC_AUDIT_PATH", str(tmp_path / "trail.jsonl"))
    monkeypatch.setenv("GAC_FEEDBACK_PATH", str(tmp_path / "candidates.jsonl"))
    import agents.audit as audit
    import agents.feedback as feedback
    audit.reset_audit()
    feedback.reset_feedback()
    reset_gate()
    yield
    audit.reset_audit()
    feedback.reset_feedback()
    reset_gate()


class TestResolveFactThreshold:
    def test_default_when_key_missing(self):
        assert resolve_fact_threshold({}) == 0.95
        assert resolve_fact_threshold(None) == 0.95
        assert resolve_fact_threshold({"grounding": {}}) == 0.95

    def test_stricter_value_is_used(self):
        assert resolve_fact_threshold({"grounding": {"fact_threshold": 0.97}}) == 0.97
        assert resolve_fact_threshold({"grounding": {"fact_threshold": 1}}) == 1.0

    def test_lower_value_is_clamped_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="agents.config"):
            assert resolve_fact_threshold({"grounding": {"fact_threshold": 0.5}}) == 0.95
        assert "tighten-only" in caplog.text

    @pytest.mark.parametrize("bad", ["abc", True, 1.5, [0.99]])
    def test_invalid_value_falls_back_to_floor(self, bad, caplog):
        with caplog.at_level(logging.WARNING, logger="agents.config"):
            assert resolve_fact_threshold({"grounding": {"fact_threshold": bad}}) == 0.95
        assert caplog.records

    def test_shipped_example_resolves_to_floor(self):
        from agents.config import load_config
        assert resolve_fact_threshold(load_config()) >= FACT_THRESHOLD_FLOOR


class TestGateUsesResolvedThreshold:
    def test_gate_default_is_floor(self):
        assert GovernanceGate().fact_threshold >= 0.95

    def test_gate_cannot_be_loosened(self):
        assert GovernanceGate(fact_threshold=0.5).fact_threshold == 0.95

    def test_stricter_gate_blocks_below_and_passes_above(self):
        gate = GovernanceGate(fact_threshold=0.97)
        low = gate.enforce("an answer", agent="platform", request="q", used_llm=True,
                           confidence=0.96, sources=["tier_a/platform/deployment_policy"])
        assert low.blocked
        assert any(f.code == "T4_UNGROUNDED" for f in low.findings)
        high = gate.enforce("an answer", agent="platform", request="q", used_llm=True,
                            confidence=0.98, sources=["tier_a/platform/deployment_policy"])
        assert not high.blocked

    def test_footer_prints_resolved_threshold(self):
        gate = GovernanceGate(fact_threshold=0.97)
        out = gate.enforce("an answer", agent="platform", request="q", used_llm=True,
                           confidence=0.98, sources=["tier_a/platform/deployment_policy"])
        assert "97% fact bar" in out.response
        assert "grounding ≥ 97%" in out.response

    def test_feedback_receives_resolved_threshold(self):
        from agents.feedback import get_feedback
        captured = {}
        fb = get_feedback()
        original = fb.record_work

        def _spy(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        fb.record_work = _spy
        GovernanceGate(fact_threshold=0.98).enforce(
            "an answer", agent="platform", request="q", used_llm=True,
            confidence=0.99, sources=["tier_a/platform/deployment_policy"])
        assert captured["fact_bar"] == 0.98


class TestBaseAgentUsesResolvedThreshold:
    def test_llm_answer_or_decline_uses_configured_bar(self, monkeypatch):
        import agents.config as config
        from agents.specialists import build_specialists
        agent = build_specialists()["platform"][0]
        monkeypatch.setattr(agent, "assess_confidence", lambda *a, **k: 0.96)
        monkeypatch.setattr(config, "load_config",
                            lambda: {"grounding": {"fact_threshold": 0.97}})
        out = agent.llm_answer_or_decline("q", "dev", llm_answer="LLM text")
        assert out != "LLM text"
        assert "can't ground an answer" in out
        monkeypatch.setattr(config, "load_config",
                            lambda: {"grounding": {"fact_threshold": 0.5}})
        assert agent.llm_answer_or_decline("q", "dev", llm_answer="LLM text") == "LLM text"
