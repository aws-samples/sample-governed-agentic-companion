"""The plan both planners produce (design D2.3, D2.4, D2.1). A plan only SELECTS catalogue ids:
`validate_plan` is the boundary between untrusted planner output and the scene. An id that is not
in the catalogue is dropped and counted, never echoed (Tenet 4)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import labels as L
from .limits import MAX_TITLE, PLAN_MAX_IDS, PLANNER_MODES
from .vocabulary import SCOPES, stem, tokens

logger = logging.getLogger(__name__)

EXCLUDE_REASONS = ("not-relevant", "out-of-scope", "opt-in-not-requested", "over-cap")
#: the deterministic planner's internal zero-anchor flow scope (D3.3 step 5); never accepted
#: from a model plan
FLOW_SCOPE = "flow"
MAX_FOCUS = 6
QID_RE = re.compile(r"^[nge]:[a-z0-9][a-z0-9_-]{0,47}$")
_KIND_OF = {"n": "node", "g": "group", "e": "edge"}
_PLAN_KEYS = ("selected", "focus", "scope", "excluded", "title", "preset")
TITLE_WORDS = frozenset(("architecture", "overview", "path", "flow", "boundaries", "dependencies",
                         "security", "network", "private", "request", "data", "views"))


@dataclass
class Plan:
    selected: List[str]
    scope: str
    focus: List[str] = field(default_factory=list)
    excluded: List[Tuple[str, str]] = field(default_factory=list)
    title: str = ""
    preset: str = ""
    unmatched: List[str] = field(default_factory=list)
    planner: str = "deterministic"      # deterministic | preset | llm
    fallback: str = ""
    unknown_ids: int = 0
    cache: str = ""                     # "" | hit | miss
    extra_fields: int = 0
    #: the active tag set A of the deterministic planner (D3.2a); closure uses it
    active: List[str] = field(default_factory=list)
    #: the flow tag of the internal `flow` scope
    flow: str = ""
    #: sanitized request terms (for the title rule)
    terms: List[str] = field(default_factory=list)
    #: opt-in planner token usage ({"input_tokens", "output_tokens"} ints)
    usage: Dict[str, int] = field(default_factory=dict)
    #: plan-record notes (for example "view-truncated")
    notes: List[str] = field(default_factory=list)


def _str_list(v: Any, cap: int) -> bool:
    return isinstance(v, list) and len(v) <= cap and all(isinstance(x, str) for x in v)


def validate_plan(raw: Any, catalogue) -> Tuple[Optional[Plan], str]:
    """Validate a model plan (D2.3, N2). Returns (plan, "") or (None, "plan-invalid").

    Plan-invalid: not a dict; `selected` not a list of at most 64 strings; `focus` not a list of
    at most 6 strings; `scope` missing or not in the enum; `excluded` not a list of dicts;
    `title` or `preset` present and not a string. Dropped and counted (`unknown_ids`): an id
    that fails the pattern, is not a catalogue qid, or names the wrong kind; an `excluded` entry
    with an unknown id or a non-enum reason; a `preset` that is not a preset id."""
    if not isinstance(raw, dict):
        return None, "plan-invalid"
    sel, foc = raw.get("selected", []), raw.get("focus", [])
    if not _str_list(sel, PLAN_MAX_IDS) or not _str_list(foc, MAX_FOCUS):
        return None, "plan-invalid"
    scope = raw.get("scope")
    if not isinstance(scope, str) or scope not in SCOPES:
        return None, "plan-invalid"
    exc = raw.get("excluded", [])
    if not isinstance(exc, list) or not all(isinstance(x, dict) for x in exc):
        return None, "plan-invalid"
    for k in ("title", "preset"):
        if k in raw and not isinstance(raw[k], str):
            return None, "plan-invalid"
    unknown = 0

    def known(q: str, kinds: Sequence[str] = ("n", "g", "e")) -> bool:
        if not QID_RE.match(q) or q[0] not in kinds:
            return False
        f = catalogue.facts.get(q)
        return f is not None and f.kind == _KIND_OF[q[0]]

    selected: List[str] = []
    for q in sel:
        if known(q):
            if q not in selected:
                selected.append(q)
        else:
            unknown += 1
    focus: List[str] = []
    for q in foc:
        if known(q, ("n",)):
            if q not in focus:
                focus.append(q)
        else:
            unknown += 1
    excluded: List[Tuple[str, str]] = []
    for x in exc[:PLAN_MAX_IDS]:
        q, why = x.get("id"), x.get("reason")
        if isinstance(q, str) and known(q) and why in EXCLUDE_REASONS:
            if q not in [e for e, _ in excluded]:
                excluded.append((q, why))
        else:
            unknown += 1
    unknown += max(0, len(exc) - PLAN_MAX_IDS)
    preset = raw.get("preset", "")
    if preset and catalogue.preset(preset) is None:
        unknown += 1
        preset = ""
    extra = sum(1 for k in raw if k not in _PLAN_KEYS)
    if extra:
        logger.info("diagram: plan carried %d extra field(s) (plan-extra-field)", extra)
    if unknown:
        logger.info("diagram: plan dropped %d unknown id(s)", unknown)
    return Plan(selected=selected, scope=scope, focus=focus, excluded=excluded,
                title=raw.get("title", "") or "", preset=preset, planner="llm",
                unknown_ids=unknown, extra_fields=extra), ""


# -- title rule (D2.4) -----------------------------------------------------------------------

def _words_ok(title: str, allowed: set) -> bool:
    for w in re.findall(r"[A-Za-z]+", title):
        if len(w) < 4:
            continue
        lw = w.lower()
        if lw not in allowed and stem(lw) not in allowed:
            return False
    return True


def _trim(template: str, label: str) -> str:
    full = template.format(label=label)
    if len(full) <= MAX_TITLE:
        return full
    bare = template.replace(": {label}", "").replace(" {label}", "").replace("{label} ", "")
    return bare.strip()[:MAX_TITLE].strip()


def title_for(plan: Plan, catalogue, request_terms: Sequence[str] = (),
              caller_title: str = "") -> str:
    """The first that applies: the caller's title; a preset title; the model's title when it
    passes the label policy, the gate detectors and the vocabulary rule; a scope template."""
    if caller_title and L.check_label(caller_title) is None:
        return caller_title
    if plan.preset:
        p = catalogue.preset(plan.preset)
        if p is not None:
            return p.title
    if plan.title and L.check_label(plan.title) is None and \
            not L.looks_like_instruction(plan.title):
        allowed = set(TITLE_WORDS) | {t for t in request_terms}
        for q in plan.selected:
            f = catalogue.facts.get(q)
            if f is None:
                continue
            for text in [f.label] + list(f.aliases) + list(f.tags):
                for t in tokens(text):
                    allowed.add(t)
                    allowed.add(stem(t))
        if _words_ok(plan.title, allowed):
            return plan.title
    label = ""
    if plan.focus:
        f = catalogue.facts.get(plan.focus[0])
        label = f.label if f is not None else ""
    s = plan.scope
    if s == "end-to-end":
        return "End-to-end architecture"
    if s == "security":
        return "Security boundaries"
    if s == FLOW_SCOPE and plan.flow:
        return f"{plan.flow.replace('-', ' ').capitalize()} flow"
    if not label:
        return "Architecture overview"
    if s in ("path", "path-in"):
        return _trim("Request path: {label}", label)
    if s == "neighbourhood-in":
        return _trim("What talks to {label}", label)
    if s == "neighbourhood-out":
        return _trim("{label} and its dependencies", label)
    if len(plan.focus) > 1:
        f2 = catalogue.facts.get(plan.focus[1])
        if f2 is not None and f2.label != label:
            both = f"{label} and {f2.label}"
            if len(f"Around {both}") <= MAX_TITLE:
                return f"Around {both}"
    return _trim("Around {label}", label)


# -- mode resolution (D2.1) -------------------------------------------------------------------

def resolve_mode(per_request: str = "", env: Optional[str] = None, cfg_mode: str = "") -> str:
    """The planner mode for one request. A caller can only downgrade: a per-request
    "deterministic" forces it; else env GAC_DIAGRAM_PLANNER (deterministic|bedrock; any other
    value WARNING and deterministic); else the config mode (default deterministic). Whether
    "bedrock" can actually run (model, guardrail, boto3, breaker) is checked by the planner."""
    if per_request == "deterministic":
        return "deterministic"
    if env is None:
        env = os.getenv("GAC_DIAGRAM_PLANNER")
    if env is not None and env.strip() != "":
        v = env.strip().lower()
        if v in PLANNER_MODES:
            return v
        logger.warning("GAC_DIAGRAM_PLANNER=%r is not deterministic or bedrock; using "
                       "deterministic.", env)
        return "deterministic"
    return cfg_mode if cfg_mode in PLANNER_MODES else "deterministic"
