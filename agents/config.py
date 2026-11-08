"""Kit configuration loading and the tighten-only grounding threshold.

`load_config()` reads `config.yaml` (gitignored) and falls back to `config.yaml.example`.

`resolve_fact_threshold()` turns `grounding.fact_threshold` into the bar an exploration-zone
(LLM) answer must clear (Tenet 4). The value is configurable UPWARD only: the 0.95 floor is the
constitution's minimum, so a lower value is clamped to the floor, and an invalid value falls
back to it. Configuration can make the gate stricter, never weaker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Floor and default for the present-as-fact threshold (PRINCIPLES.md Tenet 4).
FACT_THRESHOLD_FLOOR = 0.95


def load_config() -> dict:
    """Return the parsed kit config, or {} when none is readable (fail-open to defaults)."""
    try:
        import yaml
        for name in ("config.yaml", "config.yaml.example"):
            cfg = _REPO_ROOT / name
            if cfg.exists():
                return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception as e:  # fail open to the coded defaults
        logger.debug("config not loaded (%s)", type(e).__name__)
    return {}


def resolve_fact_threshold(cfg: Optional[dict]) -> float:
    """Resolve `grounding.fact_threshold` (tighten-only).

    - missing key or section       -> 0.95
    - a number in [0.95, 1.0]      -> used as is
    - a number below 0.95          -> clamped to 0.95 (WARNING: tighten-only)
    - a non-number, a bool, or > 1 -> 0.95 (WARNING: invalid)
    """
    grounding: Any = (cfg or {}).get("grounding") if isinstance(cfg, dict) else None
    if not isinstance(grounding, dict) or grounding.get("fact_threshold") is None:
        return FACT_THRESHOLD_FLOOR
    value = grounding.get("fact_threshold")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.warning("grounding.fact_threshold=%r is not a number; using %.2f.",
                       value, FACT_THRESHOLD_FLOOR)
        return FACT_THRESHOLD_FLOOR
    value = float(value)
    if value > 1.0:
        logger.warning("grounding.fact_threshold=%s is above 1.0; using %.2f.",
                       value, FACT_THRESHOLD_FLOOR)
        return FACT_THRESHOLD_FLOOR
    if value < FACT_THRESHOLD_FLOOR:
        logger.warning("grounding.fact_threshold=%s is below the %.2f floor; the threshold is "
                       "tighten-only, so %.2f is used.", value, FACT_THRESHOLD_FLOOR,
                       FACT_THRESHOLD_FLOOR)
        return FACT_THRESHOLD_FLOOR
    return value
