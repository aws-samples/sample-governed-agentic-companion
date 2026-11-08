"""The view planner (design D6.2 to D6.4): turns the closed selection S into at most `max_views`
views the unchanged layout engine draws well. Pure: no rendering here; the generic try calls a
caller-supplied `layout_fn` so this module stays testable without icons.

Algorithm (D6.3): a preset plan is one preset view (hints on). Otherwise an eligible selection
(D6.2 envelope) gets one generic try; if it passes it is the only view. Else a greedy preset
cover (largest |facts(p) & U| first, ties by preset order) and then flow views for whatever is
left (partitioned by `flow_order`, consecutive partitions merged while the view has at most 6
nodes and 4 ranks). More views than `max_views` is a bounds decline; nothing is trimmed. Views
are ordered presets first (preset order), then flows (flow order). Every fact of S is drawn in
a view or excluded with a reason (the coverage invariant), and every catalogue fact gets one
status: selected, added, context or excluded (the D3.4 reporting rule)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from .catalogue import Catalogue
from .closure import Closed
from .limits import (FLOW_VIEW_MAX_RANKS, GENERIC_VIEW_MAX_EDGES, GENERIC_VIEW_MAX_NODES,
                     Limits, MAX_VIEWS)

OTHER = "other"
BASE_MAX = 32
NAME_MAX = 64
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class CoverageError(AssertionError):
    """A node or edge of S is in no view and carries no exclusion reason (a views.py bug)."""


@dataclass
class View:
    id: str                    # preset id | "generic" | "<tag>-flow" | "other"
    kind: str                  # "preset" | "generic" | "flow"
    title: str
    facts: List[str]           # qids drawn, in fact order (groups, nodes and edges)
    preset: str = ""
    flow: str = ""
    hints: bool = False
    #: drawn facts that are not in S (preset views only)
    context: List[str] = field(default_factory=list)
    #: the generic-try layout, reused when this view equals S
    rendered: object = None
    name: str = ""

    @property
    def nodes(self) -> List[str]:
        return [q for q in self.facts if q[0] == "n"]

    @property
    def edges(self) -> List[str]:
        return [q for q in self.facts if q[0] == "e"]


@dataclass
class ViewPlan:
    views: List[View]
    #: every catalogue fact -> (status, reason); status selected|added|context|excluded
    status: Dict[str, Tuple[str, str]]
    generic_tried: bool = False

    @property
    def context_count(self) -> int:
        return sum(1 for s, _ in self.status.values() if s == "context")

    def count(self, status: str) -> int:
        return sum(1 for s, _ in self.status.values() if s == status)


@dataclass
class BoundsDecline:
    needed: int
    limit: int
    #: one candidate prompt per view, in view order (replayed by decline.py)
    suggestions: List[Tuple[str, str]] = field(default_factory=list)   # (prompt, preset id|"")


def slug(text: str, cap: int = NAME_MAX) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:cap].strip("-")


def _ordered(cat: Catalogue, qs) -> List[str]:
    return sorted(set(qs), key=lambda q: cat.order[q])


def preset_view_facts(cat: Catalogue, preset_id: str) -> List[str]:
    """What compose() draws for a preset: its nodes, its edges between two of its nodes, and its
    groups on each node's ancestor chain while the chain stays inside the preset."""
    tagged = set(cat.preset_facts(preset_id))
    nodes = {q for q in tagged if q[0] == "n"}
    keep: Set[str] = set()
    for n in nodes:
        g = cat.group_of(n)
        while g is not None and g in tagged and g not in keep:
            keep.add(g)
            g = cat.group_of(g)
    edges = set()
    for q in tagged:
        if q[0] == "e":
            a, b = cat.endpoints(q)
            if a in nodes and b in nodes:
                edges.add(q)
    return _ordered(cat, nodes | keep | edges)


def _with_boundaries(cat: Catalogue, qs: Set[str]) -> Set[str]:
    out = set(qs)
    for q in list(qs):
        if q[0] in ("n", "g"):
            out.update(cat.ancestors(q))
    return out


def eligible(closed: Closed, cat: Catalogue) -> bool:
    """The D6.2 generic-view envelope (a pre-filter; the quality check decides)."""
    if closed.scope == "end-to-end":
        return False
    if len(closed.nodes) > GENERIC_VIEW_MAX_NODES or len(closed.edges) > GENERIC_VIEW_MAX_EDGES:
        return False
    seen = set()
    for n in closed.nodes:
        f = cat.facts[n]
        key = (f.label, f.icon)
        if key in seen:
            return False
        seen.add(key)
    return True


