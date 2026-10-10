"""SME maturity — how far a GAC fork has grown from empty seed toward a productionizable expert.

GAC is a SEED designed to EVOLVE INTO a subject-matter expert (see docs/CORE-INTENT.md). This
module makes that becoming measurable: it reads the data the kit already captures and reports the
instance's current maturity STAGE plus the readiness metrics behind it. See
docs/MATURATION-PATH.md for the model and the design spec this implements.

Design constraints (match the rest of the kit):
  * Deterministic, stdlib-only — no LLM, no new dependency, works with no AWS.
  * READ-ONLY and FAIL-OPEN — it reads `.audit/` and `.feedback/` and the KB; it mutates nothing
    and never raises into a caller. It is observability, exactly like the audit trail — it REPORTS,
    it never gates or blocks.
  * HONEST PROXY — the audit trail has no explicit "in-domain" flag, so grounded answers stand in
    for "in-domain answered" and open feedback candidates stand in for "in-domain declined/thin".
    The report labels this as a proxy; the stage is never presented as more certain than the data.
  * HUMAN-OWNED GRADUATION (Tenet 1/4) — the computation may mark an instance `grounded` (eligible)
    but can NEVER set `productionized` itself; that needs a human ratification marker. Demotion on
    decay is automatic and surfaced, never silent.

The ladder (docs/MATURATION-PATH.md):
    seeded -> cultivating -> grounded -> productionized -> sustaining
                                            (human-ratified)   (watch for decay -> cultivating)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_KNOWLEDGE_DIR = _REPO_ROOT / "knowledge"

# The human ratification marker. Its PRESENCE (written by an explicit human action) is what lets
# an instance be `productionized`; the computation never writes it. Gitignored runtime state.
_DEFAULT_RATIFY_PATH = _REPO_ROOT / ".maturity" / "ratified.json"

# Ladder values (parallel to the Context Spine leaf-status machine, on the expertise axis).
SEEDED = "seeded"
CULTIVATING = "cultivating"
GROUNDED = "grounded"
PRODUCTIONIZED = "productionized"
SUSTAINING = "sustaining"

STAGES = [SEEDED, CULTIVATING, GROUNDED, PRODUCTIONIZED, SUSTAINING]

# Default thresholds. An engagement raises these in config.yaml (`maturity:`); a regulated domain
# sets them higher. Absent config, these apply — no config change is needed for the report to run.
DEFAULTS = {
    "grounding_rate_bar": 0.70,   # grounded / (grounded + gaps) to be eligible for `grounded`
    "decline_rate_ceiling": 0.30, # gaps / (grounded + gaps) must be at or below this
    "coverage_broad": 12,         # populated KB keys for "broad"
    "coverage_growing": 4,        # populated KB keys for "growing" (below -> "sparse")
    "min_answers_to_leave_seed": 3,  # grounded answers before an instance is past `seeded`
}


def _ratify_path() -> Path:
    override = os.environ.get("GAC_MATURITY_RATIFY_PATH")
    return Path(override) if override else _DEFAULT_RATIFY_PATH


@dataclass
class MaturityReport:
    stage: str
    eligible_for: Optional[str]        # the stage the metrics make it eligible for, if gated on a human
    grounding_rate: Optional[float]
    decline_rate: Optional[float]
    grounded_answers: int
    open_gaps: int
    coverage_keys: int
    coverage_band: str                 # sparse | growing | broad
    promotion_health: Optional[float]
    unreviewed_backlog: int
    governance_clean: bool
    ratified: bool
    proxy_note: str
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _count_coverage_keys() -> int:
    """Count populated knowledge keys (Tier-A + Tier-B), excluding the governance PRINCIPLES file.
    Coverage = breadth of the expert's field, not depth in one corner."""
    try:
        n = 0
        for p in _KNOWLEDGE_DIR.rglob("*.yaml"):
            # PRINCIPLES.yaml is the threat model / governance config, not domain expertise.
            if p.name == "PRINCIPLES.yaml":
                continue
            try:
                if p.stat().st_size > 0:
                    n += 1
            except OSError:
                continue
        return n
    except Exception:
        return 0


