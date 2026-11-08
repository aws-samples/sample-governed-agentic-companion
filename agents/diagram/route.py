"""Orthogonal edge routing and edge-label placement (design D6.4).

Each edge is routed in scene order against the icons, captions, group title bands and the edges
already routed. Candidate routes (straight, L, and three-run routes over lanes 8 px apart, with
ports 16 px apart on each icon side) are scored by the same rules the quality check measures,
and the cheapest wins. Deterministic: candidates are generated and compared in a fixed order.

A port on the south side sits below the node's caption (the node box is icon plus caption), so a
run never crosses its own caption.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .compose import Scene
from .layout import Box, EdgeGeo, FONT, GRID, LINE_H, Layout, NodeGeo, text_width

Point = Tuple[float, float]
Seg = Tuple[Point, Point]

STUB = 32
NEAR = 10
#: parallel runs of two edges closer than this read as one doubled line
RISER_GAP = 24
PORT_STEP = 16
_SLOTS = (0, -PORT_STEP, PORT_STEP)


def snap8(v: float) -> int:
    return int(round(v / GRID)) * GRID


def segments(points: Sequence[Point]) -> List[Seg]:
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def seg_hits_box(s: Seg, b: Box) -> bool:
    (x1, y1), (x2, y2) = s
    if y1 == y2:
        lo, hi = min(x1, x2), max(x1, x2)
        return b.y < y1 < b.b and lo < b.r and b.x < hi
    lo, hi = min(y1, y2), max(y1, y2)
    return b.x < x1 < b.r and lo < b.b and b.y < hi


def _inside_len(s: Seg, b: Box) -> float:
    """Length of an axis-aligned segment strictly inside box b."""
    (x1, y1), (x2, y2) = s
    if y1 == y2:
        if not b.y < y1 < b.b:
            return 0.0
        return max(0.0, min(max(x1, x2), b.r) - max(min(x1, x2), b.x))
    if not b.x < x1 < b.r:
        return 0.0
    return max(0.0, min(max(y1, y2), b.b) - max(min(y1, y2), b.y))


def _hugs(s: Seg, b: Box, near: float = 10) -> int:
    """1 when a segment runs along a border of b within `near` px (reads as part of the box)."""
    (x1, y1), (x2, y2) = s
    if y1 == y2:
        lo, hi = max(min(x1, x2), b.x), min(max(x1, x2), b.r)
        return int(hi - lo > 16 and (abs(y1 - b.y) < near or abs(y1 - b.b) < near))
    lo, hi = max(min(y1, y2), b.y), min(max(y1, y2), b.b)
    return int(hi - lo > 16 and (abs(x1 - b.x) < near or abs(x1 - b.r) < near))


def _dir(s: Seg) -> Tuple[int, int]:
    (x1, y1), (x2, y2) = s
    return ((x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1))


def _zigzag(segs: List[Seg]) -> bool:
    """Three runs whose outer runs point the same way, with a 12 to 180 px step (a Z)."""
    if len(segs) != 3:
        return False
    return _dir(segs[0]) == _dir(segs[2]) and 12 <= seg_len(segs[1]) <= 180


def seg_len(s: Seg) -> float:
    (x1, y1), (x2, y2) = s
    return abs(x2 - x1) + abs(y2 - y1)


def seg_cross(a: Seg, b: Seg) -> bool:
    """Proper crossing of a horizontal and a vertical segment (not at endpoints)."""
    (ax1, ay1), (ax2, ay2) = a
    (bx1, by1), (bx2, by2) = b
    ah, bh = ay1 == ay2, by1 == by2
    if ah == bh:
        return False
    if not ah:
        (ax1, ay1), (ax2, ay2), (bx1, by1), (bx2, by2) = (bx1, by1), (bx2, by2), (ax1, ay1), (ax2, ay2)
    x, y = bx1, ay1
    return (min(ax1, ax2) < x < max(ax1, ax2)) and (min(by1, by2) < y < max(by1, by2))


def parallel_overlap(a: Seg, b: Seg) -> Tuple[float, float]:
    """(distance, shared length) for two parallel segments; (inf, 0) otherwise."""
    (ax1, ay1), (ax2, ay2) = a
    (bx1, by1), (bx2, by2) = b
    if ay1 == ay2 and by1 == by2 and ax1 != ax2 and bx1 != bx2:
        lo = max(min(ax1, ax2), min(bx1, bx2))
        hi = min(max(ax1, ax2), max(bx1, bx2))
        return abs(ay1 - by1), max(0.0, hi - lo)
    if ax1 == ax2 and bx1 == bx2 and ay1 != ay2 and by1 != by2:
        lo = max(min(ay1, ay2), min(by1, by2))
        hi = min(max(ay1, ay2), max(by1, by2))
        return abs(ax1 - bx1), max(0.0, hi - lo)
    return float("inf"), 0.0


def _touch(a: Seg, b: Seg) -> bool:
    """Collinear segments of two edges that meet or nearly meet end to end (reads as one line)."""
    (ax1, ay1), (ax2, ay2) = a
    (bx1, by1), (bx2, by2) = b
    if ay1 == ay2 == by1 == by2:
        lo1, hi1, lo2, hi2 = min(ax1, ax2), max(ax1, ax2), min(bx1, bx2), max(bx1, bx2)
    elif ax1 == ax2 == bx1 == bx2:
        lo1, hi1, lo2, hi2 = min(ay1, ay2), max(ay1, ay2), min(by1, by2), max(by1, by2)
    else:
        return False
    return lo2 - hi1 <= 8 and lo1 - hi2 <= 8


def node_box(n: NodeGeo) -> Box:
    """Icon plus caption, the node's footprint for layout checks."""
    x = min(n.icon.x, n.caption.x)
    r = max(n.icon.r, n.caption.r)
    return Box(x, n.icon.y, r - x, n.caption.b - n.icon.y)


