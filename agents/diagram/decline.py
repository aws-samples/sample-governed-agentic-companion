"""Honest declines and the knowledge-base notice lines (design D7.1, review M8). Pinned wording.

Every variable string is a sanitized request term (2..24 of [a-z0-9], label policy and gate
detectors already applied by the planner, request spans stripped first), a catalogue label, or
a preset's curated suggestion: nothing else from the request is echoed.

Suggestions, at most MAX_SUGGESTIONS, in this order: each preset's curated `suggestion`, then
"draw the architecture end to end", then "what talks to <label>" for the three highest-degree
catalogue nodes (so the end-to-end prompt is offered whenever it renders cleanly). Each is replayed through the deterministic planner, the closure and
the view planner, and kept only under the M8 rule: a preset suggestion must replay to that
preset (planner "preset", same id); every suggestion must have no unmatched term and every view
must pass the quality check (no draft)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from .catalogue import Catalogue
from .limits import MAX_SUGGESTIONS

MAX_TERMS = 6
HEADER = "[diagram] I can't draw this from the knowledge base, so no file was produced."
NOTHING = "nothing I recognise"
SUGGEST_HEAD = "I can draw, for example:"
ADD_FACTS = ('Add facts for your own architecture: docs/ARCHITECTURE.md, "Describe your '
             'topology for diagrams".')
DRAWN_FROM = "Drawn from the knowledge base, not from the live deployment."
E2E = "draw the architecture end to end"
NO_SUGGESTION = "- (no prompt I tried renders cleanly; run ./run.sh diagram --check-facts)"


@dataclass
class Replay:
    """What a suggestion gives when it is run as a request."""
    planner: str = ""
    preset: str = ""
    unmatched: List[str] = field(default_factory=list)
    views: int = 0
    all_pass: bool = False


def _quoted(items: Sequence[str]) -> str:
    return ", ".join(f'"{t}"' for t in items[:MAX_TERMS])


def degree_ranked(cat: Catalogue, k: int = 3) -> List[str]:
    """The k highest-degree catalogue nodes (non-denied edges), ties by fact order."""
    deg = {n: len(cat.adj_out.get(n, [])) + len(cat.adj_in.get(n, [])) for n in cat.nodes()}
    return sorted(deg, key=lambda n: (-deg[n], cat.order[n]))[:k]


def candidates(cat: Catalogue) -> List[Tuple[str, str]]:
    """[(prompt, preset id or "")] in the D7.1 order."""
    out: List[Tuple[str, str]] = []
    for p in cat.presets:
        if p.suggestion:
            out.append((p.suggestion, p.id))
    out.append((E2E, ""))
    for n in degree_ranked(cat):
        out.append((f"what talks to {cat.facts[n].label.lower()}", ""))
    seen, uniq = set(), []
    for prompt, pid in out:
        if prompt not in seen:
            seen.add(prompt)
            uniq.append((prompt, pid))
    return uniq


def keeps(prompt_preset: str, r: Optional[Replay]) -> bool:
    """The M8 strict replay rule."""
    if r is None or r.views < 1 or r.unmatched or not r.all_pass:
        return False
    if prompt_preset:
        return r.planner == "preset" and r.preset == prompt_preset
    return True


def suggestions(cands: Sequence[Tuple[str, str]], replay: Callable[[str], Optional[Replay]],
                limit: int = MAX_SUGGESTIONS) -> List[str]:
    out: List[str] = []
    for prompt, pid in cands:
        if len(out) >= limit:
            break
        if keeps(pid, replay(prompt)):
            out.append(prompt)
    return out


def _suggestion_lines(sugg: Sequence[str]) -> List[str]:
    lines = [SUGGEST_HEAD]
    lines += [f'- "{s}"' for s in sugg] if sugg else [NO_SUGGESTION]
    return lines


def no_plan_text(*, reason: str, terms: Sequence[str], unmatched: Sequence[str],
                 example_label: str, sugg: Sequence[str]) -> str:
    """The D7.1 decline for a request the planner cannot anchor or match."""
    recognised = [t for t in terms if t not in set(unmatched)]
    lines = [HEADER, f"Asked for: {_quoted(recognised) if recognised else NOTHING}"]
    if unmatched:
        lines.append(f"Missing: no diagram facts mention {_quoted(unmatched)}")
    else:
        lines.append(f'Missing: no component named in the request; name one, for example '
                     f'"{example_label}"')
    lines += _suggestion_lines(sugg)
    lines.append(ADD_FACTS)
    return "\n".join(lines)


def views_bounds_text(needed: int, limit: int, sugg: Sequence[str]) -> str:
    """The view-bound decline (D6.3 step 5): nothing is trimmed."""
    lines = [f"[diagram] This needs {needed} views; the limit is {limit}, so no file was "
             f"produced. Ask for one part at a time."]
    lines += _suggestion_lines(sugg)
    return "\n".join(lines)


def selection_bounds_text(needed: int, limit: int, sugg: Sequence[str]) -> str:
    """The selection-bound decline (MAX_SELECTION_NODES)."""
    lines = [f"[diagram] This needs {needed} nodes; the limit is {limit}, so no file was "
             f"produced. Ask for one part at a time."]
    lines += _suggestion_lines(sugg)
    return "\n".join(lines)


def not_in_kb_line(unmatched: Sequence[str]) -> str:
    return f"Not in the knowledge base: {_quoted(unmatched)}" if unmatched else ""


def drawn_from_line(topology_note: str) -> str:
    return f"{DRAWN_FROM} {topology_note}".rstrip() if topology_note else DRAWN_FROM
