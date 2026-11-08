"""The diagram lines of the governance footer (design D5.3). These eight templates are the only
accepted shapes; the gate keeps a line only when it matches PATTERN (no `;` or `|`), renders it
after "Sources" and before "Gate status", and only on a passing response. The audit `extra`
keys are restricted to AUDIT_KEYS with int or enum values."""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Union

PATTERN = re.compile(r"^  (Diagram planner|Diagram facts|Diagram views|Diagram source): "
                     r"[A-Za-z0-9 ()%:,./>=+-]{1,160}$")
AUDIT_KEYS = ("planner", "included", "excluded", "closure_added", "unknown_ids", "views",
              "input_tokens", "output_tokens", "fallback")
_ENUM = re.compile(r"^[a-z0-9-]{0,32}$")
_CODE = re.compile(r"^[a-z-]{3,32}$")
_VIEW_ID = re.compile(r"^[a-z0-9-]{1,48}$")
MAX_VIEW_IDS = 4

SOURCE_LINE = "  Diagram source: knowledge base, not the live deployment"
LLM_TEXT = "llm (model-selected facts, every element traced)"


def planner_line(planner: str, fallback: str = "", cache: str = "") -> str:
    """`preset`, `deterministic`, `deterministic (fallback: <code>)` or the llm line with
    `cache hit|miss`."""
    if planner == "llm":
        return f"  Diagram planner: {LLM_TEXT}, cache {'hit' if cache == 'hit' else 'miss'}"
    if planner == "preset":
        return "  Diagram planner: preset"
    if fallback and _CODE.match(fallback):
        return f"  Diagram planner: deterministic (fallback: {fallback})"
    return "  Diagram planner: deterministic"


def facts_line(selected: int, added: int, excluded: int, unknown_ids: int = 0) -> str:
    return (f"  Diagram facts: {selected + added} included ({selected} selected, {added} added "
            f"by closure), {excluded} excluded, {unknown_ids} unknown ids")


def views_line(view_ids: Sequence[str], context: int) -> str:
    ids = [v for v in view_ids if _VIEW_ID.match(v)][:MAX_VIEW_IDS]
    lead = f"{len(view_ids)} ("
    tail = f"), {context} context elements"
    shown = ", ".join(ids)
    room = 160 - len(lead) - len(tail)      # the pattern allows 160 characters after the label
    if len(shown) > room:
        shown = shown[:max(0, room - 3)].rstrip(", ") + "..."
    return "  Diagram views: " + lead + shown + tail


def lines_for(*, planner: str, fallback: str = "", cache: str = "", selected: int = 0,
              added: int = 0, excluded: int = 0, unknown_ids: int = 0,
              view_ids: Sequence[str] = (), context: int = 0, rendered: bool = True
              ) -> List[str]:
    """The footer lines of one diagram response: a render gets all four, a decline the planner
    and source lines."""
    out = [planner_line(planner, fallback, cache)]
    if rendered:
        out.append(facts_line(selected, added, excluded, unknown_ids))
        out.append(views_line(view_ids, context))
    out.append(SOURCE_LINE)
    return out


def valid(line: str) -> bool:
    return isinstance(line, str) and bool(PATTERN.match(line))


def audit_extra(raw: Dict[str, object]) -> Dict[str, Union[int, str]]:
    """Keep only the D5.3 keys with an int (not bool) or a short enum string value."""
    out: Dict[str, Union[int, str]] = {}
    for k in AUDIT_KEYS:
        if k not in raw:
            continue
        v = raw[k]
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out[k] = v
        elif isinstance(v, str) and _ENUM.match(v):
            out[k] = v
    return out
