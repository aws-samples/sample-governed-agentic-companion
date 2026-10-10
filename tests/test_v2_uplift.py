"""Tests for the v2.0.0 uplift: action audit, feedback/evolution loop, layered memory, staged
mode, sensors, the front-door parity guard, and the local gate MCP surface. No AWS, no LLM.

Each test isolates its on-disk store under tmp_path via the GAC_*_PATH env vars so the suite
never writes to the repo's real .audit/.feedback/.plans dirs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.governance_gate import GovernanceGate, reset_gate  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point every v2 store at a throwaway dir and reset shared singletons."""
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


# --- action audit trail (Task 1 / ENF-004) ------------------------------------

class TestAuditTrail:
    def test_gate_emits_audit_event_metadata_only(self):
        from agents.audit import get_audit
        GovernanceGate().enforce("a clean reviewed plan", agent="platform", request="q",
                                 footer=False, used_llm=False)
        events = get_audit().tail(5)
        assert events and events[-1]["event"] == "gate_decision"
        ev = events[-1]
        # metadata only — never the raw text
        assert "sha256_16" in ev["request"] and "sha256_16" in ev["response"]
        assert "a clean reviewed plan" not in str(ev)

    def test_audit_records_block_and_findings(self):
        from agents.audit import get_audit
        GovernanceGate().enforce("I have deployed to production.", agent="platform",
                                 footer=False)
        ev = get_audit().tail(1)[0]
        assert ev["blocked"] is True
        assert any(f["code"] == "T1_DEPLOY_CLAIM" for f in ev["findings"])

    def test_secret_never_written_to_trail(self):
        from agents.audit import get_audit
        GovernanceGate().enforce("password = sup3rSecretValueABCDEF", agent="x", footer=False)
        raw = Path(get_audit().path).read_text()
        assert "sup3rSecretValueABCDEF" not in raw


# --- feedback / evolution loop (Task 2, Tenet 8) ------------------------------

class TestFeedbackLoop:
    def test_grounding_gap_stages_candidate(self):
        from agents.feedback import get_feedback
        get_feedback().record_work(agent="platform", request="unknown topic", trust_zone="verified",
                                   confidence=0.5, sources=[], blocked=False)
        assert len(get_feedback().list_candidates()) == 1

    def test_grounded_answer_stages_nothing(self):
        from agents.feedback import get_feedback
        get_feedback().record_work(agent="platform", request="q", trust_zone="verified",
                                   confidence=0.99, sources=["tier_a/platform/x"], blocked=False)
        assert get_feedback().list_candidates() == []

    def test_blocked_is_not_a_learning(self):
        from agents.feedback import get_feedback
        get_feedback().record_work(agent="platform", request="q", trust_zone="verified",
                                   confidence=None, sources=[], blocked=True)
        assert get_feedback().list_candidates() == []

    def test_promote_refuses_tier_a(self):
        from agents.feedback import get_feedback
        fb = get_feedback()
        fb.record_work(agent="platform", request="q", trust_zone="verified",
                       confidence=0.5, sources=[], blocked=False)
        cid = fb.list_candidates()[0]["id"]
        res = fb.promote(cid, knowledge_key="tier_a/platform/x", summary="s", layer="team")
        assert res["ok"] is False

    def test_promote_tier_b_team_succeeds(self):
        from agents.feedback import get_feedback, STATUS_PROMOTED
        fb = get_feedback()
        fb.record_work(agent="platform", request="q", trust_zone="verified",
                       confidence=0.5, sources=[], blocked=False)
        cid = fb.list_candidates()[0]["id"]
        res = fb.promote(cid, knowledge_key="tier_b/platform/x", summary="reviewed", layer="team")
        assert res["ok"] is True
        promoted = fb.list_candidates(status=STATUS_PROMOTED)
        assert promoted[0]["promotion"]["layer"] == "team"

    def test_promote_refuses_org_layer(self):
        from agents.feedback import get_feedback
        fb = get_feedback()
        fb.record_work(agent="platform", request="q", trust_zone="verified",
                       confidence=0.5, sources=[], blocked=False)
        cid = fb.list_candidates()[0]["id"]
        assert fb.promote(cid, knowledge_key="tier_b/x", summary="s", layer="org")["ok"] is False


