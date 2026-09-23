"""Governance Gate — the always-on enforcement seam for agent output.

Every agent response passes through this gate before it reaches the user. The gate ENFORCES
the governance model in PRINCIPLES.md rather than only describing it, and there is no mode,
flag, or configuration that turns it off — an unbypassable principle must not have a bypass.

Enforcement (hybrid, honest about where hallucination originates):
  - Tenets 1/3/6 — ABSOLUTE. A response that claims a deployment/mutation was performed,
    claims a write to a read-only system of record, or leaks secret material is HARD-BLOCKED
    and withheld (the original text is never echoed, so a leaked secret can't ride along).
  - Tenet 4 — No hallucination tolerance, applied to the EXPLORATION (LLM) zone. An answer
    produced with an LLM that cannot be grounded to the fact bar (< 0.95, or not measured) is
    BLOCKED, not decorated with a warning.
  - Tenets 11/12 — first-increment text-layer detectors (bounded egress / bounded resource):
    a fetch-and-execute DIRECTIVE is BLOCKED; descriptive/assessment mentions and self-narrated
    runaway are surfaced as INFO. These are observability signals, never proof of an action.
  - The VERIFIED (deterministic) zone is grounded by construction and is not blocked on the
    confidence heuristic; it is still subject to the absolute 1/3/6 blocks.

Confidence must be MEASURED, never guessed. If no measurement is available for an
exploration-zone answer, it cannot be shown to have met the bar, so it is treated as
below-bar (fail safe — Tenet 4).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .evidence_gateway import EvidencePacket, packet_validity_reason

logger = logging.getLogger(__name__)

CONFIDENCE_FACT_THRESHOLD = 0.95


class Severity(str, Enum):
    INFO = "info"
    BLOCK = "block"


@dataclass
class Finding:
    tenet: str
    code: str
    severity: Severity
    message: str
    evidence: str = ""


@dataclass
class GateResult:
    response: str
    agent: str
    trust_zone: str            # "verified" | "exploration"
    confidence: Optional[float]
    findings: List[Finding] = field(default_factory=list)
    blocked: bool = False
    sources: List[str] = field(default_factory=list)

    @property
    def blocking_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == Severity.BLOCK]

    def audit_dict(self) -> dict:
        return {
            "agent": self.agent,
            "trust_zone": self.trust_zone,
            "confidence": None if self.confidence is None else round(self.confidence, 3),
            "blocked": self.blocked,
            "findings": [
                {"tenet": f.tenet, "code": f.code, "severity": f.severity.value,
                 "message": f.message}
                for f in self.findings
            ],
        }


# --- Deterministic detectors ---------------------------------------------------

# Tenet 1 — deployment/mutation described as already DONE (not proposed).
_T1_DONE_PATTERNS = [
    r"\bI (?:have )?(?:deployed|applied|promoted|restarted|executed|ran|launched)\b",
    r"\b(?:successfully|now) (?:deployed|applied|promoted|restarted|started|stopped)\b",
    r"\bhas been (?:deployed|applied|promoted|restarted)\b",
]
_T1_DEPLOY_COMMANDS = [
    r"\bterraform apply\b", r"\bcdk deploy\b", r"\bcloudformation deploy\b",
    r"\bansible-playbook\b", r"\bkubectl (?:apply|delete)\b",
]

# Tenet 3 — claim of writing to a system of record. The specific system-of-record NAMES are
# configurable per deployment via the GAC_SYSTEMS_OF_RECORD env (kept generic here); the verbs
# are universal.
_SOR_NAMES = os.getenv(
    "GAC_SYSTEMS_OF_RECORD",
    "production database|the datastore|the data store|the system of record|the mainframe|"
    "the message queue|the scheduler|the directory",
)
_T3_SOR_WRITE_PATTERNS = [
    rf"\b(?:updated|modified|wrote to|changed|deleted from) (?:the )?(?:{_SOR_NAMES})\b",
    r"\bran (?:an )?(?:UPDATE|DELETE|INSERT|DROP)\b",
]

# Tenet 6 — secret-like material.
_T6_SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"(?i)(?:password|passwd|secret|token)\s*[:=]\s*\S+", "inline credential"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "private key material"),
]

# Tenet 11 — bounded egress.
_T11_FETCH_EXECUTE_PATTERNS = [
    r"(?i)\bcurl\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|)sh\b",
    r"(?i)\bwget\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|)sh\b",
    r"(?i)\b(?:iwr|invoke-webrequest|irm|invoke-restmethod)\b[^\n|]*\|\s*(?:iex|invoke-expression)\b",
    r"(?i)\bpip\s+install\b[^\n]*--index-url\s+https?://(?!(?:pypi\.org|files\.pythonhosted\.org))",
]
_T11_EGRESS_CLAIM_PATTERNS = [
    (r"(?i)\bI (?:have )?(?:fetched|downloaded|cloned|pulled) (?:from )?https?://\S+",
     "claims an outbound fetch/clone"),
    (r"(?i)\b(?:bypass|circumvent|disable|route around|reconfigure) (?:the )?"
     r"(?:egress|proxy|allowlist|allowed[_ ]?domains|firewall)\b",
     "suggests circumventing the platform egress path"),
]
_T11_DIRECTIVE_MARKERS = [
    r"(?im)^\s*(?:\$|#)?\s*(?:sudo\s+)?(?:curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod|pip)\b",
    r"(?i)\b(?:run|execute|paste|copy[- ]?(?:and[- ]?)?paste|bootstrap|set(?:\s+\w+){0,3}\s+up|install(?:\s+\w+){0,3})\b[^\n]{0,20}:\s*`?\s*(?:sudo\s+)?(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|pip)\b",
    r"(?i)\bI(?:'ll| will| am going to| have|'ve)?\s+(?:run|execute|fetch and run|pipe)\b[^\n]*\b(?:curl|wget|iwr|irm|bash|sh|iex)\b",
]
_T11_DESCRIPTIVE_MARKERS = [
    r"(?i)\b(?:the|this|its?|their|component|playbook|pipeline|image[- ]?builder|bootstrap|user[- ]?data|step|script|role|module)\b[^\n]*\b(?:does|runs|uses|performs|executes|fetches|downloads|invokes|contains|includes|references)\b",
    r"(?i)\b(?:today|currently|legitimately|for example|e\.g\.|such as)\b",
    r"(?i)\b(?:describes?|description|assessment|assessing|analy[sz]e|analysis|note that|observed?)\b",
]

# Tenet 12 — bounded resource.
_T12_RUNAWAY_PATTERNS = [
    (r"(?i)\b(?:retrying|retry)\b[^\n]*\b(?:again|indefinitely|until it (?:works|passes))\b",
     "describes an unbounded retry loop"),
    (r"(?i)\b(?:looping|iterate|iterating)\b[^\n]*\b(?:forever|indefinitely|without (?:a )?limit)\b",
     "describes a non-terminating loop"),
    (r"(?i)\b(?:ignore|exceed|over) (?:the )?(?:token|cost|budget|rate)[- ]?(?:limit|ceiling|cap|budget)\b",
     "describes exceeding a resource ceiling"),
]


def _truncate(text: str, limit: int = 120) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Tenets the gate evaluates per-response, with the finding-code prefix that proves a detector
# ran — so the footer's "tenets checked" line reflects the real detectors, never a hand list.
_CHECKED_TENETS = [
    ("Tenet 1", "T1", "human-owned deployment (no executed deploy/mutation claims)"),
    ("Tenet 3", "T3", "bounded agency (no writes to read-only systems of record)"),
    ("Tenet 4", "T4", "no hallucination tolerance (grounding ≥ 95% for LLM answers)"),
    ("Tenet 6", "T6", "data governance (no secret/credential material in output)"),
    ("Tenet 11", "T11", "bounded egress (no fetch-and-execute of remote content)"),
    ("Tenet 12", "T12", "bounded resource (no self-narrated runaway/unbounded loop)"),
]


class GovernanceGate:
    """The single, always-on enforcement seam. There is no observe mode."""

    def enforce(
        self,
        response: str,
        *,
        agent: str,
        request: str = "",
        environment: str = "dev",
        confidence: Optional[float] = None,
        used_llm: Optional[bool] = None,
        sources: Optional[List[str]] = None,
        footer: bool = True,
        evidence: Optional[EvidencePacket] = None,
    ) -> GateResult:
        """`evidence` is an optional EvidencePacket from the evidence gateway (see
        evidence_gateway.py). It defaults to None, a no-op: with no evidence provider
        configured, enforcement is byte-for-byte unchanged from before this parameter."""
        trust_zone = "exploration" if used_llm else "verified"
        sources = sources or []

        findings: List[Finding] = []
        findings.extend(self._detect_tenet1(response))
        findings.extend(self._detect_tenet3(response))
        findings.extend(self._detect_tenet6(response))
        findings.extend(self._detect_tenet4(confidence, trust_zone))
        findings.extend(self._detect_tenet11(response))
        findings.extend(self._detect_tenet12(response))
        findings.extend(self._detect_evidence(evidence, agent, request))

        blocking = [f for f in findings if f.severity == Severity.BLOCK]
        if blocking:
            out_response = self._block_message(blocking, agent)
            blocked = True
        else:
            out_response = response
            blocked = False
            if footer:
                out_response += self._governance_footer(
                    findings=findings, confidence=confidence, sources=sources,
                    trust_zone=trust_zone,
                )

        result = GateResult(
            response=out_response, agent=agent, trust_zone=trust_zone,
            confidence=confidence, findings=findings, blocked=blocked, sources=sources,
        )
        logger.info("[GovernanceGate] %s",
                    json.dumps({"env": environment, **result.audit_dict()}, default=str))
        return result

    # -- block action ------------------------------------------------------------

    def _block_message(self, blocking: List[Finding], agent: str) -> str:
        lines = [
            f"⛔ Response blocked by the governance gate ({agent}).",
            "",
            "This output violated a governance boundary and was withheld:",
        ]
        for f in blocking:
            lines.append(f"  • [{f.tenet}] {f.message}")
        lines.append("")
        lines.append("Per PRINCIPLES.md these boundaries cannot be relaxed by configuration or "
                     "prompt. Revise the request, or produce a review-ready artifact (a human "
                     "deploys).")
        return "\n".join(lines)

    # -- footer ------------------------------------------------------------------

    def _governance_footer(self, *, findings, confidence, sources, trust_zone) -> str:
        codes = {f.code for f in findings}
        lines = ["", "── Governance Outcome ──"]
        zone_label = ("verified (deterministic — grounded by construction)"
                      if trust_zone == "verified"
                      else "exploration (LLM — non-deterministic)")
        lines.append(f"  Trust zone: {zone_label}")
        lines.append("  Tenets checked:")
        for tenet, prefix, desc in _CHECKED_TENETS:
            flagged = any(c.startswith(prefix + "_") for c in codes)
            lines.append(f"    {'⚠ FLAG' if flagged else '✓ PASS'}  {tenet} — {desc}")
        if trust_zone == "verified":
            conf = ("grounded by construction (deterministic; no score needed)"
                    if confidence is None
                    else f"grounded by construction; heuristic overlap {confidence:.0%} (context only)")
        else:
            conf = ("not measured" if confidence is None
                    else f"{confidence:.0%} "
                         f"({'≥' if confidence >= CONFIDENCE_FACT_THRESHOLD else '<'} "
                         f"{CONFIDENCE_FACT_THRESHOLD:.0%} fact bar)")
        lines.append(f"  Confidence: {conf}")
        if sources:
            shown = ", ".join(sources[:6])
            more = f" (+{len(sources) - 6} more)" if len(sources) > 6 else ""
            lines.append(f"  Sources ({len(sources)}): {shown}{more}")
        else:
            lines.append("  Sources: none matched (treat with extra caution)")
        lines.append("  Gate status: ✓ PASS")
        lines.append("────────────────────────")
        return "\n".join(lines)

    # -- detectors ---------------------------------------------------------------

    def _detect_tenet1(self, response: str) -> List[Finding]:
        out: List[Finding] = []
        for pat in _T1_DONE_PATTERNS:
            m = re.search(pat, response)
            if m:
                out.append(Finding("Tenet 1", "T1_DEPLOY_CLAIM", Severity.BLOCK,
                    "Output describes a deployment/mutation as already performed; agents "
                    "produce artifacts, never execute (human-owned deployment).",
                    _truncate(m.group(0))))
                break
        for pat in _T1_DEPLOY_COMMANDS:
            m = re.search(pat, response)
            if m:
                out.append(Finding("Tenet 1", "T1_DEPLOY_COMMAND", Severity.INFO,
                    "Output references a deploy/mutate command; acceptable only as a reviewable "
                    "instruction for a human to run, not as an executed action.",
                    _truncate(m.group(0))))
                break
        return out

    def _detect_tenet3(self, response: str) -> List[Finding]:
        for pat in _T3_SOR_WRITE_PATTERNS:
            m = re.search(pat, response)
            if m:
                return [Finding("Tenet 3", "T3_SOR_WRITE", Severity.BLOCK,
                    "Output claims a write to a system of record; those are read-only. Writes "
                    "go only to human-reviewed collaboration surfaces.", _truncate(m.group(0)))]
        return []

    def _detect_tenet6(self, response: str) -> List[Finding]:
        for pat, label in _T6_SECRET_PATTERNS:
            if re.search(pat, response):
                return [Finding("Tenet 6", "T6_SECRET_LEAK", Severity.BLOCK,
                    f"Output appears to contain secret material ({label}); credentials/PII must "
                    "never appear in responses.", "[redacted]")]
        return []

    def _detect_tenet4(self, confidence, trust_zone) -> List[Finding]:
        if trust_zone != "exploration":
            return []
        if confidence is None or confidence < CONFIDENCE_FACT_THRESHOLD:
            shown = "not measured" if confidence is None else f"{confidence:.0%}"
            return [Finding("Tenet 4", "T4_UNGROUNDED", Severity.BLOCK,
                f"LLM-generated answer does not meet the grounding bar (confidence {shown} < "
                f"{CONFIDENCE_FACT_THRESHOLD:.0%}); no-hallucination-tolerance — human "
                "verification required.")]
        return []

    def _detect_evidence(self, evidence: Optional[EvidencePacket],
                         agent: str, request: str) -> List[Finding]:
        """Evidence-provenance detector. No-op when no packet is supplied; this is exactly
        how "no evidence provider configured" preserves existing behavior.

        Independent of confidence/trust_zone: a high confidence score cannot substitute for
        a missing, tampered, non-READY, request-mismatched, or route-mismatched evidence
        packet, and this runs in the verified zone too. This closes the deterministic-
        grounding-skip gap: a deterministic answer can no longer pass unproven when a
        provider is configured."""
        if evidence is None:
            return []
        reason = packet_validity_reason(evidence, request, agent)
        if not reason:
            return []
        if reason == "invalid_digest":
            return [Finding("Evidence", "EV_INVALID_DIGEST", Severity.BLOCK,
                "Evidence packet digest does not match its contents; the packet is missing, "
                "tampered, or was altered after it was built.")]
        if reason == "request_mismatch":
            return [Finding("Evidence", "EV_REQUEST_MISMATCH", Severity.BLOCK,
                "Evidence packet was built for a different request; it cannot authorize this one.")]
        if reason == "route_mismatch":
            return [Finding("Evidence", "EV_ROUTE_MISMATCH", Severity.BLOCK,
                f"Evidence packet route does not authorize dispatching '{agent}'.")]
        status = reason.split(":", 1)[1]
        return [Finding("Evidence", f"EV_NOT_READY_{status}", Severity.BLOCK,
            f"Evidence packet status is {status}, not READY; dependent work must stop "
            "until it is resolved.")]

    @staticmethod
    def _t11_is_directive(response: str, match: "re.Match") -> bool:
        """Directive (BLOCK) vs descriptive assessment (INFO); fail-safe toward INFO."""
        start = max(0, match.start() - 160)
        end = min(len(response), match.end() + 160)
        window = response[start:end]
        ls = response.rfind("\n", 0, match.start()) + 1
        le = response.find("\n", match.end())
        line = response[ls: le if le != -1 else len(response)]
        for pat in _T11_DESCRIPTIVE_MARKERS:
            if re.search(pat, window):
                return False
        for pat in _T11_DIRECTIVE_MARKERS:
            if re.search(pat, line) or re.search(pat, window):
                return True
        return False

    def _detect_tenet11(self, response: str) -> List[Finding]:
        out: List[Finding] = []
        for pat in _T11_FETCH_EXECUTE_PATTERNS:
            m = re.search(pat, response)
            if m:
                if self._t11_is_directive(response, m):
                    out.append(Finding("Tenet 11", "T11_FETCH_EXECUTE", Severity.BLOCK,
                        "Output instructs a fetch-and-execute of remote content; agents never run "
                        "remote payloads. Remote executable content is a reviewable artifact for a "
                        "human (bounded egress).", _truncate(m.group(0))))
                else:
                    out.append(Finding("Tenet 11", "T11_FETCH_EXECUTE_DESC", Severity.INFO,
                        "Output describes infrastructure that fetches-and-runs remote content (not "
                        "an agent directive); surfaced for review.", _truncate(m.group(0))))
                break
        for pat, why in _T11_EGRESS_CLAIM_PATTERNS:
            m = re.search(pat, response)
            if m:
                out.append(Finding("Tenet 11", "T11_EGRESS", Severity.INFO,
                    f"Output {why}; egress is allowlist-only via the platform egress path "
                    "(observability signal — verify against the egress allowlist).",
                    _truncate(m.group(0))))
                break
        return out

    def _detect_tenet12(self, response: str) -> List[Finding]:
        for pat, why in _T12_RUNAWAY_PATTERNS:
            m = re.search(pat, response)
            if m:
                return [Finding("Tenet 12", "T12_RUNAWAY", Severity.INFO,
                    f"Output {why}; tasks run under hard ceilings and stop-and-report on breach "
                    "(observability signal — bounded resource).", _truncate(m.group(0)))]
        return []


# --- Shared seam: one process-wide gate; enforcement is unconditional ----------

_shared_gate: Optional["GovernanceGate"] = None


def get_gate() -> "GovernanceGate":
    global _shared_gate
    if _shared_gate is None:
        _shared_gate = GovernanceGate()
    return _shared_gate


def reset_gate() -> None:
    global _shared_gate
    _shared_gate = None


def gate_output(response: str, *, agent: str, request: str = "", environment: str = "dev",
                agent_obj: object = None, used_llm: Optional[bool] = None,
                confidence: Optional[float] = None,
                sources: Optional[List[str]] = None, footer: bool = True,
                evidence: Optional[EvidencePacket] = None) -> str:
    """The ONE function every response path calls, so 'everything is gated' is literally true.

    `evidence` is an optional EvidencePacket from the evidence gateway; it defaults to None
    (a no-op that preserves existing behavior). GUARDRAIL: the packet is passed to the gate's
    evidence detector ONLY — it is never fed into confidence scoring (that would change how
    Tenet 4 is measured). Evidence can only TIGHTEN the gate, never lift an answer over the bar."""
    if agent_obj is not None:
        if used_llm is None:
            used_llm = bool(getattr(agent_obj, "llm_available", False))
        if confidence is None:
            detail = getattr(agent_obj, "assess_confidence_detail", None)
            if callable(detail):
                try:
                    confidence, measured = detail(response, request)
                    if sources is None:
                        sources = measured
                except Exception:
                    confidence = None
    return get_gate().enforce(
        response, agent=agent, request=request, environment=environment,
        confidence=confidence, used_llm=used_llm, sources=sources, footer=footer,
        evidence=evidence,
    ).response
