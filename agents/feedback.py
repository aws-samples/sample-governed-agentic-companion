"""Feedback & evolution loop — how the companion learns from its own work, safely.

This is the connective tissue of the v2 uplift: EVERY piece of work the companion does emits a
structured feedback record. The record captures what was asked, what kind of answer was produced,
the gate outcome, and — critically — the GROUNDING GAP (did the answer clear the fact bar? which
knowledge keys grounded it? none?). A gap is a candidate learning: a place the knowledge base
could grow so the next builder gets a grounded answer instead of a decline.

The loop is deliberately NON-AUTONOMOUS, per PRINCIPLES.md Tenet 8 (knowledge compounds through a
staged -> human-reviewed -> promoted flow, never silently absorbed) and the self-learning design:

    produce work -> record feedback (candidate) -> HUMAN reviews -> HUMAN promotes -> Tier-B overlay

Nothing here mutates the knowledge base. Promotion emits a Tier-B overlay-shaped candidate that a
human still has to apply (the Tier-B overlay is itself a human-reviewed, out-of-repo artifact —
Tier A stays locked and code-review-only). This keeps the compounding loop inside the governance
model: no fabrication, no self-rewriting, a human owns every KB change.

Design constraints (match the kit): deterministic, stdlib-only, fail-open (feedback must never
break a response path), metadata-safe (no raw secrets stored). Records live at
`.feedback/candidates.jsonl` (gitignored); override with GAC_FEEDBACK_PATH.
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
_DEFAULT_FEEDBACK_PATH = _REPO_ROOT / ".feedback" / "candidates.jsonl"
_LOCK = threading.Lock()

FEEDBACK_SCHEMA = "1"

# Status lifecycle for a candidate learning.
STATUS_CANDIDATE = "candidate"   # emitted automatically from a work item (ungrounded/low-ground)
STATUS_PROMOTED = "promoted"     # a human promoted it to a Tier-B overlay candidate
STATUS_DISMISSED = "dismissed"   # a human reviewed and rejected it

_SECRET_HINTS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(?:password|passwd|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]


def _feedback_path() -> Path:
    override = os.environ.get("GAC_FEEDBACK_PATH")
    return Path(override) if override else _DEFAULT_FEEDBACK_PATH


def _redact(text: str) -> str:
    out = text or ""
    for pat in _SECRET_HINTS:
        out = pat.sub("[redacted]", out)
    return out


def _candidate_id(request: str, agent: str) -> str:
    """Stable id for a request/agent pair, so repeated asks of the same ungrounded question
    collapse to one candidate rather than flooding the backlog."""
    basis = f"{agent}::{(request or '').strip().lower()}"
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:12]


class FeedbackLoop:
    """Append/update staged feedback candidates. One instance shared process-wide."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or _feedback_path()
        self.enabled = os.environ.get("GAC_FEEDBACK_DISABLED", "").lower() not in (
            "1", "true", "yes")

    # -- emit (automatic, from every work item) ----------------------------------

    def record_work(self, *, agent: str, request: str, trust_zone: str,
                    confidence: Optional[float], sources: Optional[List[str]],
                    blocked: bool, fact_bar: float = 0.95) -> Optional[Dict[str, Any]]:
        """Emit a feedback signal for one completed work item. Returns the candidate dict if a
        LEARNING GAP was detected (and staged), else None. Fail-open (never raises).

        A learning gap = the work was not blocked by an absolute boundary, but it did not ground
        well: no sources matched, or confidence is below the fact bar. That is exactly the signal
        that the knowledge base is thin here — a candidate for human-reviewed growth. A clean,
        well-grounded answer produces NO candidate (nothing to learn); the audit trail already
        records that it happened."""
        if not self.enabled:
            return None
        try:
            grounded = bool(sources) and (confidence is None or confidence >= fact_bar)
            if blocked or grounded:
                # Blocked = a governance decision (not a KB gap). Grounded = nothing to learn.
                return None
            cid = _candidate_id(request, agent)
            candidate = {
                "schema": FEEDBACK_SCHEMA,
                "id": cid,
                "status": STATUS_CANDIDATE,
                "agent": agent,
                "request": _redact((request or "").strip())[:500],
                "gap": ("no knowledge grounded this request"
                        if not sources else
                        f"grounding below the fact bar (confidence "
                        f"{confidence if confidence is None else round(confidence,3)} < {fact_bar})"),
                "trust_zone": trust_zone,
                "confidence": None if confidence is None else round(float(confidence), 3),
                "matched_sources": list(sources or [])[:12],
                "first_seen": round(time.time(), 3),
                "last_seen": round(time.time(), 3),
                "seen_count": 1,
                "promotion": None,   # filled in on human promotion
            }
            self._upsert(candidate)
            return candidate
        except Exception as exc:  # fail-open
            logger.debug("feedback record failed (fail-open): %s", exc)
            return None

    # -- storage -----------------------------------------------------------------

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        out = []
        for ln in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    def _write_all(self, records: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def _upsert(self, candidate: Dict[str, Any]) -> None:
        """Insert a new candidate, or bump the seen_count/last_seen of an existing one."""
        with _LOCK:
            records = self._read_all()
            for r in records:
                if r.get("id") == candidate["id"] and r.get("status") == STATUS_CANDIDATE:
                    r["seen_count"] = int(r.get("seen_count", 1)) + 1
                    r["last_seen"] = candidate["last_seen"]
                    self._write_all(records)
                    return
            records.append(candidate)
            self._write_all(records)

    # -- review (human-driven) ---------------------------------------------------

    def list_candidates(self, status: str = STATUS_CANDIDATE) -> List[Dict[str, Any]]:
        return [r for r in self._read_all() if r.get("status") == status]

    def promote(self, candidate_id: str, *, knowledge_key: str, summary: str,
                layer: str = "team") -> Dict[str, Any]:
        """HUMAN action: mark a candidate promoted and attach a Tier-B overlay-shaped payload
        tagged with a memory layer (team | project — see memory_layers.py).

        This does NOT write the knowledge base. It records the human's decision and the
        overlay-shaped entry (`{knowledge_key: {summary, _layer}}`) that a human then applies to
        the Tier-B overlay through the normal reviewed promotion (see kb_overlay.py). Tier A
        stays locked. Returns {"ok": bool, "message": str}.

        The promotion flow is candidate -> team -> project: a first promotion typically lands at
        `team` (an affirmed practice); a later re-promotion to `project` raises its precedence.

        Guardrails: the knowledge_key MUST be a Tier-B key (`tier_b/...`) — a promotion can never
        target locked Tier-A knowledge (mirrors the overlay allowlist); and the layer must be
        `team` or `project` (`org` is the shipped default and is not a promotion target)."""
        if not knowledge_key.startswith("tier_b/"):
            return {"ok": False,
                    "message": f"refused: promotions target Tier-B only (got '{knowledge_key}'). "
                               "Tier-A knowledge is locked and code-review-only."}
        if layer not in ("team", "project"):
            return {"ok": False,
                    "message": f"refused: layer must be 'team' or 'project' (got '{layer}'). "
                               "'org' is the shipped default, not a promotion target."}
        with _LOCK:
            records = self._read_all()
            for r in records:
                if r.get("id") == candidate_id and r.get("status") == STATUS_CANDIDATE:
                    r["status"] = STATUS_PROMOTED
                    r["promotion"] = {
                        "knowledge_key": knowledge_key,
                        "layer": layer,
                        "overlay_entry": {knowledge_key: {"summary": _redact(summary),
                                                          "_layer": layer}},
                        "promoted_at": round(time.time(), 3),
                        "note": "Human-reviewed. Apply via the Tier-B overlay promotion "
                                "(kb_overlay.py); a human still owns the KB change (Tenet 8).",
                    }
                    self._write_all(records)
                    return {"ok": True,
                            "message": f"promoted {candidate_id} -> {knowledge_key} ({layer})"}
            return {"ok": False, "message": f"no open candidate with id {candidate_id}"}

    def dismiss(self, candidate_id: str, *, reason: str = "") -> Dict[str, Any]:
        """HUMAN action: reject a candidate (not a real gap / won't grow the KB for it)."""
        with _LOCK:
            records = self._read_all()
            for r in records:
                if r.get("id") == candidate_id and r.get("status") == STATUS_CANDIDATE:
                    r["status"] = STATUS_DISMISSED
                    r["dismiss_reason"] = _redact(reason)[:300]
                    self._write_all(records)
                    return {"ok": True, "message": f"dismissed {candidate_id}"}
            return {"ok": False, "message": f"no open candidate with id {candidate_id}"}


_shared_feedback: Optional[FeedbackLoop] = None


def get_feedback() -> FeedbackLoop:
    global _shared_feedback
    if _shared_feedback is None:
        _shared_feedback = FeedbackLoop()
    return _shared_feedback


def reset_feedback() -> None:
    global _shared_feedback
    _shared_feedback = None