# --- layered memory (Task 3) --------------------------------------------------

class TestMemoryLayers:
    def test_noop_default_preserves_baseline(self):
        from agents.memory_layers import resolve_layers
        base = {"tier_b/x": {"summary": "a"}}
        assert resolve_layers(base, None) == base

    def test_project_overrides_team_overrides_org(self):
        from agents.memory_layers import resolve_layers
        base = {"tier_b/x": {"summary": "org", "_layer": "org"}}
        out = resolve_layers(base, [
            {"knowledge_key": "tier_b/x", "layer": "team", "data": {"summary": "team"}},
            {"knowledge_key": "tier_b/x", "layer": "project", "data": {"summary": "project"}},
        ])
        assert out["tier_b/x"]["summary"] == "project"

    def test_layer_cannot_touch_tier_a(self):
        from agents.memory_layers import resolve_layers
        base = {"tier_a/locked": {"summary": "LOCKED"}}
        out = resolve_layers(base, [
            {"knowledge_key": "tier_a/locked", "layer": "project", "data": {"summary": "HIJACK"}},
        ])
        assert out["tier_a/locked"]["summary"] == "LOCKED"


# --- staged mode (Task 5) -----------------------------------------------------

class TestStagedMode:
    def _planner(self, tmp_path):
        from agents.orchestrator import Orchestrator
        from agents.staged import StagedPlanner
        return StagedPlanner(Orchestrator(), plans_dir=tmp_path / "plans")

    def test_plan_creates_draft(self, tmp_path):
        p = self._planner(tmp_path).plan("harden the platform runbook")
        assert p.status == "draft" and len(p.steps) >= 1

    def test_execute_refused_before_approval(self, tmp_path):
        planner = self._planner(tmp_path)
        p = planner.plan("harden the platform runbook")
        res = planner.execute_next(p.id)
        assert res["ok"] is False and "not approved" in res["message"]

    def test_execute_after_approval_produces_gated_artifact(self, tmp_path):
        planner = self._planner(tmp_path)
        p = planner.plan("harden the platform runbook")
        planner.approve(p.id)
        res = planner.execute_next(p.id)
        assert res["ok"] is True and res["artifact"]


# --- sensors (Task 6) ---------------------------------------------------------

class TestSensors:
    def test_absolute_claim_warns_not_blocks(self):
        from agents.sensors import run_sensors
        results = {r.sensor: r for r in run_sensors("This guarantees 100% uptime.")}
        assert results["no-absolute-claim"].passed is False

    def test_sensors_are_advisory_listable(self):
        from agents.sensors import list_sensors
        names = {s["name"] for s in list_sensors()}
        assert {"claim-sources", "traceability"}.issubset(names)


# --- parity / determinism guard (Task 6) --------------------------------------

class TestParityGuard:
    def test_front_doors_agree_and_deterministic(self):
        from scripts.parity_check import run
        assert run() == 0


# --- local gate MCP surface (Task 4) ------------------------------------------

class TestGateMcpSurface:
    def test_govern_blocks_deploy_claim(self):
        from frontdoor.gate_mcp_server import govern
        v = govern("I have deployed to prod.")
        assert v["verdict"] == "BLOCK"

    def test_govern_passes_clean(self):
        from frontdoor.gate_mcp_server import govern
        v = govern("A reviewed plan for a human to apply.")
        assert v["verdict"] == "PASS"


# --- governance core unchanged (regression guard for the uplift) --------------

class TestGovernanceCoreUnchanged:
    def test_gate_has_no_off_switch(self):
        """There is no parameter/flag/env that makes enforce() not block an absolute violation."""
        g = GovernanceGate()
        for kw in ({}, {"footer": False}, {"used_llm": True}, {"environment": "prod"}):
            r = g.enforce("I have deployed to prod.", agent="x", **kw)
            assert r.blocked is True
