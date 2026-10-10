"""Action-level audit trail — the per-task record of what the companion actually did.

PRINCIPLES.yaml names two gaps this closes:
  * OB-001 (Insufficient Action Audit Trail) — log tools/agents invoked, trust zone, confidence,
    sources, gate decisions, and findings, in enough detail to reconstruct an incident and
    demonstrate containment on audit. This is the ENF-006 control.
  * HI-003 (Action/Output Divergence) — the gate scans output TEXT; this trail records the
    ACTIONS and outcomes around each gated response, so "what the agent said" and "what the
    system recorded it doing" can be compared. The trail is an observability record, NOT a
    second enforcement path — it never blocks (enforcement stays in the GovernanceGate).

Design constraints (match the rest of the kit):
  * Deterministic, stdlib-only — no new dependency, no LLM, works with no AWS.
  * Append-only JSONL at a gitignored path (default `.audit/trail.jsonl`); override with
    GAC_AUDIT_PATH. One JSON object per line, so the file is greppable and streamable.
  * FAIL-OPEN: auditing must never crash or block a response. Any error is swallowed (the audit
    is a record, not a gate). The gate remains the only thing that can block.
  * No secrets: the recorder stores lengths/hashes and gate findings, never raw response text
    and never secret-shaped material. A short, redaction-safe digest is kept for correlation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_AUDIT_PATH = _REPO_ROOT / ".audit" / "trail.jsonl"
_LOCK = threading.Lock()

# Patterns that must never be written to the trail verbatim, even in a digest preview.
_SECRET_HINTS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(?:password|passwd|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]

#: audit schema version — bump if the event shape changes in a breaking way.
AUDIT_SCHEMA = "1"


def _audit_path() -> Path:
    override = os.environ.get("GAC_AUDIT_PATH")
    return Path(override) if override else _DEFAULT_AUDIT_PATH


def _safe_text_metadata(text: str) -> Dict[str, Any]:
    """Record length + a short content hash for correlation — never the raw text, never a
    secret. The hash lets two events be correlated (same output) without storing the output."""
    t = text or ""
    redacted = t
    for pat in _SECRET_HINTS:
        redacted = pat.sub("[redacted]", redacted)
    digest = hashlib.sha256(redacted.encode("utf-8", "replace")).hexdigest()[:16]
    return {"len": len(t), "sha256_16": digest}


class AuditTrail:
    """Append-only JSONL recorder. One instance is shared process-wide (see get_audit)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or _audit_path()
        self.enabled = os.environ.get("GAC_AUDIT_DISABLED", "").lower() not in ("1", "true", "yes")

    def record(self, event_type: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """Append one event. Returns the event dict (for tests), or None on any failure.

        FAIL-OPEN: this never raises — an audit problem must not break a response path."""
        if not self.enabled:
            return None
        event = {
            "schema": AUDIT_SCHEMA,
            "ts": round(time.time(), 3),
            "event": event_type,
            **fields,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, default=str, ensure_ascii=False)
            with _LOCK:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            return event
        except Exception as exc:  # fail-open: record nothing rather than disrupt the user
            logger.debug("audit record failed (fail-open): %s", exc)
            return None

    def record_gate(self, *, agent: str, request: str, response: str, trust_zone: str,
                    confidence: Optional[float], sources: Optional[List[str]],
                    blocked: bool, findings: Optional[List[Dict[str, Any]]] = None,
                    environment: str = "dev", stage: Optional[str] = None,
                    front_door: str = "engine") -> Optional[Dict[str, Any]]:
        """Record a gate decision — the core action event. Stores metadata only (no raw text)."""
        return self.record(
            "gate_decision",
            front_door=front_door,
            agent=agent,
            environment=environment,
            stage=stage,
            trust_zone=trust_zone,
            confidence=None if confidence is None else round(float(confidence), 3),
            sources=list(sources or [])[:12],
            blocked=bool(blocked),
            findings=findings or [],
            request=_safe_text_metadata(request),
            response=_safe_text_metadata(response),
        )

    def tail(self, n: int = 20) -> List[Dict[str, Any]]:
        """Return the last n events (for `./run.sh audit` and tests). Fail-open to []."""
        try:
            if not self.path.is_file():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
            out = []
            for ln in lines[-n:]:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
            return out
        except Exception:
            return []


_shared_audit: Optional[AuditTrail] = None


def get_audit() -> AuditTrail:
    global _shared_audit
    if _shared_audit is None:
        _shared_audit = AuditTrail()
    return _shared_audit


def reset_audit() -> None:
    global _shared_audit
    _shared_audit = None