def _flow_key(cat: Catalogue, tags: Sequence[str]) -> Optional[Tuple[int, int, str]]:
    """The partition of an edge: its first tag in flow_order; undeclared tags follow in
    alphabetical order."""
    order = cat.vocabulary.flow_order
    best = None
    for t in tags:
        k = (0, order.index(t), t) if t in order else (1, 0, t)
        if best is None or k < best:
            best = k
    return best


def _ranks(cat: Catalogue, nodes: Set[str], edges: Set[str]) -> int:
    """The longest path in nodes (the width proxy, D6.3 step 4). Denied edges and cycles do not
    lengthen it (bounded relaxation)."""
    rank = {n: 0 for n in nodes}
    live = [e for e in _ordered(cat, edges) if not cat.denied(e)]
    for _ in range(len(nodes)):
        changed = False
        for e in live:
            a, b = cat.endpoints(e)
            if a in rank and b in rank and rank[b] < rank[a] + 1 and rank[a] + 1 < len(nodes):
                rank[b] = rank[a] + 1
                changed = True
        if not changed:
            break
    return (max(rank.values()) + 1) if rank else 0


def _flow_partitions(cat: Catalogue, U: Set[str], s_edges: Sequence[str]
                     ) -> List[Tuple[str, Set[str]]]:
    """[(tag, facts)] over the facts still in U, in flow order then alphabetical, `other` last.
    A partition's facts are its edges and their endpoints (boundaries are added later)."""
    edges = [e for e in _ordered(cat, U) if e[0] == "e"]
    part: Dict[str, str] = {}
    for e in edges:
        k = _flow_key(cat, cat.facts[e].tags)
        if k is not None:
            part[e] = k[2]
    for e in edges:
        if e in part:
            continue
        ends = set(cat.endpoints(e))
        adj = [x for x in edges if x in part and ends & set(cat.endpoints(x))]
        part[e] = part[adj[0]] if adj else OTHER
    groups: Dict[str, Set[str]] = {}
    for e in edges:
        groups.setdefault(part[e], set()).update({e, *cat.endpoints(e)})
    for n in (q for q in _ordered(cat, U) if q[0] == "n"):
        if any(n in fs for fs in groups.values()):
            continue
        target = OTHER
        for e in s_edges:
            a, b = cat.endpoints(e)
            other = b if a == n else a if b == n else None
            if other is None:
                continue
            hit = next((t for t, fs in groups.items() if other in fs), None)
            if hit is not None:
                target = hit
                break
        groups.setdefault(target, set()).add(n)

    def key(tag: str):
        if tag == OTHER:
            return (2, 0, tag)
        k = _flow_key(cat, [tag])
        return k if k is not None else (1, 0, tag)

    return [(t, groups[t]) for t in sorted(groups, key=key)]


def flow_title(tag: str) -> str:
    if tag == OTHER:
        return "Other connections"
    return f"{tag.replace('-', ' ').capitalize()} flow"


def flow_id(tag: str) -> str:
    return OTHER if tag == OTHER else f"{tag}-flow"


def _merge_flows(cat: Catalogue, parts: List[Tuple[str, Set[str]]]) -> List[Tuple[str, Set[str]]]:
    out: List[Tuple[str, Set[str]]] = []
    for tag, fs in parts:
        if out:
            ptag, pfs = out[-1]
            merged = pfs | fs
            nodes = {q for q in merged if q[0] == "n"}
            edges = {q for q in merged if q[0] == "e"}
            if (len(nodes) <= GENERIC_VIEW_MAX_NODES
                    and _ranks(cat, nodes, edges) <= FLOW_VIEW_MAX_RANKS):
                out[-1] = (ptag, merged)
                continue
        out.append((tag, set(fs)))
    return out


def _status(cat: Catalogue, closed: Closed, views: List[View], selected: Set[str]
            ) -> Dict[str, Tuple[str, str]]:
    drawn: Set[str] = set()
    for v in views:
        drawn.update(v.facts)
    in_s = set(closed.facts)
    status: Dict[str, Tuple[str, str]] = {}
    for q in cat.facts:
        if q in in_s and q in drawn:
            if closed.mode == "preset" or q in selected:
                status[q] = ("selected", "")
            else:
                status[q] = ("added", closed.added_by.get(q, "closure"))
        elif q in drawn:
            status[q] = ("context", "")
        elif q in in_s:
            if q[0] == "g":
                status[q] = ("excluded", "boundary-not-drawn")
            elif closed.mode == "preset":
                status[q] = ("excluded", "endpoint-not-in-preset")
            else:
                raise CoverageError(q)
        else:
            status[q] = ("excluded", closed.excluded.get(q, "not-selected"))
    return status


