"""Tests for the SME maturity model (agents/maturity.py).

Maturity measures how far a GAC fork has grown from empty seed toward a productionizable expert
(docs/MATURATION-PATH.md). These tests exercise the PURE, deterministic compute_maturity() with
injected fixtures — no audit/feedback files, no AWS, no LLM — plus the human-ratified graduation
rule, which is the Tenet 1/4 invariant that matters most.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.maturity import (  # noqa: E402
    compute_maturity, render_report,
    SEEDED, CULTIVATING, GROUNDED, PRODUCTIONIZED, SUSTAINING,
    DEFAULTS,
)


def _grounded_event(sources=("tier_a/platform/deployment_policy",)):
    return {"event": "gate_decision", "blocked": False, "sources": list(sources)}


def _gap(cid="g1"):
    return {"id": cid, "status": "candidate"}


def _block_event(findings=("[Tenet 1] deployment claimed",)):
    return {"event": "gate_decision", "blocked": True, "findings": list(findings)}


class TestStageInference:
    def test_fresh_fork_is_seeded(self):
        r = compute_maturity(audit_events=[], open_candidates=[], promoted_candidates=[],
                             coverage_keys=1)
        assert r.stage == SEEDED
        assert r.eligible_for is None

    def test_active_loop_below_bar_is_cultivating(self):
        # Some gaps, a little coverage, not yet grounded -> the normal growth stage.
        r = compute_maturity(audit_events=[_grounded_event()],
                             open_candidates=[_gap("a"), _gap("b")],
                             promoted_candidates=[],
                             coverage_keys=5)
        assert r.stage == CULTIVATING

    def test_meets_bar_unratified_is_grounded_eligible(self):
        # High grounding, broad coverage, clean governance, but no human ratification yet.
        audit = [_grounded_event() for _ in range(9)]
        r = compute_maturity(audit_events=audit, open_candidates=[_gap()],
                             promoted_candidates=[_gap("p")], coverage_keys=15)
        assert r.stage == GROUNDED
        assert r.eligible_for == PRODUCTIONIZED

    def test_meets_bar_ratified_is_sustaining(self, monkeypatch):
        monkeypatch.setattr("agents.maturity._is_ratified", lambda: True)
        audit = [_grounded_event() for _ in range(9)]
        r = compute_maturity(audit_events=audit, open_candidates=[],
                             promoted_candidates=[_gap("p")], coverage_keys=15)
        assert r.stage == SUSTAINING

    def test_ratified_but_decayed_demotes_to_cultivating(self, monkeypatch):
        # Human-ratified once, but metrics have since fallen below the bar -> surfaced demotion.
        monkeypatch.setattr("agents.maturity._is_ratified", lambda: True)
        r = compute_maturity(audit_events=[_grounded_event()],
                             open_candidates=[_gap("x"), _gap("y"), _gap("z")],
                             promoted_candidates=[], coverage_keys=15)
        assert r.stage == CULTIVATING
        assert any("DECAY" in n for n in r.notes)


class TestGovernanceAndGraduation:
    def test_governance_block_marks_not_clean(self):
        r = compute_maturity(audit_events=[_grounded_event(), _block_event()],
                             open_candidates=[], promoted_candidates=[], coverage_keys=15)
        assert r.governance_clean is False

    def test_a_block_prevents_grounded_eligibility(self):
        # Even with strong grounding + coverage, an absolute block keeps it out of `grounded`.
        audit = [_grounded_event() for _ in range(9)] + [_block_event()]
        r = compute_maturity(audit_events=audit, open_candidates=[],
                             promoted_candidates=[], coverage_keys=15)
        assert r.stage != GROUNDED
        assert r.eligible_for is None

    def test_computation_never_self_promotes_to_productionized(self, monkeypatch):
        # The machine may say GROUNDED/eligible, but NEVER PRODUCTIONIZED without ratification.
        monkeypatch.setattr("agents.maturity._is_ratified", lambda: False)
        audit = [_grounded_event() for _ in range(20)]
        r = compute_maturity(audit_events=audit, open_candidates=[],
                             promoted_candidates=[_gap("p")], coverage_keys=20)
        assert r.stage != PRODUCTIONIZED  # Tenet 1/4: human-owned graduation


class TestMetricsAndRendering:
    def test_rates_computed_from_grounded_and_gaps(self):
        r = compute_maturity(audit_events=[_grounded_event(), _grounded_event(), _grounded_event()],
                             open_candidates=[_gap("a")], promoted_candidates=[], coverage_keys=5)
        assert r.grounded_answers == 3
        assert r.open_gaps == 1
        assert r.grounding_rate == 0.75
        assert r.decline_rate == 0.25

    def test_coverage_bands(self):
        sparse = compute_maturity(audit_events=[], open_candidates=[], promoted_candidates=[],
                                  coverage_keys=1)
        growing = compute_maturity(audit_events=[], open_candidates=[], promoted_candidates=[],
                                   coverage_keys=DEFAULTS["coverage_growing"])
        broad = compute_maturity(audit_events=[], open_candidates=[], promoted_candidates=[],
                                 coverage_keys=DEFAULTS["coverage_broad"])
        assert sparse.coverage_band == "sparse"
        assert growing.coverage_band == "growing"
        assert broad.coverage_band == "broad"

    def test_report_is_advisory_and_discloses_proxy(self):
        r = compute_maturity(audit_events=[_grounded_event()], open_candidates=[_gap()],
                             promoted_candidates=[], coverage_keys=5)
        text = render_report(r)
        assert "SME Maturity" in text
        assert "PROXY" in text          # the honest proxy is always disclosed
        assert "never gates" in text    # advisory framing is explicit

    def test_empty_instance_has_no_rates(self):
        r = compute_maturity(audit_events=[], open_candidates=[], promoted_candidates=[],
                             coverage_keys=0)
        assert r.grounding_rate is None
        assert r.decline_rate is None
        assert r.stage == SEEDED


class TestFailOpen:
    def test_live_compute_never_raises(self):
        # With no injected inputs it reads live stores; must fail-open to a valid report.
        r = compute_maturity()
        assert r.stage in (SEEDED, CULTIVATING, GROUNDED, PRODUCTIONIZED, SUSTAINING)