def _coverage_band(keys: int, cfg: Dict[str, Any]) -> str:
    if keys >= cfg["coverage_broad"]:
        return "broad"
    if keys >= cfg["coverage_growing"]:
        return "growing"
    return "sparse"


def _is_ratified() -> bool:
    try:
        p = _ratify_path()
        if not p.is_file():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("ratified"))
    except Exception:
        return False


def _load_cfg() -> Dict[str, Any]:
    """Read optional `maturity:` thresholds from config.yaml; fall back to DEFAULTS. Stdlib-only:
    we parse the small block without a YAML dependency, and fail-open to defaults."""
    cfg = dict(DEFAULTS)
    try:
        import yaml  # type: ignore
        cfgfile = _REPO_ROOT / "config.yaml"
        if cfgfile.is_file():
            loaded = yaml.safe_load(cfgfile.read_text(encoding="utf-8")) or {}
            block = loaded.get("maturity") or {}
            for k in cfg:
                if k in block and isinstance(block[k], (int, float)):
                    cfg[k] = block[k]
    except Exception:
        # No yaml, no config, or malformed block -> defaults. Report still works.
        pass
    return cfg


def compute_maturity(audit_events: Optional[List[Dict[str, Any]]] = None,
                     open_candidates: Optional[List[Dict[str, Any]]] = None,
                     promoted_candidates: Optional[List[Dict[str, Any]]] = None,
                     coverage_keys: Optional[int] = None,
                     cfg: Optional[Dict[str, Any]] = None) -> MaturityReport:
    """Pure, deterministic maturity computation. All inputs are injectable for testing; when
    omitted they are read from the live stores (fail-open to empty)."""
    cfg = cfg or _load_cfg()

    # --- gather inputs (read-only, fail-open) ---------------------------------
    if audit_events is None:
        try:
            from agents.audit import get_audit
            audit_events = get_audit().tail(10_000)
        except Exception:
            audit_events = []
    if open_candidates is None:
        try:
            from agents.feedback import get_feedback
            open_candidates = get_feedback().list_candidates("candidate")
        except Exception:
            open_candidates = []
    if promoted_candidates is None:
        try:
            from agents.feedback import get_feedback
            promoted_candidates = get_feedback().list_candidates("promoted")
        except Exception:
            promoted_candidates = []
    if coverage_keys is None:
        coverage_keys = _count_coverage_keys()

    gate_events = [e for e in (audit_events or []) if e.get("event") == "gate_decision"]

    # grounded answer = produced, not blocked, with at least one grounding source.
    grounded = sum(1 for e in gate_events
                   if not e.get("blocked") and (e.get("sources") or []))
    # governance blocks attributable to an absolute boundary (Tenet 1/3/6) = misuse signal.
    abs_blocks = 0
    for e in gate_events:
        if e.get("blocked"):
            fs = e.get("findings") or []
            if any("Tenet 1" in str(f) or "Tenet 3" in str(f) or "Tenet 6" in str(f)
                   for f in fs) or not fs:
                abs_blocks += 1

    open_gaps = len(open_candidates or [])
    promoted = len(promoted_candidates or [])

    denom = grounded + open_gaps
    grounding_rate = (grounded / denom) if denom else None
    decline_rate = (open_gaps / denom) if denom else None
    promotion_health = (promoted / (promoted + open_gaps)) if (promoted + open_gaps) else None

    band = _coverage_band(coverage_keys, cfg)
    governance_clean = abs_blocks == 0
    ratified = _is_ratified()

    notes: List[str] = []

    # --- stage inference (deterministic) --------------------------------------
    meets_ground_bar = (grounding_rate is not None
                        and grounding_rate >= cfg["grounding_rate_bar"]
                        and decline_rate is not None
                        and decline_rate <= cfg["decline_rate_ceiling"]
                        and band == "broad"
                        and governance_clean)

    learning_active = (open_gaps > 0 or promoted > 0)

    eligible_for: Optional[str] = None

    if grounded < cfg["min_answers_to_leave_seed"] and band == "sparse":
        stage = SEEDED
        notes.append("Few grounded answers and sparse coverage — correct birth state for a fresh "
                     "fork. Drop in domain specialists + knowledge and turn the learning loop on.")
    elif meets_ground_bar and ratified:
        stage = SUSTAINING
        notes.append("Ratified and still meeting the bar — in production, keep watching for decay.")
    elif meets_ground_bar and not ratified:
        stage = GROUNDED
        eligible_for = PRODUCTIONIZED
        notes.append("Metrics clear the readiness bar. ELIGIBLE for productionized — a HUMAN must "
                     "ratify (the machine never self-promotes to production; Tenet 1/4).")
    elif ratified and not meets_ground_bar:
        # Was productionized, metrics decayed -> honest demotion, surfaced.
        stage = CULTIVATING
        notes.append("DECAY: previously ratified but metrics fell below the bar — demoted to "
                     "cultivating (surfaced, never silent). Re-grow the KB, then re-ratify.")
    elif learning_active:
        stage = CULTIVATING
        notes.append("Learning loop active and still below the readiness bar — this is the normal "
                     "growth stage and it cycles through many promotion rounds. Healthy.")
    else:
        stage = SEEDED
        notes.append("No learning activity yet — the growth loop looks idle. For a kit whose "
                     "purpose is to evolve, confirm the feedback loop is enabled.")

    proxy = ("PROXY: the audit trail has no in-domain flag, so grounded answers approximate "
             "'in-domain answered' and open feedback candidates approximate 'in-domain "
             "declined/thin'. Treat the stage as a well-grounded estimate, not a measured fact.")

    return MaturityReport(
        stage=stage,
        eligible_for=eligible_for,
        grounding_rate=None if grounding_rate is None else round(grounding_rate, 3),
        decline_rate=None if decline_rate is None else round(decline_rate, 3),
        grounded_answers=grounded,
        open_gaps=open_gaps,
        coverage_keys=coverage_keys,
        coverage_band=band,
        promotion_health=None if promotion_health is None else round(promotion_health, 3),
        unreviewed_backlog=open_gaps,
        governance_clean=governance_clean,
        ratified=ratified,
        proxy_note=proxy,
        notes=notes,
    )


