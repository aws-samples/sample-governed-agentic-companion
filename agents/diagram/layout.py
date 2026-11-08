"""Deterministic layout for a traceable Scene (design D6.1 to D6.3).

Containment tree -> ranks (DFS back edges, longest path) -> bottom-up container layout (columns by
rank, data flows left to right, barycentre ordering, column offsets that straighten arrows) ->
absolute geometry, title, legend and the fixed caption. The router (route.py) adds the edges and
the quality check (quality.py) measures the result. The candidate loop (best_layout) tries the
three candidates in order within the render deadline and keeps the best by the "improves" rule.

All coordinates are snapped to the 8 px grid; nothing here reads a clock except through the
injected `clock`, and nothing depends on dict ordering, so output is byte-stable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .compose import Scene
from .limits import CANVAS_MAX_H, CANVAS_MAX_W
from .style import (CAPTION, LEGEND_DASHED, LEGEND_DENIED, LEGEND_OUTLINE,
                    LEGEND_SOLID, LEGEND_TITLE)

GRID = 8
ICON = 48
GROUP_ICON = 24
FONT = 13
TITLE_FONT = 18
LINE_H = 16
CELL_H = 88
CELL_W = {"A": 112, "B": 96, "C": 112}
COL_GAP = 64
ROW_GAP = 24
LINK_GAP = 16   # extra room between two stacked items an arrow joins (stub + straight run)
STACK_GAP = 48
TITLE_AREA = 24
PAD = 16
BAND = 40
MARGIN = 16
LEGEND_GAP = 24

_NARROW = set("il.,:;'|!")
_WIDE = set("mwMW@")


def text_width(text: str, size: int = FONT, bold: bool = False) -> float:
    """Conservative advance estimate (overestimates DejaVu Sans; D6.2)."""
    w = 0.0
    for ch in text:
        if ch == " ":
            w += 0.32
        elif ch in _NARROW:
            w += 0.32
        elif ch in _WIDE:
            w += 0.92
        elif ch.isupper():
            w += 0.72
        else:
            w += 0.62
    return w * size * (1.06 if bold else 1.0)


def wrap(label: str, max_w: float, size: int = FONT, max_lines: int = 2) -> Tuple[List[str], bool]:
    """Wrap on spaces to at most `max_lines`; cut the last line with an ellipsis if needed."""
    words = label.split(" ")
    lines: List[str] = []
    cur = ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if text_width(cand, size) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = w
    if cur:
        lines.append(cur)
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    out = []
    for ln in lines:
        if text_width(ln, size) > max_w:
            truncated = True
            while ln and text_width(ln + "...", size) > max_w:
                ln = ln[:-1]
            ln = ln.rstrip() + "..."
        out.append(ln)
    return out, truncated


def snap(v: float) -> int:
    return int(round(v / GRID)) * GRID


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def r(self) -> float:
        return self.x + self.w

    @property
    def b(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def inflate(self, d: float) -> "Box":
        return Box(self.x - d, self.y - d, self.w + 2 * d, self.h + 2 * d)

    def overlaps(self, o: "Box") -> bool:
        return self.x < o.r and o.x < self.r and self.y < o.b and o.y < self.b

    def contains(self, o: "Box") -> bool:
        return self.x <= o.x and self.y <= o.y and o.r <= self.r and o.b <= self.b


@dataclass
class NodeGeo:
    gid: str
    key: str
    label: str
    lines: List[str]
    truncated: bool
    icon: Box
    caption: Box
    cell: Box
    icon_key: str = ""
    parent: Optional[str] = None
    optional: bool = False


@dataclass
class GroupGeo:
    gid: str
    key: str
    type: str
    label: str
    box: Box
    title_box: Box
    icon_box: Optional[Box]
    parent: Optional[str]
    depth: int
    title_bound: bool = False


@dataclass
class EdgeGeo:
    gid: str
    key: str
    src: str
    dst: str
    points: List[Tuple[float, float]]
    optional: bool
    style: str
    bidirectional: bool
    label: str = ""
    label_box: Optional[Box] = None
    back: bool = False


@dataclass
class TextGeo:
    text: str
    box: Box
    size: int = FONT
    bold: bool = False
    color: str = "primary"   # primary | secondary
    anchor: str = "start"


@dataclass
class Layout:
    width: int
    height: int
    candidate: str
    nodes: List[NodeGeo]
    groups: List[GroupGeo]
    edges: List[EdgeGeo] = field(default_factory=list)
    title: Optional[TextGeo] = None
    legend: List[TextGeo] = field(default_factory=list)
    legend_lines: List[Tuple[str, Tuple[float, float, float, float]]] = field(default_factory=list)
    legend_box: Optional[Box] = None
    caption: Optional[TextGeo] = None
    title_sets_width: float = 0.0

    def node(self, key: str) -> NodeGeo:
        return next(n for n in self.nodes if n.key == key)


# -- containment tree --------------------------------------------------------------------------

@dataclass
class _Item:
    kind: str
    key: str
    order: int
    children: List["_Item"] = field(default_factory=list)
    rank: int = 0
    w: float = 0
    h: float = 0
    x: float = 0
    y: float = 0
    columns: List[List["_Item"]] = field(default_factory=list)
    col_x: List[float] = field(default_factory=list)
    col_w: List[float] = field(default_factory=list)
    pads: Dict[int, float] = field(default_factory=dict)
    pre: float = 0
    stacked: bool = False
    title_bound: bool = False
    gtype: str = ""
    label: str = ""
    row: int = 50

    def node_keys(self) -> List[str]:
        if self.kind == "node":
            return [self.key]
        out: List[str] = []
        for c in self.children:
            out.extend(c.node_keys())
        return out


def _ranks(scene: Scene) -> Tuple[Dict[str, int], set]:
    nodes = [n.id for n in scene.nodes]
    out_edges: Dict[str, List[Tuple[str, str]]] = {n: [] for n in nodes}
    for e in scene.edges:
        out_edges[e.src].append((e.id, e.dst))
    back: set = set()
    state: Dict[str, int] = {}

    def dfs(u: str) -> None:
        state[u] = 1
        for eid, v in out_edges[u]:
            if state.get(v) == 1:
                back.add(eid)
            elif v not in state:
                dfs(v)
        state[u] = 2

    for n in nodes:
        if n not in state:
            dfs(n)
    rank = {n: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for e in scene.edges:
            if e.id in back or e.style == "denied":
                continue
            if rank[e.dst] < rank[e.src] + 1:
                rank[e.dst] = rank[e.src] + 1
                changed = True
        if not changed:
            break
    for n in scene.nodes:
        if getattr(n, "col", None) is not None:
            rank[n.id] = n.col
    return rank, back


def _denied_only(scene: Scene) -> set:
    """Callers whose only edges are denied calls (drawn last in their column)."""
    out = set()
    for n in scene.nodes:
        mine = [e for e in scene.edges if n.id in (e.src, e.dst)]
        if mine and all(e.style == "denied" and e.src == n.id for e in mine):
            out.add(n.id)
    return out


def _tree(scene: Scene, rank: Dict[str, int]) -> _Item:
    groups = {g.id: g for g in scene.groups}
    order = {e.id: i for i, e in enumerate(scene.groups + scene.nodes)}
    items: Dict[str, _Item] = {}
    for g in scene.groups:
        items["g:" + g.id] = _Item("group", g.id, order[g.id], gtype=g.type, label=g.label)
    root = _Item("group", "__root__", -1)
    for g in scene.groups:
        parent = items.get("g:" + g.parent) if g.parent in groups else root
        parent.children.append(items["g:" + g.id])
    for n in scene.nodes:
        it = _Item("node", n.id, order[n.id], label=n.label,
                   row=n.row if getattr(n, "row", None) is not None else 50)
        parent = items.get("g:" + n.parent) if n.parent in groups else root
        parent.children.append(it)

    def set_rank(it: _Item) -> int:
        if it.kind == "node":
            it.rank = rank[it.key]
        else:
            rs = [set_rank(c) for c in it.children]
            it.rank = min(rs) if rs else 0
            it.row = min([c.row for c in it.children] or [50])
        return it.rank

    set_rank(root)
    return root


# -- container layout ---------------------------------------------------------------------------

class _Engine:
    def __init__(self, scene: Scene, candidate: str):
        self.scene = scene
        self.candidate = candidate
        self.cell_w = CELL_W[candidate]
        self.rank, self.back = _ranks(scene)
        self.denied_only = _denied_only(scene)
        self.root = _tree(scene, self.rank)
        self._elem = {n.id: n for n in scene.nodes}
        if candidate == "B":
            self._pull_sinks()
        self.pos: Dict[str, Tuple[float, float]] = {}   # node key -> icon centre
        self.nbrs: Dict[str, List[str]] = {n.id: [] for n in scene.nodes}
        for e in scene.edges:
            self.nbrs[e.src].append(e.dst)
            self.nbrs[e.dst].append(e.src)
        self._deepest = {id(it) for it in self._walk(self.root)
                         if it.kind == "group" and it is not self.root
                         and it.children and all(c.kind == "node" for c in it.children)}

    def _pull_sinks(self) -> None:
        """Candidate C: a node with no outgoing edge moves to the last rank used in its own
        container, so it shares a column instead of adding one (narrower canvas)."""
        has_out = {e.src for e in self.scene.edges}
        for it in self._walk(self.root):
            if it.kind != "group" or not it.children:
                continue
            last = max(c.rank for c in it.children)
            for c in it.children:
                hinted = getattr(self._elem.get(c.key), "col", None) is not None
                if c.kind == "node" and c.key not in has_out and c.rank < last and not hinted:
                    c.rank = last
                    self.rank[c.key] = last

    def _walk(self, it: _Item):
        yield it
        for c in it.children:
            yield from self._walk(c)

    def _label_need(self, it: _Item, ci: int) -> float:
        """Width an edge label needs in the channel right of column ci (the channel just left of
        the column holding the edge's target), so the label sits clear of group borders."""
        cols = [set(k for c in col for k in c.node_keys()) for col in it.columns]
        margin = (self.cell_w - ICON) / 2

        def side_margin(ci_: int, key: str) -> float:
            item = next(c for c in it.columns[ci_] if key in c.node_keys())
            return margin if item.kind == "node" else 0.0

        need = 0.0
        for e in self.scene.edges:
            if not e.label:
                continue
            si = next((i for i, c in enumerate(cols) if e.src in c), None)
            di = next((i for i, c in enumerate(cols) if e.dst in c), None)
            if si is None or di is None or si == di:
                continue
            if (si < di and ci == di - 1) or (si > di and ci == di):
                clean = side_margin(si, e.src) * (abs(si - di) == 1) + side_margin(di, e.dst)
                need = max(need, text_width(e.label) + 4 + 24 - clean)
        return min(need, 160.0)

    def _linked(self, a: _Item, b: _Item) -> bool:
        """Two items stacked in one column that an arrow joins get room for a straight run."""
        ka, kb = set(a.node_keys()), set(b.node_keys())
        return any((e.src in ka and e.dst in kb) or (e.src in kb and e.dst in ka)
                   for e in self.scene.edges)

    def _lanes(self, it: _Item, ci: int) -> int:
        """Edges crossing the channel right of column ci inside container it."""
        left = set()
        for col in it.columns[:ci + 1]:
            for c in col:
                left.update(c.node_keys())
        right = set()
        for col in it.columns[ci + 1:]:
            for c in col:
                right.update(c.node_keys())
        return sum(1 for e in self.scene.edges
                   if (e.src in left and e.dst in right) or (e.src in right and e.dst in left))

    def measure(self, it: _Item) -> None:
        if it.kind == "node":
            it.w, it.h = self.cell_w, CELL_H
            return
        for c in it.children:
            self.measure(c)
        by_rank: Dict[int, List[_Item]] = {}
        for c in it.children:
            by_rank.setdefault(c.rank, []).append(c)
        if not it.columns or sum(len(c) for c in it.columns) != len(it.children):
            it.columns = []
            for r in sorted(by_rank):
                col = sorted(by_rank[r], key=lambda c: (c.row, c.order))
                inner = set()
                for c in col:
                    if c.kind == "group":
                        inner.update(c.node_keys())
                feeders = [c for c in col if c.kind == "node" and self.nbrs[c.key]
                           and all(k in inner for k in self.nbrs[c.key])]
                if feeders and len(feeders) < len(col):
                    # a bare node that only talks to a group in its own column goes left of it
                    it.columns.append(feeders)
                    col = [c for c in col if c not in feeders]
                it.columns.append(col)
        it.stacked = self.candidate == "C" and id(it) in self._deepest and len(it.columns) > 1
        if it.stacked:
            # candidate C: the deepest containers stack every child in one column, by rank; an
            # item that feeds the next rank goes last in its rank, so the chain stays adjacent
            stack = []
            for ci, col in enumerate(it.columns):
                nxt = set(k for c in (it.columns[ci + 1] if ci + 1 < len(it.columns) else [])
                          for k in c.node_keys())
                stack += sorted(col, key=lambda c: any(n in nxt for k in c.node_keys()
                                                       for n in self.nbrs[k]))
            content_w = max(c.w for c in stack)
            y = it.pads.get(0, 0)
            for c in stack:
                y += c.pre
                c.x, c.y = (content_w - c.w) / 2, y
                y += c.h + STACK_GAP
            content_h = y - STACK_GAP
        else:
            it.col_w = [max(c.w for c in col) for col in it.columns]
            it.col_x = []
            x = 0.0
            for ci, col in enumerate(it.columns):
                it.col_x.append(x)
                gap = max(COL_GAP + 16 * max(0, self._lanes(it, ci) - 3), snap(self._label_need(it, ci) + 4))
                x += it.col_w[ci] + gap
            content_w = x - gap if it.columns else 0
            content_h = 0.0
            for ci, col in enumerate(it.columns):
                y = it.pads.get(ci, 0)
                prev = None
                for c in col:
                    if prev is not None and self._linked(prev, c):
                        y += LINK_GAP
                    y += c.pre
                    c.x = it.col_x[ci] + (it.col_w[ci] - c.w) / 2
                    c.y = y
                    y += c.h + ROW_GAP
                    prev = c
                content_h = max(content_h, y - ROW_GAP)
        if it is self.root:
            it.w, it.h = content_w, content_h
            return
        title_w = text_width(it.label, FONT, bold=True) + (40 if it.gtype != "generic" else 8) + 8
        need = title_w - 2 * PAD
        inner_w = content_w if need <= content_w else content_w + 16 * -(-(need - content_w) // 16)
        it.title_bound = inner_w > content_w + 8
        if inner_w > content_w:
            shift = (inner_w - content_w) / 2
            for c in it.children:
                c.x += shift
        it.w = snap(inner_w + 2 * PAD + 0.49)
        it.h = snap(BAND + content_h + PAD + 0.49)

    def place(self, it: _Item, ox: float, oy: float, out: List[Tuple[_Item, float, float, int]],
              depth: int = 0) -> None:
        ax, ay = snap(ox + it.x), snap(oy + it.y)
        if it is self.root:
            ax, ay = ox, oy
        out.append((it, ax, ay, depth))
        if it.kind == "group":
            inner_x = ax + (PAD if it is not self.root else 0)
            inner_y = ay + (BAND if it is not self.root else 0)
            for c in it.children:
                self.place(c, inner_x, inner_y, out, depth + (0 if it is self.root else 1))

    def run(self, top: float) -> List[Tuple[_Item, float, float, int]]:
        placed: List[Tuple[_Item, float, float, int]] = []
        for sweep in range(16):
            self.measure(self.root)
            placed = []
            self.place(self.root, MARGIN, top, placed)
            self.pos = {it.key: (x + self.cell_w / 2, y + ICON / 2)
                        for it, x, y, _ in placed if it.kind == "node"}
            changed = self._reorder() if sweep < 2 else False
            changed = self._align() or changed
            if not changed:
                break
        return placed

    def _reorder(self) -> bool:
        """Barycentre ordering within each column (two sweeps), ties by fact order."""
        changed = False
        for it in self._walk(self.root):
            if it.kind != "group":
                continue
            for ci, col in enumerate(it.columns):
                def bary(c: _Item) -> Tuple[int, float, int]:
                    own = set(c.node_keys())
                    if own and own <= self.denied_only:
                        return (c.row, 2e9, c.order)
                    ys = [self.pos[n][1] for k in own for n in self.nbrs[k] if n not in own]
                    return (c.row, sum(ys) / len(ys) if ys else 1e9, c.order)
                new = sorted(col, key=bary)
                if [c.key for c in new] != [c.key for c in col]:
                    it.columns[ci] = new
                    changed = True
        return changed

    def _parent_map(self) -> Dict[int, _Item]:
        out: Dict[int, _Item] = {}
        for it in self._walk(self.root):
            for c in it.children:
                out[id(c)] = it
        return out

    def _shift(self, key: str, amount: float) -> bool:
        """Move node `key` down by `amount`: climb while the item is its container's only child
        (so a group moves as a whole instead of growing), then add space above that item."""
        parents = self._parent_map()
        item = next(it for it in self._walk(self.root) if it.kind == "node" and it.key == key)
        while True:
            par = parents.get(id(item))
            if par is None or par is self.root:
                break
            single_col = par.stacked or len(par.columns) == 1
            first = (par.columns and par.columns[0] and par.columns[0][0] is item) if single_col else False
            if not (len(par.children) == 1 or first):
                break
            item = par
        if item.pre + amount > CANVAS_MAX_H / 2:
            return False
        item.pre += amount
        return True

    def _ancestors(self, key: str) -> set:
        """Every scene group that holds node `key`, at any depth."""
        gparent = {g.id: g.parent for g in self.scene.groups}
        out: set = set()
        g = getattr(self._elem[key], "parent", None)
        while g is not None and g in gparent and g not in out:
            out.add(g)
            g = gparent[g]
        return out

    def _container_of(self, key: str) -> Tuple[_Item, int]:
        for it in self._walk(self.root):
            if it.kind != "group":
                continue
            for ci, col in enumerate(it.columns):
                for c in col:
                    if c.kind == "node" and c.key == key:
                        return it, ci
        raise KeyError(key)

    def _align(self) -> bool:
        """Push columns down so one-to-one arrows run straight and fan nodes sit at the median
        of their neighbours (pads only grow, so this terminates)."""
        outd: Dict[str, int] = {}
        ind: Dict[str, int] = {}
        for e in self.scene.edges:
            outd[e.src] = outd.get(e.src, 0) + 1
            ind[e.dst] = ind.get(e.dst, 0) + 1
        for n in self.scene.nodes:
            tgt = getattr(n, "align", None)
            if tgt is None or tgt not in self.pos:
                continue
            dy = snap(self.pos[tgt][1] - self.pos[n.id][1])
            if dy >= GRID and self._shift(n.id, dy):
                return True
            if dy <= -GRID and self._shift(tgt, -dy):
                return True
        for e in self.scene.edges:
            if e.id in self.back or outd.get(e.src) != 1 or ind.get(e.dst) != 1:
                continue
            (ax, ay), (bx, by) = self.pos[e.src], self.pos[e.dst]
            if abs(ax - bx) < 1:
                continue
            dy = snap(ay - by)
            target = e.dst if dy > 0 else e.src
            amount = abs(dy)
            if amount == 0:
                continue
            if self._shift(target, amount):
                return True
        # a fan-out target a little below its source, in another column, can be neither a
        # straight run nor a one-bend L into its top face (the run would graze the target, or
        # need a short Z): push it down until the L fits (review DR2-01)
        l_room = ICON / 2 + 32
        for e in self.scene.edges:
            if e.id in self.back or outd.get(e.src, 0) < 2 or ind.get(e.dst) != 1:
                continue
            (ax, ay), (bx, by) = self.pos[e.src], self.pos[e.dst]
            if abs(ax - bx) < ICON + 32:
                continue
            # only when the target sits loose in a boundary the source is also in: a target
            # inside a group of its own would be entered through that group's title band
            dpar = getattr(self._elem[e.dst], "parent", None)
            if dpar is not None and dpar not in self._ancestors(e.src):
                continue
            dy = by - ay
            if GRID <= dy < l_room and self._shift(e.dst, snap(l_room - dy)):
                return True
        for n in self.scene.nodes:
            nb = [self.pos[k][1] for k in self.nbrs[n.id] if abs(self.pos[k][0] - self.pos[n.id][0]) > 1]
            if not nb:
                continue
            nb.sort()
            med = nb[len(nb) // 2] if len(nb) % 2 else (nb[len(nb) // 2 - 1] + nb[len(nb) // 2]) / 2
            dy = snap(med - self.pos[n.id][1])
            if dy >= GRID:
                cont, ci = self._container_of(n.id)
                if len(cont.columns[ci]) == 1 and self._shift(n.id, dy):
                    return True
        return False


def _move(n: NodeGeo, dx: float, dy: float) -> NodeGeo:
    def mv(b: Box) -> Box:
        return Box(b.x + dx, b.y + dy, b.w, b.h)
    return NodeGeo(n.gid, n.key, n.label, n.lines, n.truncated, mv(n.icon), mv(n.caption),
                   mv(n.cell), n.icon_key, n.parent, n.optional)


def _drop_denied_callers(nodes: List[NodeGeo], groups: List[GroupGeo], eng: "_Engine",
                         scene: Scene) -> None:
    """A top-level caller whose only arrows are denied calls goes below every group, under its
    single target, so the denied arrow is one straight run from outside every boundary (it never
    reads as traffic inside a network it does not belong to)."""
    if not groups:
        return
    bottom = max(g.box.b for g in groups)
    for i, n in enumerate(nodes):
        if n.key not in eng.denied_only or n.parent is not None:
            continue
        targets = {e.dst for e in scene.edges if e.src == n.key}
        if len(targets) != 1:
            continue
        t = next((m for m in nodes if m.key in targets), None)
        if t is None:
            continue
        moved = _move(n, snap(t.cell.x - n.cell.x), snap(bottom + STACK_GAP - n.cell.y))
        if any(m is not n and moved.cell.overlaps(m.cell) for m in nodes):
            continue
        if any(moved.cell.overlaps(g.box) for g in groups):
            continue
        nodes[i] = moved


def build_layout(scene: Scene, candidate: str = "A", icon_keys: Optional[Dict[str, str]] = None
                 ) -> Layout:
    """Geometry for the scene (no edges yet; route.route_edges adds them)."""
    title_text = scene.title or scene.topic_title
    top = MARGIN + (TITLE_FONT + 16 if title_text else 0)
    eng = _Engine(scene, candidate)
    placed = eng.run(top)
    nodes: List[NodeGeo] = []
    groups: List[GroupGeo] = []
    labels = {n.id: n for n in scene.nodes}
    gelems = {g.id: g for g in scene.groups}
    parent_of: Dict[str, Optional[str]] = {}

    def walk(it: _Item, parent: Optional[str]):
        for c in it.children:
            parent_of[c.key] = parent
            if c.kind == "group":
                walk(c, c.key)

    walk(eng.root, None)
    gi = ni = 0
    for it, x, y, depth in placed:
        if it is eng.root:
            continue
        if it.kind == "group":
            gi += 1
            g = gelems[it.key]
            box = Box(x, y, it.w, it.h)
            has_icon = g.type != "generic"
            icon_box = Box(x + 8, y + 8, GROUP_ICON, GROUP_ICON) if has_icon else None
            tx = x + (40 if has_icon else 8)
            tw = text_width(g.label, FONT, bold=True)
            groups.append(GroupGeo(f"g{gi}", g.id, g.type, g.label, box,
                                   Box(tx, y + 12, tw, LINE_H), icon_box, parent_of.get(it.key),
                                   depth, it.title_bound))
        else:
            ni += 1
            n = labels[it.key]
            cw = eng.cell_w
            icon = Box(x + (cw - ICON) / 2, y, ICON, ICON)
            lines, trunc = wrap(n.label, max(cw - 8, 104))
            lw = max(text_width(ln) for ln in lines)
            cap = Box(icon.cx - lw / 2, y + ICON + 6, lw, LINE_H * len(lines))
            nodes.append(NodeGeo(f"n{ni}", n.id, n.label, lines, trunc, icon, cap,
                                 Box(x, y, cw, CELL_H), (icon_keys or {}).get(n.id, n.icon),
                                 parent_of.get(it.key), bool(n.optional)))
    _drop_denied_callers(nodes, groups, eng, scene)
    content_r = max([g.box.r for g in groups] + [n.cell.r for n in nodes])
    content_b = max([g.box.b for g in groups] + [n.cell.b for n in nodes])
    lay = Layout(0, 0, candidate, nodes, groups)
    lay.edges = []
    if title_text:
        lay.title = TextGeo(title_text, Box(MARGIN, MARGIN, text_width(title_text, TITLE_FONT, True),
                                            TITLE_FONT + 4), TITLE_FONT, True)
    lay._content = (content_r, content_b)  # type: ignore[attr-defined]
    lay._back = eng.back  # type: ignore[attr-defined]
    lay._title_text = title_text  # type: ignore[attr-defined]
    return lay


def finish_canvas(lay: Layout, scene: Scene) -> None:
    """Title, legend, caption and the canvas size, after routing (edges may extend content)."""
    content_r, content_b = lay._content  # type: ignore[attr-defined]
    for e in lay.edges:
        for (x, y) in e.points:
            content_r, content_b = max(content_r, x), max(content_b, y)
        if e.label_box:
            content_r, content_b = max(content_r, e.label_box.r), max(content_b, e.label_box.b)
    title_text = lay._title_text  # type: ignore[attr-defined]
    styles = []
    styles.append(("solid", LEGEND_SOLID))
    if any(e.optional and e.style != "denied" for e in lay.edges):
        styles.append(("dashed", LEGEND_DASHED))
    if any(e.style == "denied" for e in lay.edges):
        styles.append(("denied", LEGEND_DENIED))
    if any(n.optional for n in lay.nodes):
        styles.append(("outline", LEGEND_OUTLINE))
    leg_w = max(text_width(t) for _, t in styles) + 40
    cap_w = text_width(CAPTION)
    width = snap(max(content_r + MARGIN, leg_w + 2 * MARGIN, cap_w + 2 * MARGIN) + 4)
    title_need = 0.0
    if title_text:
        title_need = text_width(title_text, TITLE_FONT, bold=True) + 2 * MARGIN
        lay.title_sets_width = max(0.0, title_need - width)
        width = max(width, snap(title_need + 4))
        lay.title = TextGeo(title_text, Box(MARGIN, MARGIN, text_width(title_text, TITLE_FONT, True),
                                            TITLE_FONT + 4), TITLE_FONT, True)
    y = snap(content_b + LEGEND_GAP)
    lx = width - MARGIN - leg_w
    lay.legend = [TextGeo(LEGEND_TITLE, Box(lx, y, text_width(LEGEND_TITLE, FONT, True), LINE_H),
                          FONT, True)]
    yy = y + LINE_H + 4
    for kind, text in styles:
        lay.legend_lines.append((kind, (lx, yy + LINE_H / 2, lx + 28, yy + LINE_H / 2)))
        lay.legend.append(TextGeo(text, Box(lx + 36, yy, text_width(text), LINE_H)))
        yy += LINE_H + 4
    lay.legend_box = Box(lx, y, leg_w, yy - y)
    cy = yy + 8
    lay.caption = TextGeo(CAPTION, Box(width - MARGIN - cap_w, cy, cap_w, LINE_H), FONT,
                          color="secondary")
    lay.width = int(width)
    lay.height = int(snap(cy + LINE_H + MARGIN + 4))


# -- candidate loop ----------------------------------------------------------------------------

@dataclass
class Rendered:
    layout: Layout
    quality: object
    attempts: int
    timed_out: bool = False


def without_hints(scene: Scene) -> Scene:
    """A copy of the scene whose elements carry no `col`, `row` or `align` hint (generic and
    flow views, design D6.3). The elements are copied, so the caller's scene is unchanged."""
    import copy as _copy

    def strip(e):
        c = _copy.copy(e)
        c.col = c.row = c.align = None
        return c

    out = _copy.copy(scene)
    out.groups = [strip(e) for e in scene.groups]
    out.nodes = [strip(e) for e in scene.nodes]
    out.edges = [strip(e) for e in scene.edges]
    return out


def best_layout(scene: Scene, *, pass_score: float, max_attempts: int, deadline: float,
                clock: Callable[[], float] = time.monotonic,
                icon_warnings: Optional[Dict[str, str]] = None,
                hints_enabled: bool = True) -> Optional[Rendered]:
    """Try candidates A, B, C (bounded) and keep the best by the "improves" rule: a higher score
    with no hard count increased. Stops at the first passing candidate or at the deadline.
    `hints_enabled=False` ignores every `col`, `row` and `align` hint (the algorithm is
    otherwise unchanged)."""
    from .quality import check, improves
    from .route import route_edges
    if not hints_enabled:
        scene = without_hints(scene)
    best: Optional[Rendered] = None
    attempts = 0
    for cand in ("A", "B", "C")[:max_attempts]:
        if clock() > deadline:
            if best is not None:
                best.timed_out = True
            break
        attempts += 1
        lay = build_layout(scene, cand)
        route_edges(lay, scene)
        finish_canvas(lay, scene)
        q = check(lay, pass_score=pass_score, icon_warnings=icon_warnings or {})
        if best is None or improves(q, best.quality):
            best = Rendered(lay, q, attempts)
        best.attempts = attempts
        if q.passed:
            break
    return best
