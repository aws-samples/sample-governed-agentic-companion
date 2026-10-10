"""Layered memory — org / team / project precedence over the Tier-A / Tier-B knowledge base.

AI-DLC layers its method/rule memory as org (framework defaults) -> team (affirmed practices) ->
project (overrides). GAC adopts the same *layering* over its existing knowledge model without
disturbing it: this is a PRECEDENCE resolver, not a restructuring of knowledge/.

How the two models compose (additive — nothing in the existing KB changes):

    TRUST tier (WHO may change it, and when):
      Tier A  — locked, in-repo, code-review only. NEVER overlay-mutable. The safety floor.
      Tier B  — medium-velocity, overlay-eligible via a human-reviewed promotion (kb_overlay.py).

    MEMORY layer (WHOSE practice it encodes — precedence low -> high):
      org     — framework defaults shipped with the kit (lowest precedence).
      team    — practices a team has affirmed (overrides org on conflict).
      project — project-specific overrides (highest precedence).

Resolution rule: within the SAME knowledge key, a higher memory layer overrides a lower one.
A layer assignment NEVER escalates trust: a project-layer entry that targets a Tier-A key is
refused, exactly like the overlay allowlist — the locked floor cannot be moved by a layer.

The feedback promotion path (Tenet 8) flows: candidate -> team -> project, each step a HUMAN
decision. A candidate promoted at the `team` layer becomes a Tier-B overlay entry tagged
`team`; a later promotion to `project` raises its precedence. Nothing is auto-applied.

This module is deterministic, stdlib-only, and fails open: an unparseable layer map degrades to
"no layering" (the base KB), never to an empty or wrong-precedence KB.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Memory layers in ascending precedence. A higher layer wins on a key conflict.
LAYERS: List[str] = ["org", "team", "project"]
_LAYER_RANK = {name: i for i, name in enumerate(LAYERS)}

# Only Tier-B keys may carry a team/project layer (same floor as the overlay allowlist).
_LAYERABLE_PREFIXES = ("tier_b/",)


def is_layerable(knowledge_key: str) -> bool:
    """True only for keys a team/project layer may override (Tier-B). Tier-A is never layerable."""
    return knowledge_key.startswith(_LAYERABLE_PREFIXES)


def layer_of(entry: dict, default: str = "org") -> str:
    """Read an entry's declared memory layer (`_layer: team|project`), defaulting to org.
    An unknown layer value falls back to org (lowest precedence) — fail safe."""
    layer = (entry or {}).get("_layer", default)
    return layer if layer in _LAYER_RANK else default


def resolve_layers(baseline: Dict[str, dict],
                   layer_entries: Optional[List[dict]] = None) -> Dict[str, dict]:
    """Resolve org/team/project precedence over `baseline` (the already-loaded KB).

    `layer_entries` is an optional list of promoted layer records, each shaped:
        {"knowledge_key": "tier_b/...", "layer": "team"|"project", "data": {...}}
    (This is exactly what a promoted feedback candidate carries; see feedback.py.) With no
    layer entries, the baseline is returned unchanged — a true no-op, so existing behavior and
    tests are preserved byte-for-byte.

    Precedence: for each knowledge key, the entry from the HIGHEST memory layer wins. A layer
    entry that targets a non-layerable (Tier-A) key is IGNORED (the locked floor cannot move).
    Fail-open: any malformed entry is skipped, never raised."""
    if not layer_entries:
        return baseline

    merged: Dict[str, dict] = dict(baseline)
    # Track the winning layer rank per key so a lower layer can't override a higher one.
    winning_rank: Dict[str, int] = {}
    # Seed from any _layer tags already present on baseline entries (default org).
    for key, data in baseline.items():
        if isinstance(data, dict):
            winning_rank[key] = _LAYER_RANK.get(layer_of(data), 0)

    applied = ignored = 0
    for entry in layer_entries:
        try:
            key = entry["knowledge_key"]
            layer = entry.get("layer", "team")
            data = entry.get("data")
            if not isinstance(data, dict) or layer not in _LAYER_RANK:
                ignored += 1
                continue
            if not is_layerable(key):
                ignored += 1  # Tier-A target — refuse, like the overlay allowlist
                continue
            rank = _LAYER_RANK[layer]
            if rank >= winning_rank.get(key, -1):
                tagged = dict(data)
                tagged["_layer"] = layer
                merged[key] = tagged
                winning_rank[key] = rank
                applied += 1
            else:
                ignored += 1  # a lower layer cannot override a higher one already applied
        except Exception:
            ignored += 1
            continue
    logger.info("memory layers resolved: %d applied, %d ignored", applied, ignored)
    return merged
