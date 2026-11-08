"""Subgraph closure (design D3.4), shared by both planners. Turns a plan's selection into the
closed set S that views draw: seed, endpoints, connect, induced edges, boundaries, isolated
nodes. Closure only ever adds catalogue facts (with an `added_by` reason) or lists them as
excluded; it never invents an element (Tenet 4). The selection is bounded by
MAX_SELECTION_NODES (Tenet 12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .catalogue import Catalogue
from .limits import CLOSURE_MAX_ADDED, MAX_SELECTION_NODES
from .plan import Plan
from .planner_det import admissible

CONNECT_MAX_HOPS = 3
NO_CONNECT_SCOPES = ("security", "end-to-end")
DENIED_SCOPES = ("security", "end-to-end")


@dataclass
class Closed:
    nodes: List[str]
    groups: List[str]
    edges: List[str]
    scope: str
    mode: str                                   # "preset" | "generic"
    added_by: Dict[str, str] = field(default_factory=dict)
    excluded: Dict[str, str] = field(default_factory=dict)
    bounds: bool = False
    preset: str = ""

    @property
    def facts(self) -> List[str]:
        return self.groups + self.nodes + self.edges

    def __contains__(self, q: str) -> bool:
        return q in self.nodes or q in self.groups or q in self.edges


def _ordered(cat: Catalogue, qs: Iterable[str]) -> List[str]:
    return sorted(set(qs), key=lambda q: cat.order[q])


def _components(cat: Catalogue, nodes: Set[str], edges: Iterable[str]) -> List[List[str]]:
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        a, b = cat.endpoints(e)
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    comps: Dict[str, List[str]] = {}
    for n in _ordered(cat, nodes):
        comps.setdefault(find(n), []).append(n)
    return list(comps.values())


def _best_path(cat: Catalogue, comp: Set[str], target: Set[str], included: Set[str], ok
               ) -> Optional[Tuple[List[str], List[str]]]:
    """The shortest admissible path (at most 3 hops) from `comp` to `target`. Ties: fewest
    added nodes, then the fact order of the first edge (then of the rest). Returns
    (edges, added nodes) or None."""
    best: Optional[Tuple[tuple, List[str], List[str]]] = None

    def nbrs(n: str) -> List[Tuple[str, str]]:
        both = list(cat.adj_out.get(n, [])) + list(cat.adj_in.get(n, []))
        return sorted(both, key=lambda ew: cat.order[ew[0]])

    def dfs(n: str, path_e: List[str], path_n: List[str]) -> None:
        nonlocal best
        if len(path_e) >= CONNECT_MAX_HOPS:
            return
        for e, w in nbrs(n):
            if not ok(e) or w in comp or w in path_n:
                continue
            pe, pn = path_e + [e], path_n + [w]
            if w in target:
                added = [x for x in pn[:-1] if x not in included]
                key = (len(pe), len(added), tuple(cat.order[x] for x in pe))
                if best is None or key < best[0]:
                    best = (key, pe, added)
                continue
            dfs(w, pe, pn)

    for start in _ordered(cat, comp):
        dfs(start, [], [start])
    if best is None:
        return None
    return best[1], best[2]


def close(plan: Plan, cat: Catalogue, A: Optional[Iterable[str]] = None) -> Closed:
    """D3.4 steps 1 to 7."""
    if plan.preset and cat.preset(plan.preset) is not None:
        pf = cat.preset_facts(plan.preset)
        kinds = {q[0]: [x for x in pf if x[0] == q[0]] for q in ("n", "g", "e")}
        return Closed(nodes=kinds["n"], groups=kinds["g"], edges=kinds["e"], scope=plan.scope,
                      mode="preset", preset=plan.preset,
                      bounds=len(kinds["n"]) > MAX_SELECTION_NODES)
    A = set(plan.active if A is None else A)
    anchors = set(plan.focus)
    excluded: Dict[str, str] = {q: why for q, why in plan.excluded}
    selected = [q for q in plan.selected if q in cat.facts]
    added_by: Dict[str, str] = {}
    nodes: Set[str] = set()
    groups: Set[str] = set()
    edges: Set[str] = set()

    def add(q: str, why: str) -> None:
        bucket = nodes if q[0] == "n" else groups if q[0] == "g" else edges
        if q not in bucket:
            bucket.add(q)
            added_by[q] = why

    # 1. seed
    for q in selected:
        if q[0] == "n":
            nodes.add(q)
        elif q[0] == "g":
            groups.add(q)
    for g in [q for q in selected if q[0] == "g"]:
        for m in cat.members(g):
            add(m, "closure:group-member")
    # 2. endpoints
    for e in (q for q in selected if q[0] == "e"):
        edges.add(e)
        for n in cat.endpoints(e):
            add(n, "closure:endpoint")

    def ok(e: str) -> bool:
        return admissible(cat, e, A, (), anchors)

    # 3. connect
    if plan.scope not in NO_CONNECT_SCOPES:
        comps = _components(cat, nodes, [e for e in edges if ok(e)])
        if len(comps) > 1:
            largest = max(comps, key=lambda c: (len(c), -cat.order[c[0]]))
            big = set(largest)
            others = sorted((c for c in comps if c is not largest),
                            key=lambda c: (len(c), cat.order[c[0]]))
            added_nodes = 0
            for comp in others:
                if set(comp) <= big:
                    continue
                found = _best_path(cat, set(comp), big, nodes, ok)
                if found is None:
                    continue
                pe, pn = found
                if added_nodes + len(pn) > CLOSURE_MAX_ADDED:
                    continue
                for e in pe:
                    add(e, "closure:path")
                for n in pn:
                    add(n, "closure:path")
                added_nodes += len(pn)
                big |= set(comp) | set(pn)
    # 4. induced edges
    for e in cat.edges():
        if e in edges:
            continue
        a, b = cat.endpoints(e)
        if a not in nodes or b not in nodes:
            continue
        if cat.denied(e):
            if plan.scope in DENIED_SCOPES:
                add(e, "closure:induced")
            continue
        if e in excluded:
            continue
        if ok(e):
            add(e, "closure:induced")
        else:
            excluded[e] = "other-flow"
    # 6. isolated nodes (before boundaries, so a boundary is only kept for a drawn node)
    touched: Set[str] = set()
    for e in edges:
        touched.update(cat.endpoints(e))
    keep_alone = set(selected) | anchors if plan.scope != "end-to-end" else set()
    for n in list(nodes):
        if n not in touched and n not in keep_alone:
            nodes.discard(n)
            added_by.pop(n, None)
            excluded[n] = "isolated"
    # 5. boundaries
    for q in list(nodes) + list(groups):
        for g in cat.ancestors(q):
            add(g, "closure:boundary")
    # 7. mode
    closed = Closed(nodes=_ordered(cat, nodes), groups=_ordered(cat, groups),
                    edges=_ordered(cat, edges), scope=plan.scope, mode="generic",
                    added_by=added_by, excluded=excluded)
    closed.bounds = len(closed.nodes) > MAX_SELECTION_NODES
    return closed
