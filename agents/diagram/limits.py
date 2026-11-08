"""Bounds for the diagram specialist (Tenet 12). Ceilings are constants; config may only lower
them (tighten-only), the same pattern as agents/config.resolve_fact_threshold."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# -- coded ceilings -----------------------------------------------------------------------------
MAX_NODES = 24
MAX_EDGES = 40
MAX_GROUPS = 12
MAX_DEPTH = 5
MAX_LABEL = 48
MAX_TITLE = 48
MAX_REQUEST_CHARS = 2000
MAX_FACTS = 500
MAX_TOPICS = 50
CANVAS_MAX_W = 992
CANVAS_MAX_H = 1400
PNG_SCALE_MAX = 2
QUALITY_PASS_MIN = 85.0
MAX_LAYOUT_ATTEMPTS = 3
RENDER_TIMEOUT_MAX = 20
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = {"drawio": 2 * 1024 * 1024, "svg": 1536 * 1024, "png": 1024 * 1024,
                      "report": 256 * 1024}
ENGINE_TEXT_CAP = 10 * 1024
MAX_FLAGGED_LINES = 10
MAX_QUALITY_LINES = 10
PAYLOAD_FINDINGS_CAP = 30
ICON_MAX_BYTES = 16 * 1024
SOURCE_MAX_BYTES = 160
DEFAULT_MCP_RESULT_CAP = 16 * 1024
EVIDENCE_BASIS_CAP = 1024
UPSTREAM_BODY_CAP = MAX_PAYLOAD_BYTES + 1024 * 1024
# -- natural-language planning and views (design D12) -------------------------------------------
MAX_VIEWS = 4
GENERIC_VIEW_MAX_NODES = 6
GENERIC_VIEW_MAX_EDGES = 8
FLOW_VIEW_MAX_RANKS = 4
MAX_SELECTION_NODES = 96
PLANNER_MAX_TOKENS = 1024
PLANNER_MIN_TOKENS = 256
PLANNER_TIMEOUT_MAX = 15
PLANNER_TIMEOUT_MIN = 3
PLANNER_VIEW_MAX_BYTES = 48 * 1024
PLAN_MAX_IDS = 64
CLOSURE_MAX_ADDED = 8
PATH_MAX_HOPS = 6
MAX_SUGGESTIONS = 5
PLAN_CACHE_ENTRIES = 64
PLANNER_MODES = ("deterministic", "bedrock")


@dataclass(frozen=True)
class PlannerLimits:
    """The resolved `diagram.planner:` block. `mode` is the configured mode only; the per-request
    and env downgrade rules live in plan.resolve_mode."""
    mode: str = "deterministic"
    max_tokens: int = PLANNER_MAX_TOKENS
    timeout_seconds: int = PLANNER_TIMEOUT_MAX
    #: optional strings from the config (the env overrides them; validated by the planner)
    model_id: str = ""
    region: str = ""
    guardrail_id: str = ""
    guardrail_version: str = ""


def _opt_str(p: Dict[str, Any], key: str) -> str:
    v = p.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        logger.warning("diagram.planner.%s is not a string; ignored.", key)
        return ""
    return v.strip()


@dataclass(frozen=True)
class Limits:
    enabled: bool = True
    max_nodes: int = MAX_NODES
    max_edges: int = MAX_EDGES
    max_groups: int = MAX_GROUPS
    png_scale: int = PNG_SCALE_MAX
    quality_pass_score: float = QUALITY_PASS_MIN
    max_layout_attempts: int = MAX_LAYOUT_ATTEMPTS
    render_timeout_seconds: int = RENDER_TIMEOUT_MAX
    max_artifact_bytes: Dict[str, int] = field(default_factory=lambda: dict(MAX_ARTIFACT_BYTES))
    max_payload_bytes: int = MAX_PAYLOAD_BYTES
    max_views: int = MAX_VIEWS
    planner: PlannerLimits = field(default_factory=PlannerLimits)


def resolve_planner(raw: Any) -> PlannerLimits:
    """The `diagram.planner:` sub-block: mode deterministic|bedrock (anything else WARNING and
    deterministic), max_tokens 256..1024 and timeout_seconds 3..15, both tighten-only."""
    p = raw if isinstance(raw, dict) else {}
    mode = p.get("mode", "deterministic")
    if mode not in PLANNER_MODES:
        logger.warning("diagram.planner.mode=%r is not one of %s; using deterministic.", mode,
                       "/".join(PLANNER_MODES))
        mode = "deterministic"
    return PlannerLimits(
        mode=mode,
        max_tokens=_int_le("planner.max_tokens", p.get("max_tokens"), PLANNER_MIN_TOKENS,
                           PLANNER_MAX_TOKENS),
        timeout_seconds=_int_le("planner.timeout_seconds", p.get("timeout_seconds"),
                                PLANNER_TIMEOUT_MIN, PLANNER_TIMEOUT_MAX),
        model_id=_opt_str(p, "model_id"),
        region=_opt_str(p, "region"),
        guardrail_id=_opt_str(p, "guardrail_id"),
        guardrail_version=_opt_str(p, "guardrail_version"),
    )


def _int_le(name: str, value: Any, lo: int, hi: int) -> int:
    """An int in [lo, hi]; above hi clamps down, invalid or below lo uses hi (WARNING)."""
    if value is None:
        return hi
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning("diagram.%s=%r is not an integer; using %d.", name, value, hi)
        return hi
    if value > hi:
        logger.warning("diagram.%s=%s is above the ceiling %d; limits are tighten-only, so %d "
                       "is used.", name, value, hi, hi)
        return hi
    if value < lo:
        logger.warning("diagram.%s=%s is below %d; using %d.", name, value, lo, hi)
        return hi
    return value


def _env_disabled() -> bool:
    return (os.getenv("GAC_DIAGRAM_ENABLED") or "").strip().lower() in ("false", "0", "no", "off")


def resolve_limits(cfg: Optional[dict] = None) -> Limits:
    """Resolve the `diagram:` config block (tighten-only). The env GAC_DIAGRAM_ENABLED=false can
    only disable the specialist; it can never enable one the config disabled."""
    if cfg is None:
        from ..config import load_config
        cfg = load_config()
    d = cfg.get("diagram") if isinstance(cfg, dict) else None
    d = d if isinstance(d, dict) else {}
    enabled = d.get("enabled", True)
    enabled = (enabled is not False) and not _env_disabled()
    score = d.get("quality_pass_score")
    if score is None:
        pass_score = QUALITY_PASS_MIN
    elif isinstance(score, bool) or not isinstance(score, (int, float)) or score > 100:
        logger.warning("diagram.quality_pass_score=%r is invalid; using %.0f.", score,
                       QUALITY_PASS_MIN)
        pass_score = QUALITY_PASS_MIN
    elif score < QUALITY_PASS_MIN:
        logger.warning("diagram.quality_pass_score=%s is below %.0f; tighten-only, so %.0f is "
                       "used.", score, QUALITY_PASS_MIN, QUALITY_PASS_MIN)
        pass_score = QUALITY_PASS_MIN
    else:
        pass_score = float(score)
    arts = dict(MAX_ARTIFACT_BYTES)
    raw_arts = d.get("max_artifact_bytes")
    if isinstance(raw_arts, dict):
        for k, ceiling in MAX_ARTIFACT_BYTES.items():
            if k in raw_arts:
                arts[k] = _int_le(f"max_artifact_bytes.{k}", raw_arts[k], 1, ceiling)
    return Limits(
        enabled=enabled,
        max_nodes=_int_le("max_nodes", d.get("max_nodes"), 2, MAX_NODES),
        max_edges=_int_le("max_edges", d.get("max_edges"), 1, MAX_EDGES),
        max_groups=_int_le("max_groups", d.get("max_groups"), 0, MAX_GROUPS),
        png_scale=_int_le("png_scale", d.get("png_scale"), 1, PNG_SCALE_MAX),
        quality_pass_score=pass_score,
        max_layout_attempts=_int_le("max_layout_attempts", d.get("max_layout_attempts"), 1,
                                    MAX_LAYOUT_ATTEMPTS),
        render_timeout_seconds=_int_le("render_timeout_seconds", d.get("render_timeout_seconds"),
                                       1, RENDER_TIMEOUT_MAX),
        max_artifact_bytes=arts,
        max_payload_bytes=_int_le("max_payload_bytes", d.get("max_payload_bytes"), 65536,
                                  MAX_PAYLOAD_BYTES),
        max_views=_int_le("max_views", d.get("max_views"), 1, MAX_VIEWS),
        planner=resolve_planner(d.get("planner")),
    )
