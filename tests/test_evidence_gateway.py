"""Tests for the provider-neutral evidence-gateway contract (agents/evidence_gateway.py).

Contract under test:
- EvidencePacket.to_dict() embeds a canonical-JSON SHA-256 packet_hash.
- verify_packet() recomputes the digest; any field change invalidates the packet.
- derive_status() implements the documented status semantics: missing evidence ->
  INSUFFICIENT_EVIDENCE, a CONTESTED/SUPERSEDED claim -> STOP, a POINT-IN-TIME claim on a
  current-state request -> LIVE_CHECK_REQUIRED, otherwise READY.
- EvidencePacket.authorizes() enforces the route allowlist.
- build_evidence_packet() is a no-op (returns None) with no provider configured, and never
  lets a provider fail open (an exception is converted into a blocking packet).
- parse_line_ranges() preserves discontiguous ranges.
- redact_secrets() redacts the two documented narrow patterns.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.evidence_gateway import (
    EvidenceClaim,
    EvidencePacket,
    EvidenceReference,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_LIVE_CHECK_REQUIRED,
    STATUS_READY,
    STATUS_STOP,
    build_evidence_packet,
    compute_packet_hash,
    derive_status,
    parse_line_ranges,
    redact_secrets,
    request_digest,
    verify_packet,
)


def _packet(**overrides) -> EvidencePacket:
    defaults = dict(
        request_digest=request_digest("enable mtls for asm"),
        action_mode="analyze",
        route=("platform",),
        provider_id="test-provider",
        provider_version="1.0.0",
        corpus_digest="deadbeef",
        status=STATUS_READY,
    )
    defaults.update(overrides)
    return EvidencePacket(**defaults)


class TestPacketDigest:
    def test_correctly_hashed_packet_validates(self):
        assert verify_packet(_packet().to_dict()) is True

    def test_changing_any_field_invalidates_the_packet(self):
        data = _packet().to_dict()
        data["status"] = STATUS_STOP
        assert verify_packet(data) is False

    def test_changing_route_invalidates_the_packet(self):
        data = _packet().to_dict()
        data["route"] = ["devops"]
        assert verify_packet(data) is False

    def test_missing_packet_hash_is_invalid(self):
        data = _packet().to_dict()
        del data["packet_hash"]
        assert verify_packet(data) is False

    def test_compute_packet_hash_ignores_stored_hash_field(self):
        data = _packet().to_dict()
        recomputed = compute_packet_hash(data)
        assert recomputed == data["packet_hash"]

    def test_changing_reopened_source_content_invalidates_packet(self):
        ref = EvidenceReference(citation="a.py:1-2", path="a.py", reopened=True,
                                 excerpt="original", digest="abc123")
        claim = EvidenceClaim(claim_id="R-A-1", claim_class="INVARIANT",
                              assertion="x", references=(ref,))
        packet = _packet(claims=(claim,))
        data = packet.to_dict()
        assert verify_packet(data) is True

        # Evidence content changed after the packet was built (digest now stale).
        tampered = dict(data)
        tampered["claims"] = [dict(data["claims"][0])]
        tampered["claims"][0]["references"] = [dict(data["claims"][0]["references"][0])]
        tampered["claims"][0]["references"][0]["excerpt"] = "changed content"
        assert verify_packet(tampered) is False


class TestRouteAuthorization:
    def test_authorizes_listed_specialist(self):
        assert _packet(route=("platform", "devops")).authorizes("platform") is True

    def test_does_not_authorize_unlisted_specialist(self):
        assert _packet(route=("platform",)).authorizes("developer") is False


class TestDeriveStatus:
    def test_no_claims_is_insufficient_evidence(self):
        status, stops, checks = derive_status([], [], current_state_required=False)
        assert status == STATUS_INSUFFICIENT_EVIDENCE

    def test_gaps_present_is_insufficient_evidence(self):
        claim = EvidenceClaim(claim_id="R-A-1", claim_class="INVARIANT", assertion="x")
        status, _, _ = derive_status([claim], ["no evidence reopened"], current_state_required=False)
        assert status == STATUS_INSUFFICIENT_EVIDENCE

    def test_contested_claim_produces_stop(self):
        claim = EvidenceClaim(claim_id="R-A-2", claim_class="CONTESTED", assertion="x")
        status, stops, _ = derive_status([claim], [], current_state_required=False)
        assert status == STATUS_STOP
        assert stops and "CONTESTED" in stops[0]

    def test_superseded_claim_produces_stop(self):
        claim = EvidenceClaim(claim_id="R-A-3", claim_class="SUPERSEDED", assertion="x")
        status, stops, _ = derive_status([claim], [], current_state_required=False)
        assert status == STATUS_STOP
        assert stops and "SUPERSEDED" in stops[0]

    def test_point_in_time_claim_requires_live_check_for_current_state_request(self):
        claim = EvidenceClaim(claim_id="R-A-4", claim_class="POINT-IN-TIME",
                              assertion="x", as_of="2026-01-01")
        status, _, checks = derive_status([claim], [], current_state_required=True)
        assert status == STATUS_LIVE_CHECK_REQUIRED
        assert checks

    def test_point_in_time_claim_without_current_state_request_is_ready(self):
        claim = EvidenceClaim(claim_id="R-A-5", claim_class="POINT-IN-TIME", assertion="x")
        status, _, _ = derive_status([claim], [], current_state_required=False)
        assert status == STATUS_READY

    def test_ordinary_invariant_claim_is_ready(self):
        claim = EvidenceClaim(claim_id="R-A-6", claim_class="INVARIANT", assertion="x")
        status, _, _ = derive_status([claim], [], current_state_required=False)
        assert status == STATUS_READY


class TestBuildEvidencePacketNoOp:
    def test_no_provider_returns_none(self):
        assert build_evidence_packet(None, "request", "analyze", ["platform"]) is None

    def test_provider_cannot_silently_fail_open(self):
        class Boom:
            provider_id = "boom"
            provider_version = "0.0.1"

            def build_packet(self, request, action_mode, route):
                raise RuntimeError("provider exploded")

        packet = build_evidence_packet(Boom(), "request", "analyze", ["platform"])
        assert packet is not None
        assert packet.status == STATUS_INSUFFICIENT_EVIDENCE
        assert packet.gaps

    def test_working_provider_packet_round_trips(self):
        class Working:
            provider_id = "working"
            provider_version = "1.2.3"

            def build_packet(self, request, action_mode, route):
                return _packet(provider_id=self.provider_id, provider_version=self.provider_version)

        packet = build_evidence_packet(Working(), "request", "analyze", ["platform"])
        assert packet.provider_id == "working"
        assert verify_packet(packet.to_dict())


class TestLineRangeParsing:
    def test_single_range(self):
        assert parse_line_ranges("10-20") == ((10, 20),)

    def test_discontiguous_ranges_are_preserved_not_collapsed(self):
        assert parse_line_ranges("10-20,45") == ((10, 20), (45, 45))

    def test_single_line(self):
        assert parse_line_ranges("7") == ((7, 7),)

    def test_empty_spec(self):
        assert parse_line_ranges("") == ()


class TestSecretRedaction:
    def test_redacts_aws_access_key(self):
        # Assemble an AKIA-shaped value at runtime from fragments (no complete key literal in
        # source, and not the well-known AWS sample suffix), so a secret scanner does not flag
        # this test file itself while still exercising the redactor.
        secret = ("AK" + "IA") + ("Z" * 12) + "9WXY"   # 16-char body, upper/digits, not a real key
        redacted = redact_secrets(f"key is {secret}")
        assert secret not in redacted
        assert "[REDACTED-AWS-KEY]" in redacted

    def test_redacts_key_value_secret(self):
        redacted = redact_secrets("password: supersecret123")
        assert "supersecret123" not in redacted
        assert "[REDACTED]" in redacted

    def test_truncates_long_excerpts(self):
        redacted = redact_secrets("x" * 5000)
        assert len(redacted) == 1600


class TestFinalOutputRendersEvidenceBasis:
    def test_render_basis_includes_provider_claims_and_digest(self):
        claim = EvidenceClaim(claim_id="R-A-1", claim_class="INVARIANT", assertion="x")
        packet = _packet(claims=(claim,))
        basis = packet.render_basis()
        assert packet.provider_id in basis
        assert "R-A-1" in basis
        assert packet.to_dict()["packet_hash"] in basis