def icon_zone(n: NodeGeo) -> Box:
    """The icon, 2 px wider on each side, extended down to the caption: a run inside it cuts
    the node (a run along the icon's bottom edge, between icon and caption, counts)."""
    i = n.icon
    return Box(i.x - 2, i.y - 2, i.w + 4, max(i.h + 4, n.caption.y + 1 - (i.y - 2)))


def cuts_own(points: Sequence[Point], n: NodeGeo, is_src: bool, is_dst: bool) -> bool:
    """True when a run of the edge, other than the run leaving (src) or entering (dst) the
    node's port, enters the node's icon zone: the arrow loops back through its own end."""
    segs = segments(points)
    for k, s in enumerate(segs):
        if (is_src and k == 0) or (is_dst and k == len(segs) - 1):
            continue
        if seg_hits_box(s, icon_zone(n)):
            return True
    return False


def grazes_own(points: Sequence[Point], n: NodeGeo, is_src: bool, is_dst: bool,
               near: float = NEAR) -> bool:
    """True when a run of the edge, other than its port run at that end, passes within `near`
    px of the node's icon (or of the dashed opt-in outline drawn 4 px around it): the arrow runs
    along its own end and reads as a doubled border before it turns in (review DR2-01)."""
    segs = segments(points)
    zone = n.icon.inflate(near)
    for k, s in enumerate(segs):
        if (is_src and k == 0) or (is_dst and k == len(segs) - 1):
            continue
        if seg_hits_box(s, zone):
            return True
    return False


#: a south port sits this far below the caption, so an arrowhead entering from below keeps clear
#: of the caption text (DQ-L109 measures 6 px)
SOUTH_CLEAR = 8


def _port(n: NodeGeo, side: str, off: float) -> Point:
    i = n.icon
    if side == "E":
        return (i.r, i.cy + off)
    if side == "W":
        return (i.x, i.cy + off)
    if side == "N":
        return (i.cx + off, i.y)
    return (i.cx + off, n.caption.b + SOUTH_CLEAR)


