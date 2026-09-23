"""Integration test: the Orchestrator's evidence-gateway seam (agents/orchestrator.py).

Covers the two behaviours the wiring must guarantee:
  1. NO-OP by default — with no evidence provider configured (`evidence.provider: none`, the
     kit default), the orchestrator dispatches and gates exactly as before: no evidence basis
     is appended and the response is not blocked by the gateway.
  2. Pre-dispatch block — when a provider is configured but the packet does not authorize the
     routed specialist (route mismatch) or is not READY, the orchestrator blocks BEFORE calling
     the specialist, and the block still passes through the governance gate.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.orchestrator import Orchestrator
from agents.evidence_gateway import (
    EvidencePacket,
    STATUS_READY,
    STATUS_INSUFFICIENT_EVIDENCE,
    request_digest,
)
from agents.governance_gate import reset_gate


class _StubProvider:
    """A provider that returns a caller-supplied packet, so a test can pin route/status."""

    provider_id = "stub"
    provider_version = "1.0.0"

    def __init__(self, status=STATUS_READY, route=None):
        self._status = status
        self._route = route

    def build_packet(self, request, action_mode, route):
        return EvidencePacket(
            request_digest=request_digest(request), action_mode=action_mode,
            route=tuple(self._route if self._route is not None else route),
            provider_id=self.provider_id, provider_version=self.provider_version,
            corpus_digest="d", status=self._status,
        )


class TestNoOpByDefault:
    def test_default_orchestrator_has_no_evidence_provider(self):
        # The kit default is evidence.provider: none -> provider is None (true no-op).
        assert Orchestrator().evidence_provider is None

    def test_default_response_appends_no_evidence_basis_and_is_not_blocked(self):
        reset_gate()
        out = Orchestrator().handle("what is the deployment policy?", environment="dev")
        assert "Evidence Basis" not in out
        assert "blocked by the evidence gateway" not in out


class TestPreDispatchBlock:
    def test_route_mismatch_blocks_before_dispatch(self):
        reset_gate()
        o = Orchestrator()
        # Packet authorizes a specialist that is NOT the one the request routes to.
        o.evidence_provider = _StubProvider(status=STATUS_READY, route=["nonexistent-specialist"])
        out = o.handle("deploy and scale the platform runtime", environment="dev")
        assert "blocked by the evidence gateway" in out

    def test_not_ready_packet_blocks_before_dispatch(self):
        reset_gate()
        o = Orchestrator()
        o.evidence_provider = _StubProvider(status=STATUS_INSUFFICIENT_EVIDENCE)
        out = o.handle("deploy and scale the platform runtime", environment="dev")
        assert "blocked by the evidence gateway" in out

    def test_ready_authorizing_packet_appends_basis_and_passes(self):
        reset_gate()
        o = Orchestrator()
        # A READY packet whose route matches whatever the router picks: build it against the
        # routed specialist by first asking the router, then pinning that route.
        name = o.route("deploy and scale the platform runtime")
        o.evidence_provider = _StubProvider(status=STATUS_READY, route=[name])
        out = o.handle("deploy and scale the platform runtime", environment="dev")
        assert "blocked by the evidence gateway" not in out
        assert "Evidence Basis" in out
