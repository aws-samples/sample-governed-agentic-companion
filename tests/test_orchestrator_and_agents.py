"""Tests for routing, grounding, and integrity. No AWS, no LLM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.orchestrator import Orchestrator          # noqa: E402
from agents.specialists import build_specialists       # noqa: E402
from governance.integrity_check import GovernanceIntegrity  # noqa: E402


class TestRouting:
    def test_routes_security_question_to_security(self):
        o = Orchestrator()
        assert o.route("how are secrets and credentials handled?") == "security"

    def test_routes_deployment_question_to_platform(self):
        o = Orchestrator()
        assert o.route("what is the deployment and promotion policy?") == "platform"

    def test_every_response_is_gated(self):
        # A routed answer carries the governance-outcome footer (proof it went through the gate).
        o = Orchestrator()
        out = o.handle("what is the deployment policy?")
        assert "Governance Outcome" in out


class TestGrounding:
    def test_known_question_grounds_to_a_source(self):
        agent = build_specialists()["platform"][0]
        score, sources = agent.assess_confidence_detail(
            "deployment is human-owned in every environment", "deployment policy")
        assert sources                     # matched a knowledge file
        assert score >= 0.6                # deterministic floor

    def test_unknown_question_has_no_sources(self):
        agent = build_specialists()["security"][0]
        score, sources = agent.assess_confidence_detail(
            "what is the airspeed velocity of an unladen swallow", "trivia")
        assert not sources
        assert score == 0.5                # below the fact bar


class TestGovernanceIntegrity:
    def test_principles_integrity_verifies(self):
        # The kit ships with a baseline; the check must pass out of the box.
        result = GovernanceIntegrity().verify_principles_integrity()
        assert result["valid"], result["message"]
