"""Guard test for the evidence-gateway GOVERNANCE GUARDRAIL (Tenet 4 measurement boundary).

The guardrail: an EvidencePacket must NEVER become an input to confidence scoring. In this
companion the confidence path is base_agent.assess_confidence_detail (and its wrapper
assess_confidence). Evidence flows only to the gate's evidence detector
(GovernanceGate._detect_evidence), where it can only BLOCK. A READY packet must never by
itself move an exploration-zone answer over the 0.95 fact bar.

These tests are mechanical: they fail if a future change wires evidence into the confidence
path, so the guardrail cannot silently erode.
"""

import ast
import inspect
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.base_agent import BaseAgent
from agents.governance_gate import GovernanceGate, gate_output, reset_gate
from agents.evidence_gateway import EvidencePacket, STATUS_READY, request_digest

_AGENTS_DIR = PROJECT_ROOT / "agents"
_FORBIDDEN = {"evidence", "packet", "evidence_packet", "evidencepacket"}


class TestConfidenceMethodsTakeNoEvidence:
    def test_assess_confidence_detail_has_no_evidence_parameter(self):
        params = set(inspect.signature(BaseAgent.assess_confidence_detail).parameters)
        leaked = params & _FORBIDDEN
        assert not leaked, f"assess_confidence_detail must not accept evidence ({leaked})."

    def test_assess_confidence_has_no_evidence_parameter(self):
        params = set(inspect.signature(BaseAgent.assess_confidence).parameters)
        leaked = params & _FORBIDDEN
        assert not leaked, f"assess_confidence must not accept evidence ({leaked})."

    def test_base_agent_does_not_import_evidence_gateway(self):
        source = (_AGENTS_DIR / "base_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        offenders = [m for m in imported if "evidence" in m.lower()]
        assert not offenders, (
            f"base_agent.py must not import the evidence gateway (found {offenders})."
        )


class TestReadyPacketDoesNotRaiseConfidence:
    def _ready_packet(self, request, agent="platform"):
        return EvidencePacket(
            request_digest=request_digest(request), action_mode="assessment",
            route=(agent,), provider_id="test", provider_version="1",
            corpus_digest="d", status=STATUS_READY,
        )

    def test_below_bar_llm_answer_stays_blocked_even_with_ready_packet(self):
        reset_gate()
        req = "explain the deployment policy"
        pkt = self._ready_packet(req)
        out = gate_output(
            "some LLM-authored answer", agent="platform", request=req,
            used_llm=True, confidence=0.40, evidence=pkt, footer=False,
        )
        assert "blocked by the governance gate" in out, (
            "a READY packet must not lift a below-bar LLM answer over the fact bar."
        )

    def test_ready_packet_does_not_change_the_measured_confidence(self):
        reset_gate()
        req = "explain the deployment policy"
        pkt = self._ready_packet(req)
        result = GovernanceGate().enforce(
            "answer", agent="platform", request=req,
            confidence=0.40, used_llm=True, evidence=pkt, footer=False,
        )
        assert result.confidence == 0.40
