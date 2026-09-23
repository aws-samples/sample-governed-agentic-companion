"""Evidence Gateway: provider-neutral evidence-provenance contract.

An optional extension to the always-on GovernanceGate (see governance_gate.py). Every
function here is a no-op when no evidence provider is configured, so existing gate and
orchestrator behavior is unchanged when `evidence.provider: none` (the default).

A configured provider builds an EvidencePacket before a specialist is dispatched. The
packet binds the request, the authorized route, the claims relied on, the reopened
source evidence (with digests), and any gaps/stop-conditions/live-checks. The gate
recomputes the packet digest and validates route/status before allowing a response
through.

Packet integrity is a tamper-EVIDENCE checksum (canonical-JSON SHA-256), not a
cryptographic signature: it proves the packet dict was not altered after it was hashed,
not that the underlying claims are true. See docs/architecture/evidence-gateway.md.

GOVERNANCE GUARDRAIL (Tenet 4 measurement boundary): an EvidencePacket must NEVER become
an input to confidence scoring (base_agent.assess_confidence / assess_confidence_detail).
Evidence can only ever TIGHTEN the gate (block on missing/tampered/not-READY/unauthorized);
a READY packet must never by itself move an exploration-zone answer over the 0.95 fact bar.
Feeding evidence into the score would change HOW Tenet 4 is measured and requires the
PRINCIPLES.md governance handshake, not a config toggle. Enforced by
tests/test_evidence_guardrail.py.

This module has no dependency on any specific evidence source (e.g. a BIBLE markdown
corpus) and no dependency on the agents runtime. A provider (see
agents/evidence_providers/) supplies the actual claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, Sequence, Tuple

SCHEMA_ID = "evidence-packet/v1"

STATUS_READY = "READY"
STATUS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
STATUS_STOP = "STOP"
STATUS_LIVE_CHECK_REQUIRED = "LIVE_CHECK_REQUIRED"

_LIVE_CHECK_CLASSES = {"POINT-IN-TIME", "POINT_IN_TIME"}

_LINE_RUN_RE = re.compile(r"(\d+)(?:-(\d+))?")
_AWS_KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_KV_SECRET_RE = re.compile(r"(?i)(password|passwd|secret|token)\s*[:=]\s*\S+")
EXCERPT_MAX_CHARS = 1600


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_digest(request: str) -> str:
    return hashlib.sha256(request.encode("utf-8")).hexdigest()


def parse_line_ranges(spec: str) -> Tuple[Tuple[int, int], ...]:
    """Parse a citation line spec like "10-20,45" into ((10, 20), (45, 45)).

    Preserves discontiguous ranges rather than collapsing them into one min-max span.
    """
    if not spec:
        return ()
    ranges = []
    for part in spec.split(","):
        m = _LINE_RUN_RE.search(part.strip())
        if not m:
            continue
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if end < start:
            start, end = end, start
        ranges.append((start, end))
    return tuple(ranges)


def redact_secrets(text: str) -> str:
    """Narrow secret redaction: AWS access-key-id and single-token key=value pairs only,
    plus length truncation. This is NOT general-purpose secret scanning; callers must
    not present evidence excerpts as safe from all secret leakage on this alone.
    """
    text = _AWS_KEY_RE.sub("[REDACTED-AWS-KEY]", text)
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    if len(text) > EXCERPT_MAX_CHARS:
        text = text[:EXCERPT_MAX_CHARS]
    return text


def resolve_within_roots(path: str, allowed_roots: Sequence[Path]) -> Optional[Path]:
    """Resolve `path` and return it only if it lives under one of allowed_roots.

    Applies to BOTH absolute and relative citations. There is no bypass for absolute
    paths: a citation of an absolute path is only honored if it resolves under an
    allowed root, closing the unrestricted-absolute-path gap in the original
    single-vault BIBLE-gateway reference design.
    """
    roots = [Path(r).resolve() for r in allowed_roots]
    if not roots:
        return None
    candidate = Path(path)
    candidates = [candidate] if candidate.is_absolute() else [root / candidate for root in roots]
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        for root in roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return resolved
    return None


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_packet_hash(data: dict) -> str:
    """Canonical-JSON SHA-256 over the packet dict with packet_hash excluded."""
    payload = dict(data)
    payload.pop("packet_hash", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def verify_packet(packet: dict) -> bool:
    """Recompute the packet digest and compare it to the stored packet_hash."""
    stored = packet.get("packet_hash")
    if not stored:
        return False
    return compute_packet_hash(packet) == stored


@dataclass(frozen=True)
class EvidenceReference:
    """One evidence citation and the result of attempting to reopen it."""

    citation: str
    path: str
    line_ranges: Tuple[Tuple[int, int], ...] = ()
    reopened: bool = False
    excerpt: str = ""
    digest: str = ""  # sha256 of the PRE-redaction excerpt (integrity checksum)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "citation": self.citation,
            "path": self.path,
            "line_ranges": [list(r) for r in self.line_ranges],
            "reopened": self.reopened,
            "excerpt": self.excerpt,
            "digest": self.digest,
            "error": self.error,
        }


@dataclass(frozen=True)
class EvidenceClaim:
    """One claim relied on to authorize the response, with its reopened evidence."""

    claim_id: str
    claim_class: str
    assertion: str
    as_of: str = ""
    authority: str = "UNSPECIFIED"
    references: Tuple[EvidenceReference, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.claim_id,
            "class": self.claim_class,
            "assertion": self.assertion,
            "as_of": self.as_of,
            "authority": self.authority,
            "references": [r.to_dict() for r in self.references],
        }


@dataclass(frozen=True)
class EvidencePacket:
    """Immutable, self-hashing evidence packet built before specialist dispatch."""

    request_digest: str
    action_mode: str
    route: Tuple[str, ...]
    provider_id: str
    provider_version: str
    corpus_digest: str
    status: str = STATUS_READY
    claims: Tuple[EvidenceClaim, ...] = ()
    gaps: Tuple[str, ...] = ()
    stop_conditions: Tuple[str, ...] = ()
    live_checks: Tuple[str, ...] = ()
    authorization_notes: Tuple[str, ...] = ()
    mutation_boundary: str = (
        "This packet authorizes read-only preparation of artifacts only. No agent may "
        "deploy, apply, promote, restart, or otherwise mutate any environment."
    )
    schema: str = SCHEMA_ID
    generated_at: str = field(default_factory=utc_now_iso)

    @property
    def can_dispatch(self) -> bool:
        return self.status == STATUS_READY

    def authorizes(self, specialist: str) -> bool:
        return specialist in self.route

    def to_dict(self) -> dict:
        data = {
            "schema": self.schema,
            "request_digest": self.request_digest,
            "action_mode": self.action_mode,
            "route": list(self.route),
            "generated_at": self.generated_at,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "corpus_digest": self.corpus_digest,
            "status": self.status,
            "claims": [c.to_dict() for c in self.claims],
            "gaps": list(self.gaps),
            "stop_conditions": list(self.stop_conditions),
            "live_checks": list(self.live_checks),
            "authorization_notes": list(self.authorization_notes),
            "mutation_boundary": self.mutation_boundary,
        }
        data["packet_hash"] = compute_packet_hash(data)
        return data

    def render_basis(self) -> str:
        """Compact, human-readable evidence basis appended to the final delivered output."""
        claim_ids = ", ".join(c.claim_id for c in self.claims) or "none"
        lines = [
            "## Evidence Basis",
            f"- Provider: {self.provider_id} v{self.provider_version}",
            f"- Status: {self.status}",
            f"- Claims: {claim_ids}",
            f"- Packet digest: {self.to_dict()['packet_hash']}",
        ]
        if self.gaps:
            lines.append(f"- Gaps: {', '.join(self.gaps)}")
        if self.stop_conditions:
            lines.append(f"- Stop conditions: {', '.join(self.stop_conditions)}")
        if self.live_checks:
            lines.append(f"- Required live checks: {', '.join(self.live_checks)}")
        return "\n".join(lines) + "\n"


def derive_status(
    claims: Sequence[EvidenceClaim],
    gaps: Sequence[str],
    current_state_required: bool,
) -> Tuple[str, Tuple[str, ...], Tuple[str, ...]]:
    """Derive (status, stop_conditions, live_checks) from selected claims and gaps.

    First match wins:
      no claims or gaps present               -> INSUFFICIENT_EVIDENCE
      any CONTESTED/SUPERSEDED claim           -> STOP
      current-state request + POINT-IN-TIME    -> LIVE_CHECK_REQUIRED
      else                                     -> READY
    """
    stop_conditions = []
    live_checks = []
    for claim in claims:
        cls = (claim.claim_class or "").upper()
        if cls == "CONTESTED":
            stop_conditions.append(
                f"{claim.claim_id}: CONTESTED, preserve all positions and resolve the "
                "open question before dependent work."
            )
        elif cls == "SUPERSEDED":
            stop_conditions.append(
                f"{claim.claim_id}: SUPERSEDED, do not restore or recommend this behavior."
            )
        elif cls in _LIVE_CHECK_CLASSES:
            live_checks.append(
                f"{claim.claim_id}: re-verify current state (AsOf {claim.as_of or 'unknown'})."
            )

    if not claims or gaps:
        return STATUS_INSUFFICIENT_EVIDENCE, tuple(stop_conditions), tuple(live_checks)
    if stop_conditions:
        return STATUS_STOP, tuple(stop_conditions), tuple(live_checks)
    if current_state_required and live_checks:
        return STATUS_LIVE_CHECK_REQUIRED, tuple(stop_conditions), tuple(live_checks)
    return STATUS_READY, tuple(stop_conditions), tuple(live_checks)


def packet_validity_reason(packet: EvidencePacket, request: str, specialist: str) -> str:
    """Check whether packet authorizes dispatching specialist for this exact request,
    right now. Returns "" when it does, else a short machine-checkable reason code.

    Shared by the orchestrator's pre-dispatch seam and the gate's evidence detector, so
    a request that fails one of these checks is blocked before a specialist ever runs,
    not only after the fact.
    """
    if not verify_packet(packet.to_dict()):
        return "invalid_digest"
    if packet.request_digest != request_digest(request):
        return "request_mismatch"
    if not packet.authorizes(specialist):
        return "route_mismatch"
    if packet.status != STATUS_READY:
        return f"not_ready:{packet.status}"
    return ""


class EvidenceProvider(Protocol):
    """Provider-neutral contract a configured evidence source must implement."""

    provider_id: str
    provider_version: str

    def build_packet(
        self, request: str, action_mode: str, route: Sequence[str]
    ) -> EvidencePacket: ...


class UnavailableProvider:
    """Stub for 'configured but could not initialize or is unrecognized'.

    Always returns a blocking INSUFFICIENT_EVIDENCE packet. A configured provider that
    cannot be reached must fail closed, never silently fail open.
    """

    provider_version = "unavailable"

    def __init__(self, provider_id: str, reason: str):
        self.provider_id = provider_id
        self._reason = reason

    def build_packet(self, request: str, action_mode: str, route: Sequence[str]) -> EvidencePacket:
        return EvidencePacket(
            request_digest=request_digest(request),
            action_mode=action_mode,
            route=tuple(route),
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            corpus_digest="",
            status=STATUS_INSUFFICIENT_EVIDENCE,
            gaps=(f"provider '{self.provider_id}' unavailable: {self._reason}",),
        )


def build_evidence_packet(
    provider: Optional[EvidenceProvider],
    request: str,
    action_mode: str,
    route: Sequence[str],
) -> Optional[EvidencePacket]:
    """Build a packet if a provider is configured; return None otherwise.

    A provider is never allowed to fail open: any exception raised while building the
    packet is converted into a blocking INSUFFICIENT_EVIDENCE packet instead of being
    swallowed, so a misbehaving provider blocks dispatch rather than silently disabling
    enforcement.
    """
    if provider is None:
        return None
    try:
        return provider.build_packet(request, action_mode, route)
    except Exception as exc:
        return EvidencePacket(
            request_digest=request_digest(request),
            action_mode=action_mode,
            route=tuple(route),
            provider_id=getattr(provider, "provider_id", "unknown"),
            provider_version=getattr(provider, "provider_version", "unknown"),
            corpus_digest="",
            status=STATUS_INSUFFICIENT_EVIDENCE,
            gaps=(f"provider raised {type(exc).__name__}: {exc}",),
        )