def plan_views(closed: Closed, cat: Catalogue, limits: Limits, *, title: str = "",
               selected: Sequence[str] = (),
               layout_fn: Optional[Callable[[View], Tuple[bool, object]]] = None
               ) -> Union[ViewPlan, BoundsDecline]:
    """D6.3 steps 1 to 6 and 8 (step 7, quality per view, and step 9, the deadline, belong to
    the renderer). `selected` is the planner's own selection (the rest of S is `added`).
    `layout_fn(view) -> (passed, rendered)` runs the generic try; without it the try is
    skipped (tests of the cover only)."""
    max_views = max(1, min(MAX_VIEWS, limits.max_views))
    sel = set(selected) if selected else set(closed.facts)
    if closed.mode == "preset":
        p = cat.preset(closed.preset)
        facts = preset_view_facts(cat, closed.preset)
        v = View(closed.preset, "preset", p.title if p else closed.preset, facts,
                 preset=closed.preset, hints=True)
        return ViewPlan([v], _status(cat, closed, [v], set(closed.facts)))
    s_facts = _ordered(cat, closed.facts)
    generic = View("generic", "generic", title, s_facts)
    tried = False
    fallback = None
    if eligible(closed, cat) and layout_fn is not None:
        tried = True
        passed, rendered = layout_fn(generic)
        if passed:
            generic.rendered = rendered
            return ViewPlan([generic], _status(cat, closed, [generic], sel), True)
        fallback = rendered
    # 3. preset cover over S's nodes and edges
    U: Set[str] = {q for q in closed.facts if q[0] in ("n", "e")}
    preset_views: List[View] = []
    pfacts = {p.id: preset_view_facts(cat, p.id) for p in cat.presets}
    chosen: List[str] = []
    while U and len(chosen) < MAX_VIEWS:
        best, best_n = None, 0
        for p in cat.presets:
            if p.id in chosen:
                continue
            n = len(U & set(pfacts[p.id]))
            if n > best_n:
                best, best_n = p, n
        if best is None:
            break
        chosen.append(best.id)
        U -= set(pfacts[best.id])
    in_s = set(closed.facts)
    for p in cat.presets:
        if p.id in chosen:
            fs = pfacts[p.id]
            preset_views.append(View(p.id, "preset", p.title, fs, preset=p.id, hints=True,
                                     context=[q for q in fs if q not in in_s]))
    # 4. flow views for what U still holds
    flow_views: List[View] = []
    if U:
        parts = _merge_flows(cat, _flow_partitions(cat, U, closed.edges))
        for tag, fs in parts:
            facts = _ordered(cat, _with_boundaries(cat, fs))
            v = View(flow_id(tag), "flow", flow_title(tag), facts, flow=tag)
            if set(facts) == in_s and fallback is not None:
                v.rendered = fallback
            flow_views.append(v)
    views = preset_views + flow_views
    if not views:
        views = [generic]
    # 5. bound
    if len(views) > max_views:
        sugg: List[Tuple[str, str]] = []
        for v in views:
            if v.kind == "preset":
                p = cat.preset(v.preset)
                if p is not None and p.suggestion:
                    sugg.append((p.suggestion, p.id))
            elif v.flow and v.flow != OTHER:
                sugg.append((f"draw the {v.flow.replace('-', ' ')} flow", ""))
        return BoundsDecline(len(views), max_views, sugg)
    return ViewPlan(views, _status(cat, closed, views, sel), tried)


def assign_names(views: List[View], *, caller_name: str, title: str) -> None:
    """D6.4: one view keeps the caller's name, else the preset id or the slug of the title;
    several views are `<base>-<i>-<view id>` (base: the caller's name or the title slug cut
    to 32 characters), at most 64 characters."""
    if len(views) == 1:
        v = views[0]
        v.name = caller_name or (v.preset if v.kind == "preset" else "") or slug(title) or v.id
        v.name = v.name[:NAME_MAX].strip("-") or "diagram"
        return
    base = caller_name[:BASE_MAX].strip("-") if caller_name else slug(title, BASE_MAX)
    base = base or "diagram"
    for i, v in enumerate(views, 1):
        v.name = f"{base}-{i}-{v.id}"[:NAME_MAX].strip("-")
