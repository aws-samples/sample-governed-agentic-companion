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


class TestRelevanceCalibration:
    """Relevance damping (Tenet 4 honesty): coverage measures 'uses our KB vocabulary', not
    'answers the question'. A response full of domain words that ignores the request must not
    score high on vocabulary alone, while a genuinely on-topic answer still clears the bar."""

    def test_on_topic_grounded_answer_clears_fact_bar(self):
        agent = build_specialists()["platform"][0]
        q = "what is the deployment policy for production?"
        ans = agent.handle_request(q)
        assert agent.assess_confidence(ans, q) >= 0.95

    def test_kb_vocabulary_answer_that_ignores_the_question_is_damped(self):
        agent = build_specialists()["platform"][0]
        # Full of platform-KB vocabulary, but answers a DIFFERENT question than the one asked.
        kb_vocab = ("Deployment is human-owned in every environment including non-production; "
                    "the companion produces the deployment artifact and runbook, a human "
                    "reviews and executes, promotion DEV TEST UAT PROD with a human gate.")
        off_topic_q = "how do I tune Kafka consumer lag partition rebalancing throughput?"
        assert agent.assess_confidence(kb_vocab, off_topic_q) < 0.95

    def test_relevance_matches_paraphrase_via_stem(self):
        agent = build_specialists()["platform"][0]
        # Plural/singular paraphrase must match via shared stem (not be counted a miss):
        # "environments"~"environment", "runbooks"~"runbook". A fully-addressed request -> 1.0.
        rel = agent._request_relevance(
            "deployment environments and runbooks",
            "Deployment across environment tiers is human-owned; the runbook is produced for review.")
        assert rel == 1.0   # deployment (exact), environments~environment, runbooks~runbook (stems)


class TestPeeringDeclineParity:
    """The base peering decline is a reusable method, and the fork-facing llm_answer_or_decline
    helper degrades an ungrounded LLM answer to that decline instead of a hard gate block."""

    def test_peering_decline_offers_paths_not_dead_end(self):
        agent = build_specialists()["platform"][0]
        out = agent._peering_decline("how do I tune Kafka consumer lag?")
        assert "can't ground an answer" in out
        assert "./run.sh ask" in out

    def test_llm_answer_or_decline_delivers_grounded_but_declines_ungrounded(self):
        agent = build_specialists()["platform"][0]
        q = "what is the deployment policy for production?"
        grounded = agent.handle_request(q)   # a real grounded answer clears the bar
        assert agent.llm_answer_or_decline(q, "dev", llm_answer=grounded) == grounded
        # an ungrounded off-topic "LLM answer" degrades to the peering decline
        out = agent.llm_answer_or_decline(
            "how do I tune Kafka consumer lag?", "dev",
            llm_answer="Increase partitions and scale consumers in the group.")
        assert "can't ground an answer" in out


class TestGovernanceIntegrity:
    def test_principles_integrity_verifies(self):
        # The kit ships with a baseline; the check must pass out of the box.
        result = GovernanceIntegrity().verify_principles_integrity()
        assert result["valid"], result["message"]
