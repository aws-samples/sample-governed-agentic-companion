"""Deterministic post-layout quality check (design D14).

Measured on the exact geometry the layout produced, at the README place width (880 px; the canvas
itself is at most 992 px wide, so 13 px text shows at 11.5 px or more). The kit's own rule table
is MESSAGES (every code the check can emit, with its dimension and message) plus NOT_APPLICABLE
(rule ids this model cannot trigger, with the reason). The rules are the ones listed in
docs/ARCHITECTURE.md ("Diagram quality rules"): official icons and group
boundaries, arrows that follow the data, a legend for every line style, legible text at README
width, no overlapping or crossing arrows, short routes and even padding.

Score = 100 - 15 per FAIL - 6 per FIX - 1.5 per WARN (WARN total capped at 15), floored at 0,
rounded to 0.1. Hard = any layout FAIL or FIX, any paths FAIL or FIX (edge crossings are FIX),
any legibility FAIL or FIX. Pass = no hard finding and score >= the pass mark (85 default,
tighten-only).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .layout import BAND, Box, FONT, TITLE_AREA, Layout
from .route import (_zigzag, cuts_own, grazes_own, node_box, parallel_overlap, seg_cross, seg_hits_box,
                    seg_len, segments)

PLACE_WIDTH = 880
BLOCKER_TEXT, MIN_TEXT, MIN_ICON, SMALL_ICON, MIN_CAPTION = 6, 8, 24, 32, 11
WARN_CAP = 15.0

#: code -> (dimension, fixed message of at most 48 characters)
MESSAGES: Dict[str, Tuple[str, str]] = {
    "DQ-P101": ("paths", "edge crosses icon"),
    "DQ-P102": ("paths", "zero-length segment"),
    "DQ-L101": ("layout", "nodes overlap"),
    "DQ-L102": ("layout", "node straddles group border"),
    "DQ-L103": ("layout", "edge label on group border"),
    "DQ-L104": ("layout", "rows share a vertical band"),
    "DQ-L105": ("layout", "uneven spacing in row"),
    "DQ-L106": ("layout", "node half beside a group"),
    "DQ-L107": ("layout", "aspect ratio past 2.5:1"),
    "DQ-L108": ("layout", "long arrow or sparse figure"),
    "DQ-L109": ("layout", "arrowhead near text"),
    "DQ-L110": ("layout", "uneven sibling boxes"),
    "DQ-L111": ("layout", "empty space or uneven gaps"),
    "DQ-L112": ("layout", "short elbow stub or jog"),
    "DQ-L113": ("layout", "numbered edge label"),
    "DQ-L114": ("layout", "zig-zag arrow"),
    "DQ-L115": ("layout", "canvas wider than 1280 px"),
    "DQ-L116": ("layout", "edges share a line"),
    "DQ-L117": ("layout", "edge runs through text"),
    "DQ-L118": ("layout", "edge too close to a group title"),
    "DQ-L119": ("layout", "avoidable bend"),
    "DQ-L120": ("layout", "edge runs close to an icon"),
    "DQ-L121": ("layout", "edge label overlaps icon or text"),
    "DQ-L122": ("layout", "arrowheads collide"),
    "DQ-L123": ("layout", "stacked subnets differ in width"),
    "DQ-L124": ("layout", "title sets the canvas width"),
    "DQ-L125": ("layout", "edges run side by side"),
    "DQ-L126": ("layout", "short arrow"),
    "DQ-R101": ("legibility", "text unreadable at README width"),
    "DQ-R102": ("legibility", "text too small at README width"),
    "DQ-R103": ("legibility", "icon too small at README width"),
    "DQ-R104": ("legibility", "icon small at README width"),
    "DQ-A101": ("legibility", "caption under 11 px at README width"),
    "DQ-A102": ("layout", "uneven group padding"),
    "DQ-A103": ("layout", "canvas wider than 992 px"),
    "DQ-G101": ("layout", "groups overlap"),
    "DQ-LG101": ("layout", "line style missing from legend"),
    "DQ-X101": ("paths", "edges cross"),
    "DQ-T101": ("layout", "caption truncated"),
    "DQ-I101": ("layout", "generic icon used"),
    "DQ-I103": ("layout", "icon failed its checksum"),
}

#: rule ids that cannot occur in this model, with the reason (never emitted).
NOT_APPLICABLE = {
    "L190": "poster figure: the canvas is capped at 992 px",
    "L191": "unparsed coordinates: geometry is numeric, nothing is parsed",
    "R105": "tall figure for a document page: README is a screen column",
    "R106": "printed-page target text: only page places define it",
    "L199": "catch-all for unclassified layout text: none emitted",
    "P199": "catch-all for unclassified path text: none emitted",
}


@dataclass(frozen=True)
class QFinding:
    code: str
    severity: str        # fail | fix | warn
    element: str = ""    # generated id (n3, e2, g1) or ""

    @property
    def dimension(self) -> str:
        return MESSAGES[self.code][0]

    @property
    def message(self) -> str:
        return MESSAGES[self.code][1]

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "element": self.element,
                "message": self.message}


@dataclass
class QualityResult:
    score: float
    passed: bool
    threshold: float
    findings: List[QFinding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def hard(self) -> List[QFinding]:
        return [f for f in self.findings if _is_hard(f)]

    def hard_counts(self) -> Dict[str, int]:
        out = {"layout": 0, "paths": 0, "legibility": 0}
        for f in self.hard:
            out[f.dimension] += 1
        return out


def _is_hard(f: QFinding) -> bool:
    return f.severity in ("fail", "fix")


def score_of(findings: List[QFinding]) -> float:
    fails = sum(1 for f in findings if f.severity == "fail")
    fixes = sum(1 for f in findings if f.severity == "fix")
    warns = sum(1 for f in findings if f.severity == "warn")
    s = 100 - 15 * fails - 6 * fixes - min(WARN_CAP, 1.5 * warns)
    return round(max(0.0, s), 1)


def improves(new: QualityResult, old: QualityResult) -> bool:
    if new.score <= old.score:
        return False
    nh, oh = new.hard_counts(), old.hard_counts()
    return all(nh[k] <= oh[k] for k in nh)


# -- helpers -----------------------------------------------------------------------------------

def _tips(lay: Layout) -> List[Tuple[str, Tuple[float, float], Tuple[float, float]]]:
    """(edge id, tip point, unit direction) for every arrowhead."""
    out = []
    for e in lay.edges:
        p = e.points
        if len(p) < 2:
            continue
        (ax, ay), (bx, by) = p[-2], p[-1]
        out.append((e.gid, (bx, by), (_sgn(bx - ax), _sgn(by - ay))))
        if e.bidirectional:
            (ax, ay), (bx, by) = p[1], p[0]
            out.append((e.gid, (bx, by), (_sgn(bx - ax), _sgn(by - ay))))
    return out


def _near(p, q) -> bool:
    return abs(p[0] - q[0]) + abs(p[1] - q[1]) <= 4


def _sgn(v: float) -> int:
    return (v > 0) - (v < 0)


def _dist_point_box(p: Tuple[float, float], b: Box) -> float:
    dx = max(b.x - p[0], 0, p[0] - b.r)
    dy = max(b.y - p[1], 0, p[1] - b.b)
    return (dx * dx + dy * dy) ** 0.5


def _degrees(lay: Layout) -> Tuple[Dict[str, int], Dict[str, int]]:
    o: Dict[str, int] = {}
    i: Dict[str, int] = {}
    for e in lay.edges:
        o[e.src] = o.get(e.src, 0) + 1
        i[e.dst] = i.get(e.dst, 0) + 1
    return o, i


def _rows(boxes: List[Tuple[str, Box]]) -> List[List[Tuple[str, Box]]]:
    rows: List[List[Tuple[str, Box]]] = []
    for item in sorted(boxes, key=lambda t: (t[1].cy, t[1].x)):
        if rows and abs(rows[-1][0][1].cy - item[1].cy) <= 24:
            rows[-1].append(item)
        else:
            rows.append([item])
    return rows


_NUMBERED = re.compile(r"(?i)^\s*(?:\(?\d+[).:]?\s+\S|step\s*\d+)")
_QUANTITY = re.compile(r"(?i)^\s*\d+\s*(?:ms|s|sec|secs|min|mins|h|hrs?|days?|azs?|az|kb|mb|gb|"
                       r"tb|%|x|gib|mib|kib|ports?|subnets?)\b")


# -- the check ---------------------------------------------------------------------------------

def check(lay: Layout, *, pass_score: float = 85.0, icon_warnings: Optional[Dict[str, str]] = None
          ) -> QualityResult:
    F: List[QFinding] = []

    def add(code: str, sev: str, el: str = "") -> None:
        F.append(QFinding(code, sev, el))

    scale = min(1.0, PLACE_WIDTH / max(1, lay.width))
    nodes, groups, edges = lay.nodes, lay.groups, lay.edges
    nbox = {n.key: node_box(n) for n in nodes}
    texts: List[Tuple[str, Box]] = []
    for n in nodes:
        texts.append(("cap:" + n.key, n.caption))
    for g in groups:
        texts.append(("gt:" + g.key, g.title_box))
    for e in edges:
        if e.label_box:
            texts.append(("el:" + e.gid, e.label_box))
    if lay.title:
        texts.append(("title", lay.title.box))
    outd, ind = _degrees(lay)

    # -- paths
    for e in edges:
        for s in segments(e.points):
            if seg_len(s) == 0:
                add("DQ-P102", "fail", e.gid)
                break
        for n in nodes:
            if n.key in (e.src, e.dst):
                # its own end: only the port run may touch it; any other run that enters the
                # icon (or runs between the icon and its caption) cuts the node
                if cuts_own(e.points, n, n.key == e.src, n.key == e.dst):
                    add("DQ-P101", "fail", e.gid)
                    break
                if grazes_own(e.points, n, n.key == e.src, n.key == e.dst):
                    # a run along its own end before turning in (doubled border, DR2-01)
                    add("DQ-L120", "fix", e.gid)
                    break
                continue
            if any(seg_hits_box(s, n.icon) for s in segments(e.points)):
                add("DQ-P101", "fail", e.gid)
                break
    for i, a in enumerate(edges):
        for b in edges[i + 1:]:
            if any(seg_cross(s, t) for s in segments(a.points) for t in segments(b.points)):
                add("DQ-X101", "fix", b.gid)

    # -- layout: nodes and groups
    keys = [n.key for n in nodes]
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if nbox[a].overlaps(nbox[b]):
                add("DQ-L101", "fail", next(n.gid for n in nodes if n.key == a))
    for n in nodes:
        for g in groups:
            nb = nbox[n.key]
            if nb.overlaps(g.box) and not g.box.contains(nb):
                add("DQ-L102", "fail", n.gid)
                break
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            nested = a.box.contains(b.box) or b.box.contains(a.box)
            if a.box.overlaps(b.box) and not nested:
                add("DQ-G101", "fail", b.gid)
    if lay.width > 1280:
        add("DQ-L115", "fix")
    if lay.width > 992:
        add("DQ-A103", "fix")
    if max(lay.width, lay.height) / max(1, min(lay.width, lay.height)) > 2.5:
        add("DQ-L107", "warn")
    if lay.title_sets_width >= 48:
        add("DQ-L124", "warn")

    rows = [r for r in _rows([(n.gid, n.icon) for n in nodes]) if len(r) >= 2]
    for i, ra in enumerate(rows):
        a_lo = min(nbox[_k(nodes, x)].y for x, _ in ra)
        a_hi = max(nbox[_k(nodes, x)].b for x, _ in ra)
        for rb in rows[i + 1:]:
            b_lo = min(nbox[_k(nodes, x)].y for x, _ in rb)
            if b_lo < a_hi - 1 and a_lo < b_lo:
                add("DQ-L104", "warn", rb[0][0])
    by_parent: Dict[Optional[str], List] = {}
    for n in nodes:
        by_parent.setdefault(n.parent, []).append(n)
    for sibs in by_parent.values():
        for row in _rows([(n.gid, n.icon) for n in sibs]):
            if len(row) < 3:
                continue
            xs = sorted(b.cx for _, b in row)
            gaps = [b - a for a, b in zip(xs, xs[1:])]
            if min(gaps) <= 0 or max(gaps) > 1.8 * min(gaps):
                add("DQ-L105", "warn", row[0][0])
    for n in nodes:
        nb = nbox[n.key]
        for g in groups:
            if g.parent != n.parent or g.box.contains(nb) or g.box.w >= 0.85 * lay.width:
                continue
            if nb.r <= g.box.x or g.box.r <= nb.x:
                ov = min(nb.b, g.box.b) - max(nb.y, g.box.y)
                frac = ov / nb.h if nb.h else 0
                if 0.2 <= frac <= 0.8:
                    add("DQ-L106", "warn", n.gid)
                    break
    # sibling evenness, subnets
    sib_groups: Dict[Optional[str], List] = {}
    for g in groups:
        sib_groups.setdefault(g.parent, []).append(g)
    for sibs in sib_groups.values():
        for i, a in enumerate(sibs):
            for b in sibs[i + 1:]:
                same_row = a.box.y < b.box.b and b.box.y < a.box.b
                same_col = a.box.x < b.box.r and b.box.x < a.box.r
                if same_row:
                    d = abs(a.box.h - b.box.h) / max(a.box.h, b.box.h)
                    if 0.10 <= d <= 0.40:
                        add("DQ-L110", "warn", b.gid)
                elif same_col:
                    d = abs(a.box.w - b.box.w) / max(a.box.w, b.box.w)
                    if 0.10 <= d <= 0.40:
                        add("DQ-L110", "warn", b.gid)
                    if a.type.endswith("subnet") and b.type.endswith("subnet") and \
                            abs(a.box.w - b.box.w) > 4:
                        add("DQ-L123", "warn", b.gid)
    # emptiness (8 px raster), gaps and padding
    labels_ = [e.label_box for e in edges if e.label_box is not None]
    canvas = Box(0, 0, lay.width, lay.height)
    scopes = [(g.gid, g.box, BAND, g) for g in groups] + [("", canvas, 0, None)]
    for gid, gb, band, g in scopes:
        kid_groups = [c.box for c in groups if (c.parent == g.key if g else c.parent is None)]
        own = [nbox[n.key] for n in nodes if (n.parent == g.key if g else n.parent is None)]
        if not own and not kid_groups:
            continue
        inner = Box(gb.x, gb.y + band, gb.w, gb.h - band)
        cover = [b.inflate(16) for b in own] + kid_groups + [lb for lb in labels_ if lb.overlaps(inner)]
        if g is None:
            cover += [t.box for t in lay.legend] + ([lay.caption.box] if lay.caption else []) + \
                     ([lay.title.box] if lay.title else [])
        if _empty_frac(inner, cover) > 0.65:
            add("DQ-L111", "fix", gid)
        items = own + kid_groups
        for lo_, hi_ in (("y", "b"), ("x", "r")):
            bands: List[List[float]] = []
            for it in sorted(items, key=lambda b: getattr(b, lo_)):
                a0, a1 = getattr(it, lo_), getattr(it, hi_)
                hit = next((bd for bd in bands if a0 < bd[1] and bd[0] < a1), None)
                if hit:
                    hit[0], hit[1] = min(hit[0], a0), max(hit[1], a1)
                else:
                    bands.append([a0, a1])
            gaps_ = [b2[0] - b1[1] for b1, b2 in zip(bands, bands[1:]) if b2[0] - b1[1] > 0]
            if gaps_ and (max(gaps_) > 160 or (len(gaps_) >= 2 and max(gaps_) > 2.5 * min(gaps_)
                                                and max(gaps_) > 64)):
                add("DQ-L111", "warn", gid)
                break
        if g is None:
            continue
        kids = [n.cell for n in nodes if n.parent == g.key] + kid_groups
        left = min(k.x for k in kids) - g.box.x
        right = g.box.r - max(k.r for k in kids)
        top = min(k.y for k in kids) - (g.box.y + TITLE_AREA)
        bottom = g.box.b - max(k.b for k in kids)
        insets = [top, bottom] if g.title_bound else [left, right, top, bottom]
        if g.title_bound and abs(left - right) > 8:
            add("DQ-A102", "fix", g.gid)
        elif max(insets) - min(insets) > 8:
            add("DQ-A102", "fix", g.gid)
        if max(insets) > 80 and max(insets) > 3 * max(8.0, min(insets)):
            add("DQ-L111", "warn", g.gid)
    # pitch, sparse figure and long arrows
    def _pitch_of(key, along) -> List[float]:
        lines_: List[Tuple[float, List[float]]] = []
        for n in nodes:
            c = key(n.icon)
            hit = next((ln for ln in lines_ if abs(ln[0] - c) < 24), None)
            if hit:
                hit[1].append(along(n.icon))
            else:
                lines_.append((c, [along(n.icon)]))
        out_ = []
        for _c, vs in lines_:
            vs.sort()
            out_ += [b - a for a, b in zip(vs, vs[1:]) if b - a > 0]
        return out_

    pitches = _pitch_of(lambda b: b.cy, lambda b: b.cx) + _pitch_of(lambda b: b.cx, lambda b: b.cy)
    gaps = []
    for n in nodes:
        others = [m for m in nodes if m is not n]
        if others:
            gaps.append(min(max(m.cell.x - n.cell.r, n.cell.x - m.cell.r,
                                m.cell.y - n.cell.b, n.cell.y - m.cell.b, 0) for m in others))
    pitch = statistics.median(pitches) if pitches else 160.0
    if gaps and statistics.median(gaps) > 150:
        add("DQ-L108", "fix")
    elif pitches and pitch > 240:
        add("DQ-L108", "warn")
    long_limit = max(240.0, 2 * min(pitch, 160.0))
    for e in edges:
        for s_ in segments(e.points):
            if _empty_run(s_, labels_) > long_limit:
                add("DQ-L108", "fix", e.gid)
                break

    # -- layout: edges
    for e in edges:
        segs = segments(e.points)
        if e.label and _NUMBERED.match(e.label) and not _QUANTITY.match(e.label):
            add("DQ-L113", "fail", e.gid)
        if len(segs) > 1 and (seg_len(segs[0]) < 32 or seg_len(segs[-1]) < 32):
            add("DQ-L112", "warn", e.gid)
        elif len(segs) == 3 and seg_len(segs[1]) < 12:
            add("DQ-L112", "warn", e.gid)
        one_to_one = outd.get(e.src) == 1 and ind.get(e.dst) == 1
        shares_end = any(o is not e and (_near(o.points[0], e.points[0])
                                         or _near(o.points[-1], e.points[-1])) for o in edges)
        if len(segs) == 3 and not shares_end and _zigzag(segs):
            add("DQ-L114", "fix", e.gid)
        if one_to_one and len(segs) == 2 and min(seg_len(s) for s in segs) <= 240:
            add("DQ-L119", "fix", e.gid)
        if len(segs) == 1 and seg_len(segs[0]) < 12:
            add("DQ-L126", "warn", e.gid)
        hit_text = False
        for tid, tb in texts:
            if tid == "el:" + e.gid:
                continue
            if any(seg_hits_box(s, tb) for s in segs):
                hit_text = True
                break
        if hit_text:
            add("DQ-L117", "fix", e.gid)
        near_title = any(seg_hits_box(s, g.title_box.inflate(12)) for g in groups for s in segs) \
            or any(seg_hits_box(s, g.icon_box) for g in groups if g.icon_box is not None
                   for s in segs)
        if near_title and not hit_text:
            add("DQ-L118", "fix", e.gid)
        for n in nodes:
            if n.key in (e.src, e.dst):
                continue
            if any(seg_hits_box(s, n.icon.inflate(10)) and not seg_hits_box(s, n.icon) for s in segs):
                add("DQ-L120", "fix", e.gid)
                break
        if e.label_box is not None:
            lb = e.label_box
            for g in groups:
                if lb.overlaps(g.box) and not g.box.contains(lb):
                    add("DQ-L103", "fail", e.gid)
                    break
            for g in groups:
                band = Box(g.box.x, g.box.y, g.box.w, 28)
                if lb.overlaps(band):
                    add("DQ-L118", "fix", e.gid)
                    break
            others = [n.icon for n in nodes] + [tb for tid, tb in texts if tid != "el:" + e.gid]
            if any(lb.overlaps(o) for o in others):
                add("DQ-L121", "fix", e.gid)
    for i, a in enumerate(edges):
        for b in edges[i + 1:]:
            fan = a.src == b.src or a.dst == b.dst
            worst_shared, worst_d = 0.0, float("inf")
            for s in segments(a.points):
                for t in segments(b.points):
                    d, shared = parallel_overlap(s, t)
                    if shared > worst_shared or (shared == worst_shared and d < worst_d):
                        if d <= 12:
                            worst_shared, worst_d = shared, d
            if worst_d <= 6 and worst_shared > 24 and not fan:
                add("DQ-L116", "fail", b.gid)
            elif 0 < worst_d <= 12 and worst_shared > 16:
                add("DQ-L125", "warn", b.gid)
    tips = _tips(lay)
    for i, (eid, p, d) in enumerate(tips):
        for eid2, p2, d2 in tips[i + 1:]:
            if abs(p[0] - p2[0]) <= 2 and abs(p[1] - p2[1]) <= 2 and d != d2:
                add("DQ-L122", "fail", eid2)
        edge = next(e for e in edges if e.gid == eid)
        target = edge.dst
        for tid, tb in texts:
            if tid == "el:" + eid:
                continue
            if tid == "cap:" + target and d == (0, -1):
                if _dist_point_box(p, tb) <= 6:
                    add("DQ-L109", "warn", eid)
                continue
            if tid == "cap:" + target:
                continue
            if _dist_point_box(p, tb) <= 6:
                add("DQ-L109", "fail", eid)
                break

    # -- legend
    if any(e.optional or e.style == "denied" for e in edges):
        kinds = {k for k, _ in lay.legend_lines}
        if any(e.optional and e.style != "denied" for e in edges) and "dashed" not in kinds:
            add("DQ-LG101", "fail")
        if any(e.style == "denied" for e in edges) and "denied" not in kinds:
            add("DQ-LG101", "fail")

    # -- captions and icons
    for n in nodes:
        if n.truncated:
            add("DQ-T101", "warn", n.gid)
        w = (icon_warnings or {}).get(n.key)
        if w in ("DQ-I101", "DQ-I103"):
            add(w, "warn", n.gid)

    # -- legibility
    sizes = [FONT] + ([lay.title.size] if lay.title else [])
    smallest = min(sizes) * scale
    if smallest < BLOCKER_TEXT:
        add("DQ-R101", "fail")
    elif smallest < MIN_TEXT:
        add("DQ-R102", "fix")
    if FONT * scale < MIN_CAPTION:
        add("DQ-A101", "fix")
    icon_shown = min([n.icon.w for n in nodes] or [48]) * scale
    if icon_shown < MIN_ICON:
        add("DQ-R103", "fix")
    elif icon_shown < SMALL_ICON:
        add("DQ-R104", "warn")

    notes = []
    if lay.height * scale > 900 and lay.width < lay.height:
        notes.append("portrait: taller than a laptop screen at README width")
    sc = score_of(F)
    passed = not [f for f in F if _is_hard(f)] and sc >= pass_score
    return QualityResult(sc, passed, pass_score, F, notes)


def _empty_frac(inner: Box, cover: List[Box], cell: int = 8) -> float:
    """Share of the 8 px cells of `inner` that no cover box touches."""
    total = empty = 0
    y = inner.y
    while y < inner.b:
        x = inner.x
        while x < inner.r:
            total += 1
            c = Box(x, y, cell, cell)
            if not any(b.overlaps(c) for b in cover):
                empty += 1
            x += cell
        y += cell
    return empty / total if total else 0.0


def _empty_run(s, labels_: List[Box]) -> float:
    """A straight run's length minus the labels riding on it."""
    (x1, y1), (x2, y2) = s
    h = y1 == y2
    lo, hi = (min(x1, x2), max(x1, x2)) if h else (min(y1, y2), max(y1, y2))
    line = y1 if h else x1
    covered = 0.0
    for t in labels_:
        if h and t.y - 20 < line < t.b + 20 and t.x < hi and t.r > lo:
            covered += min(t.r, hi) - max(t.x, lo)
        elif not h and t.x - 20 < line < t.r + 20 and t.y < hi and t.b > lo:
            covered += min(t.b, hi) - max(t.y, lo)
    return (hi - lo) - covered


def _k(nodes, gid: str) -> str:
    return next(n.key for n in nodes if n.gid == gid)