def render_report(r: MaturityReport) -> str:
    """Human-readable report for `./run.sh verify --maturity`. Advisory — reports, never blocks."""
    def pct(x: Optional[float]) -> str:
        return "n/a" if x is None else f"{x * 100:.0f}%"

    lines = []
    lines.append("── SME Maturity ──")
    lines.append(f"  Stage: {r.stage}" + (f"  (eligible for: {r.eligible_for} — human ratifies)"
                                          if r.eligible_for else ""))
    lines.append(f"  Grounding rate: {pct(r.grounding_rate)}   "
                 f"Decline rate: {pct(r.decline_rate)}")
    lines.append(f"  Grounded answers: {r.grounded_answers}   Open gaps: {r.open_gaps}")
    lines.append(f"  Coverage: {r.coverage_keys} KB key(s) ({r.coverage_band})")
    lines.append(f"  Promotion health: {pct(r.promotion_health)}   "
                 f"Unreviewed backlog: {r.unreviewed_backlog}")
    lines.append(f"  Governance clean: {'yes' if r.governance_clean else 'NO — review blocks'}")
    lines.append(f"  Human-ratified: {'yes' if r.ratified else 'no'}")
    for n in r.notes:
        lines.append(f"  • {n}")
    lines.append(f"  {r.proxy_note}")
    lines.append("  (Advisory — this is observability over existing data; it never gates.)")
    lines.append("──────────────────")
    return "\n".join(lines)
