"""Sensors — deterministic, inspectable verification manifests (advisory).

AI-DLC ships "sensors" as deterministic verification MANIFESTS rather than in-code checks:
required-sections, claim-sources, traceability, etc. GAC adopts the same framing — a sensor is
a small, declarative, inspectable rule plus a deterministic checker — as an ADVISORY layer that
sits beside (never replaces) the always-on GovernanceGate.

The distinction, kept honest (Tenet 4/5):
  * The GovernanceGate ENFORCES (blocks). It is the mechanical floor.
  * A sensor VERIFIES and REPORTS (pass/warn). It is advisory observability — it never blocks,
    exactly like PRINCIPLES.yaml marks the egress/resource text-layer detectors as advisory. A
    sensor result is a signal for a human, not a gate.

Each sensor is defined by a manifest (name + what it checks + why) so the set is inspectable
(`./run.sh verify --list`) rather than buried in code. Deterministic, stdlib-only.

Shipped sensors (advisory):
  * claim-sources     — a factual answer should cite at least one grounding source.
  * required-sections — a staged-plan artifact should carry its expected structure.
  * traceability      — a staged step's artifact should reference the intent it derives from.
  * no-absolute-claim — flags unqualified absolutes ("guarantees", "100%", "always") for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass
class SensorResult:
    sensor: str
    passed: bool
    detail: str


@dataclass
class Sensor:
    name: str
    checks: str          # what it verifies
    why: str             # why it matters
    fn: Callable[[str, dict], SensorResult]


# -- individual deterministic checkers -----------------------------------------

def _claim_sources(text: str, ctx: dict) -> SensorResult:
    sources = ctx.get("sources") or []
    # A decline/block is exempt (it makes no factual claim to source).
    if text.lstrip().startswith(("⛔", "[")) and "can't ground" in text:
        return SensorResult("claim-sources", True, "no factual claim (decline) — exempt")
    ok = bool(sources)
    return SensorResult("claim-sources", ok,
                        f"{len(sources)} source(s)" if ok else
                        "no grounding source cited for a factual answer")


def _required_sections(text: str, ctx: dict) -> SensorResult:
    required = ctx.get("required_sections")
    if not required:
        return SensorResult("required-sections", True, "no required-sections manifest for this artifact")
    missing = [s for s in required if s.lower() not in text.lower()]
    return SensorResult("required-sections", not missing,
                        "all present" if not missing else f"missing: {', '.join(missing)}")


def _traceability(text: str, ctx: dict) -> SensorResult:
    intent = ctx.get("intent")
    if not intent:
        return SensorResult("traceability", True, "no intent to trace (not a staged step)")
    # The artifact (or its request) should reference the intent it derives from.
    terms = [t for t in re.findall(r"[a-z0-9]{4,}", intent.lower())][:6]
    hit = sum(1 for t in terms if t in text.lower())
    ok = hit >= max(1, len(terms) // 3)
    return SensorResult("traceability", ok,
                        f"traces intent ({hit}/{len(terms)} terms)" if ok else
                        "artifact does not reference its originating intent")


_ABSOLUTE_MARKERS = re.compile(
    r"(?i)\b(?:guarantee[sd]?|100%|always works|never fails|fully secure|bullet[- ]?proof|"
    r"tamper[- ]?proof|zero (?:risk|downtime|trust))\b")


def _no_absolute_claim(text: str, ctx: dict) -> SensorResult:
    m = _ABSOLUTE_MARKERS.search(text)
    return SensorResult("no-absolute-claim", not m,
                        "no unqualified absolutes" if not m else
                        f"unqualified absolute to qualify/remove: '{m.group(0)}'")


# -- the manifest set ----------------------------------------------------------

_SENSORS: List[Sensor] = [
    Sensor("claim-sources",
           "a factual answer cites >=1 grounding source",
           "an unsourced factual claim is a grounding risk (Tenet 4)",
           _claim_sources),
    Sensor("required-sections",
           "an artifact carries its expected structure (when a manifest is supplied)",
           "missing structure is a completeness defect reviewers catch",
           _required_sections),
    Sensor("traceability",
           "a staged step's artifact references the intent it derives from",
           "forward/backward traceability keeps context correct across steps",
           _traceability),
    Sensor("no-absolute-claim",
           "flags unqualified absolutes for review",
           "absolute claims ('guarantees', '100%') are a review finding on public content",
           _no_absolute_claim),
]


def list_sensors() -> List[Dict[str, str]]:
    """Inspectable manifest set (name/checks/why) — the sensors as data, not code."""
    return [{"name": s.name, "checks": s.checks, "why": s.why} for s in _SENSORS]


def run_sensors(text: str, ctx: Optional[dict] = None) -> List[SensorResult]:
    """Run all sensors over `text` with optional context (sources, intent, required_sections).
    ADVISORY — returns pass/warn results; never blocks. Fail-open per sensor."""
    ctx = ctx or {}
    out: List[SensorResult] = []
    for s in _SENSORS:
        try:
            out.append(s.fn(text, ctx))
        except Exception as exc:
            out.append(SensorResult(s.name, True, f"sensor error (skipped): {exc}"))
    return out