def _lanes(lo: float, hi: float, prefer: Sequence[float]) -> List[float]:
    if hi < lo:
        return []
    start = int(-(-lo // GRID)) * GRID
    xs = list(range(int(start), int(hi) + 1, GRID))
    if len(xs) > 14:
        step = len(xs) / 14
        picked = sorted({xs[int(i * step)] for i in range(14)} | {xs[-1]})
        xs = picked
    xs = sorted(set(xs), key=lambda v: (min(abs(v - p) for p in prefer) if prefer else 0, v))
    return xs


class _Router:
    def __init__(self, lay: Layout):
        self.lay = lay
        self.tiles = {n.key: n.icon for n in lay.nodes}
        self.by_key = {n.key: n for n in lay.nodes}
        self.captions = {n.key: n.caption for n in lay.nodes}
        if lay.title is not None:
            self.captions["__title__"] = lay.title.box
        # a group's title band is its icon tile and its title text: a run through either
        # reads as crossing the boundary's label
        self.bands = [g.title_box.inflate(4) for g in lay.groups] + \
            [g.icon_box.inflate(4) for g in lay.groups if g.icon_box is not None]
        self.routed: List[Tuple[str, List[Seg]]] = []
        self.ends: Dict[str, Tuple[str, str]] = {}
        self.slots: Dict[Tuple[str, str], List[float]] = {}
        gparent = {g.key: g.parent for g in lay.groups}
        self.gboxes = {g.key: g.box for g in lay.groups}
        self.anc: Dict[str, set] = {}
        for n in lay.nodes:
            chain, g = set(), n.parent
            while g is not None and g not in chain:
                chain.add(g)
                g = gparent.get(g)
            self.anc[n.key] = chain
        self.style = "data"

    def _free(self, key: str, side: str, role: str) -> List[float]:
        """Port offsets on one icon side: unused slots, plus a slot already used by an edge in
        the same role: a fan-out may share its start point, as in the published figures (check
        L116 exempts fans). Fan-ins keep separate end points so each arrowhead stays readable."""
        used = self.slots.get((key, side), [])
        same = [off for off, r in used if r == role == "out"]
        taken = [off for off, _r in used]
        free = [s for s in _SLOTS if s not in taken]
        out = (free[:2] if free else []) + [o for o in same[:1] if o not in free[:2]]
        return out or [0.0]

    def cost(self, pts: List[Point], src: str, dst: str, dst_side: str) -> float:
        segs = segments(pts)
        c = 0.0
        for s in segs:
            if seg_len(s) == 0:
                c += 5000
            for key, tile in self.tiles.items():
                if key in (src, dst):
                    if seg_hits_box(s, tile.inflate(-1)):
                        c += 4000
                    continue
                if seg_hits_box(s, tile):
                    c += 4000
                elif seg_hits_box(s, tile.inflate(NEAR)):
                    c += 400
            for key, cap in self.captions.items():
                if seg_hits_box(s, cap.inflate(2)):
                    c += 600
            for band in self.bands:
                if seg_hits_box(s, band.inflate(8)):
                    c += 500
            for _k, other in self.routed:
                ends = self.ends.get(_k, ("", ""))
                fan = bool({src, dst} & set(ends))
                same_role = ends[0] == src
                for o in other:
                    if seg_cross(s, o):
                        c += 700
                    d, shared = parallel_overlap(s, o)
                    if same_role and d == 0:
                        continue
                    if d <= 12 and shared > 16:
                        c += 1500 if d <= 6 else 900
                    elif d < RISER_GAP and shared > 16 and not fan:
                        c += 500
                    elif d == 0 and _touch(s, o):
                        c += 900
        for key, is_src in ((src, True), (dst, False)):
            n = self.by_key.get(key)
            if n is not None and cuts_own(pts, n, is_src, not is_src):
                c += 4000
            elif n is not None and grazes_own(pts, n, is_src, not is_src):
                c += 400
        if len(segs) > 1:
            if seg_len(segs[0]) < STUB or seg_len(segs[-1]) < STUB:
                c += 300
        if len(segs) == 3 and seg_len(segs[1]) < 12:
            c += 300
        if _zigzag(segs):
            c += 800
        if dst_side == "S" and len(segs) > 1:
            c += 120
        # a run inside a group that holds neither end reads as traffic inside that boundary
        foreign = [b for k, b in self.gboxes.items()
                   if k not in self.anc.get(src, set()) and k not in self.anc.get(dst, set())]
        weight = 6.0 if self.style == "denied" else 1.5
        for s in segs:
            for b in self.gboxes.values():
                c += 300 * _hugs(s, b)
        for s in segs:
            for b in foreign:
                c += weight * _inside_len(s, b.inflate(-2))
        c += sum(seg_len(s) for s in segs) * 0.25 + 30 * (len(segs) - 1) + (200 if len(segs) > 3 else 0)
        return c

    def _detours(self, sa: str, sb: str, pa: Point, pb: Point) -> List[List[Point]]:
        """Routes that leave sideways, run along a horizontal lane above or below the obstacles,
        and come back: used only when the direct shapes are blocked (four bends cost more)."""
        if sa not in "EW":
            return []
        (x1, y1), (x2, y2) = pa, pb
        xa = x1 + STUB if sa == "E" else x1 - STUB
        ys = sorted({snap8(v) for n in self.lay.nodes for v in (n.icon.y - 20, n.caption.b + 16)})
        out: List[List[Point]] = []
        for yl in ys:
            if yl in (y1, y2):
                continue
            if sb in "EW":
                xb = x2 - STUB if sb == "W" else x2 + STUB
                out.append([pa, (xa, y1), (xa, yl), (xb, yl), (xb, y2), pb])
            elif (sb == "N" and yl < y2 - STUB) or (sb == "S" and yl > y2 + STUB):
                out.append([pa, (xa, y1), (xa, yl), (x2, yl), pb])
        return out

    def candidates(self, a: NodeGeo, b: NodeGeo) -> List[Tuple[List[Point], str, str]]:
        out: List[Tuple[List[Point], str, str]] = []
        sides = ("E", "W", "N", "S")
        for sa in sides:
            for sb in sides:
                for oa in self._free(a.key, sa, "out"):
                    for ob in self._free(b.key, sb, "in"):
                        pa, pb = _port(a, sa, oa), _port(b, sb, ob)
                        for path in self._shapes(sa, sb, pa, pb):
                            out.append((path, sa, sb))
                        for path in self._detours(sa, sb, pa, pb):
                            out.append((path, sa, sb))
        return out

    @staticmethod
    def _shapes(sa: str, sb: str, pa: Point, pb: Point) -> List[List[Point]]:
        (x1, y1), (x2, y2) = pa, pb
        out: List[List[Point]] = []
        if sa in "EW" and sb in "EW":
            if y1 == y2 and ((sa == "E" and sb == "W" and x2 - x1 >= 24)
                             or (sa == "W" and sb == "E" and x1 - x2 >= 24)):
                out.append([pa, pb])
            if sa == "E" and sb == "W":
                lanes = _lanes(x1 + STUB, x2 - STUB, [x2 - STUB, x1 + STUB])
            elif sa == "W" and sb == "E":
                lanes = _lanes(x2 + STUB, x1 - STUB, [x2 + STUB, x1 - STUB])
            elif sa == "E" and sb == "E":
                m = max(x1, x2) + STUB
                lanes = _lanes(m, m + 96, [m])
            else:
                m = min(x1, x2) - STUB
                lanes = _lanes(m - 96, m, [m])
            if y1 != y2:
                for x in lanes:
                    out.append([pa, (x, y1), (x, y2), pb])
        elif sa in "EW" and sb in "NS":
            ok_x = (sa == "E" and x2 >= x1 + STUB) or (sa == "W" and x2 <= x1 - STUB)
            ok_y = (sb == "N" and y2 >= y1 + STUB) or (sb == "S" and y2 <= y1 - STUB)
            if ok_x and ok_y:
                out.append([pa, (x2, y1), pb])
        elif sa in "NS" and sb in "EW":
            ok_y = (sa == "S" and y2 >= y1 + STUB) or (sa == "N" and y2 <= y1 - STUB)
            ok_x = (sb == "W" and x2 >= x1 + STUB) or (sb == "E" and x2 <= x1 - STUB)
            if ok_x and ok_y:
                out.append([pa, (x1, y2), pb])
        else:
            if x1 == x2 and ((sa == "S" and sb == "N" and y2 - y1 >= 24)
                             or (sa == "N" and sb == "S" and y1 - y2 >= 24)):
                out.append([pa, pb])
            if sa == "S" and sb == "N":
                lanes = _lanes(y1 + STUB, y2 - STUB, [y1 + STUB])
            elif sa == "N" and sb == "S":
                lanes = _lanes(y2 + STUB, y1 - STUB, [y1 - STUB])
            elif sa == "S" and sb == "S":
                m = max(y1, y2) + STUB
                lanes = _lanes(m, m + 96, [m])
            else:
                m = min(y1, y2) - STUB
                lanes = _lanes(m - 96, m, [m])
            if x1 != x2:
                for y in lanes:
                    out.append([pa, (x1, y), (x2, y), pb])
        return out

    def route(self, a: NodeGeo, b: NodeGeo) -> List[Point]:
        best: Optional[Tuple[float, int, List[Point], str, str]] = None
        for i, (pts, sa, sb) in enumerate(self.candidates(a, b)):
            c = self.cost(pts, a.key, b.key, sb)
            if best is None or c < best[0]:
                best = (c, i, pts, sa, sb)
        if best is None:  # cannot happen: a W/E or E/W pair always yields a route
            raise RuntimeError("no route candidate")
        _c, _i, pts, sa, sb = best
        for key, side, p in ((a.key, sa, pts[0]), (b.key, sb, pts[-1])):
            n = a if key == a.key else b
            centre = _port(n, side, 0)
            off = (p[1] - centre[1]) if side in "EW" else (p[0] - centre[0])
            role = "out" if key == a.key else "in"
            self.slots.setdefault((key, side), []).append((off, role))
        return pts


def _label_candidates(points: List[Point], w: float, h: float) -> List[Box]:
    """Label boxes along every segment (centre first, then outwards in 8 px steps), 3 px clear."""
    segs = sorted(segments(points), key=lambda s: -seg_len(s))
    out: List[Box] = []
    for (x1, y1), (x2, y2) in segs:
        horiz = y1 == y2
        length = abs(x2 - x1) if horiz else abs(y2 - y1)
        need = (w if horiz else h) + 8
        if length < need:
            continue
        lo = (min(x1, x2) if horiz else min(y1, y2)) + need / 2
        hi = (max(x1, x2) if horiz else max(y1, y2)) - need / 2
        mid = (lo + hi) / 2
        offs = [0.0]
        k = 8.0
        while mid - k >= lo or mid + k <= hi:
            if mid - k >= lo:
                offs.append(-k)
            if mid + k <= hi:
                offs.append(k)
            k += 8
        for o in offs:
            c = mid + o
            if horiz:
                out.append(Box(c - w / 2, y1 - 3 - h, w, h))
                out.append(Box(c - w / 2, y1 + 3, w, h))
            else:
                out.append(Box(x1 + 4, c - h / 2, w, h))
                out.append(Box(x1 - 4 - w, c - h / 2, w, h))
    return out


def label_conflicts(box: Box, lay: Layout, self_edge: Optional[EdgeGeo]) -> int:
    n = 0
    for nd in lay.nodes:
        if box.overlaps(nd.icon.inflate(2)) or box.overlaps(nd.caption.inflate(2)):
            n += 1
    for g in lay.groups:
        if box.overlaps(g.title_box.inflate(6)) or box.overlaps(Box(g.box.x, g.box.y, g.box.w, 28)):
            n += 1
        b = g.box
        inside = b.contains(box)
        outside = not box.overlaps(b)
        if not inside and not outside:
            n += 1
    for e in lay.edges:
        if e is self_edge:
            continue
        if e.label_box is not None and box.overlaps(e.label_box.inflate(2)):
            n += 1
        for s in segments(e.points):
            if seg_hits_box(s, box.inflate(1)):
                n += 1
    if self_edge is not None:
        for s in segments(self_edge.points):
            if seg_hits_box(s, box.inflate(1)):
                n += 1
    return n


def route_edges(lay: Layout, scene: Scene) -> None:
    router = _Router(lay)
    back = getattr(lay, "_back", set())
    ei = 0
    by_key = {n.key: n for n in lay.nodes}
    for e in scene.edges:
        ei += 1
        a, b = by_key[e.src], by_key[e.dst]
        router.style = e.style
        pts = router.route(a, b)
        router.routed.append((e.id, segments(pts)))
        router.ends[e.id] = (e.src, e.dst)
        lay.edges.append(EdgeGeo(f"e{ei}", e.id, e.src, e.dst, pts, e.optional, e.style,
                                 e.bidirectional, e.label, None, e.id in back))
    for eg in lay.edges:
        if not eg.label:
            continue
        w, h = text_width(eg.label, FONT) + 4, LINE_H
        best: Optional[Tuple[int, int, Box]] = None
        for i, box in enumerate(_label_candidates(eg.points, w, h)):
            k = label_conflicts(box, lay, eg)
            if best is None or k < best[0]:
                best = (k, i, box)
            if k == 0:
                break
        if best is None:
            (x1, y1), (x2, y2) = max(segments(eg.points), key=seg_len)
            best = (99, 0, Box((x1 + x2) / 2 - w / 2, min(y1, y2) - 3 - h, w, h))
        eg.label_box = best[2]
