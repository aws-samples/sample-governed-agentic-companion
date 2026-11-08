"""SVG emitter (design D6.5). Inputs are only the laid-out scene and the catalog: every string in
the output is a policy-checked scene label or a fixed template, and every value passes _esc().
No <script>, <style>, <foreignObject>, <image>, event attribute or external reference; arrowheads
are two fixed local markers, so every `url(` / `href` value is a local `#` reference.
Element ids are generated (g1, n1, e1; icon internals prefixed i<n>-), never KB ids (C28)."""

from __future__ import annotations

import html
import re
from typing import Dict, List, Optional, Tuple

from .assets import IconRef
from .layout import FONT, LINE_H, Layout
from .style import (DASH, DENIED_COLOR, EDGE_COLOR, FONT_FAMILY, GROUP_STYLE, OPTIONAL_COLOR,
                    SECONDARY_COLOR, TEXT_COLOR)

ARROW = 8
OUTLINE = 4   # px between an optional node's icon and its dashed outline


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _n(v: float) -> str:
    r = round(float(v), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def prefix_ids(body: str, prefix: str) -> str:
    """Prefix every internal id and #reference of an inlined icon so icons cannot collide."""
    body = re.sub(r'\bid="([^"]+)"', lambda m: f'id="{prefix}{m.group(1)}"', body)
    body = re.sub(r"url\(#([^)\s'\"]+)\)", lambda m: f"url(#{prefix}{m.group(1)})", body)
    body = re.sub(r'\bhref="#([^"]+)"', lambda m: f'href="#{prefix}{m.group(1)}"', body)
    return body


def group_color(gtype: str, catalog_color: Optional[str] = None) -> Tuple[str, bool]:
    color, dashed = GROUP_STYLE.get(gtype, GROUP_STYLE["generic"])
    if gtype in ("public-subnet", "account") and catalog_color:
        color = catalog_color
    return color, dashed


def arrowhead(tip: Tuple[float, float], prev: Tuple[float, float]) -> List[Tuple[float, float]]:
    """Open arrowhead as 3 points ending at `tip` (used by the geometry checks and tests; the
    SVG draws it with the fixed `gac-arrow` marker of the same size)."""
    (x, y), (px, py) = tip, prev
    dx = (x > px) - (x < px)
    dy = (y > py) - (y < py)
    if dx:
        return [(x - dx * ARROW, y - ARROW / 2), (x, y), (x - dx * ARROW, y + ARROW / 2)]
    return [(x - ARROW / 2, y - dy * ARROW), (x, y), (x + ARROW / 2, y - dy * ARROW)]


def _markers() -> str:
    """Fixed arrowhead markers (local `#` references only; marker size in stroke widths)."""
    out = ["<defs>"]
    for name, color in (("gac-arrow", EDGE_COLOR), ("gac-arrow-denied", DENIED_COLOR)):
        out.append(f'<marker id="{name}" markerWidth="9" markerHeight="9" refX="5" refY="3" '
                   f'orient="auto" markerUnits="strokeWidth"><path d="M0 0 L5 3 L0 6" '
                   f'fill="none" stroke="{color}" stroke-width="1" stroke-linecap="round" '
                   f'stroke-linejoin="round"/></marker>')
        out.append(f'<marker id="{name}-rev" markerWidth="9" markerHeight="9" refX="0" refY="3" '
                   f'orient="auto" markerUnits="strokeWidth"><path d="M5 0 L0 3 L5 6" '
                   f'fill="none" stroke="{color}" stroke-width="1" stroke-linecap="round" '
                   f'stroke-linejoin="round"/></marker>')
    out.append("</defs>")
    return "".join(out)


def outline_path(b) -> str:
    """The dashed outline drawn around an optional node's icon (a path, not a rect, so it is
    never read as a group box)."""
    o = b.inflate(OUTLINE)
    return (f'<path d="M{_n(o.x)} {_n(o.y)} H{_n(o.r)} V{_n(o.b)} H{_n(o.x)} Z" fill="none" '
            f'stroke="{OPTIONAL_COLOR}" stroke-width="1.5" stroke-dasharray="{DASH}"/>')


def _text(x: float, y: float, s: str, *, size: int = FONT, bold: bool = False,
          color: str = TEXT_COLOR, anchor: str = "start") -> str:
    weight = ' font-weight="bold"' if bold else ""
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}"{weight} fill="{color}" '
            f'text-anchor="{anchor}">{_esc(s)}</text>')


def emit_svg(lay: Layout, icons: Dict[str, IconRef], group_icons: Dict[str, Optional[IconRef]],
             group_colors: Dict[str, Optional[str]]) -> str:
    """`icons` maps node key -> IconRef (catalog or generic); `group_icons` maps group key ->
    IconRef or None; `group_colors` maps group type -> catalog colour (public-subnet, account).

    Structure (so the figure can be measured from the SVG alone): the title sits before the
    content group; each group is `<g data-name data-kind><title/><rect/>...` with its subgroups
    and nodes nested inside; each node is `<g data-name><title/><svg overflow x y width>` plus its
    captions; every arrow is a path in `<g data-name="connectors">` and every arrow label a halo
    text in `<g data-name="arrow-labels">` whose `data-arrow` is that path's index."""
    W, H = lay.width, lay.height
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'font-family="{_esc(FONT_FAMILY)}">',
        _markers(),
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>',
    ]
    icon_n = 0

    def nested(ref: IconRef, x: float, y: float, size: int) -> str:
        nonlocal icon_n
        icon_n += 1
        body = prefix_ids(ref.body, f"i{icon_n}-")
        return (f'<svg viewBox="{_esc(ref.viewbox)}" overflow="visible" x="{_n(x)}" y="{_n(y)}" '
                f'width="{size}" height="{size}">{body}</svg>')

    if lay.title:
        t = lay.title
        out.append(_text(t.box.x, t.box.y + t.size, t.text, size=t.size, bold=True))
    out.append('<g transform="translate(0 0)">')
    kids: Dict[Optional[str], List[Tuple[str, object]]] = {}
    for g in sorted(lay.groups, key=lambda g: int(g.gid[1:])):
        kids.setdefault(g.parent, []).append(("g", g))
    for n in sorted(lay.nodes, key=lambda n: int(n.gid[1:])):
        kids.setdefault(n.parent, []).append(("n", n))

    def node(n) -> None:
        out.append(f'<g id="{n.gid}" data-name="{_esc(n.label)}"><title>{_esc(n.label)}</title>'
                   + (outline_path(n.icon) if n.optional else "")
                   + nested(icons[n.key], n.icon.x, n.icon.y, 48))
        for i, line in enumerate(n.lines):
            out.append(_text(n.icon.cx, n.caption.y + 12 + i * LINE_H, line, anchor="middle"))
        out.append("</g>")

    def group(g) -> None:
        color, dashed = group_color(g.type, group_colors.get(g.type))
        dash = f' stroke-dasharray="{DASH}"' if dashed else ""
        b = g.box
        out.append(f'<g id="{g.gid}" data-name="{_esc(g.label)}" data-kind="{_esc(g.type)}">'
                   f'<title>{_esc(g.label)}</title>'
                   f'<rect x="{_n(b.x)}" y="{_n(b.y)}" width="{_n(b.w)}" height="{_n(b.h)}" rx="8" '
                   f'ry="8" fill="none" stroke="{color}" stroke-width="1.5"{dash}/>')
        ref = group_icons.get(g.key)
        if ref is not None and g.icon_box is not None:
            out.append(nested(ref, g.icon_box.x, g.icon_box.y, 24))
        out.append(_text(g.title_box.x, g.title_box.y + 12, g.label, bold=True, color=color))
        walk(g.key)
        out.append("</g>")

    def walk(parent: Optional[str]) -> None:
        for kind, item in kids.get(parent, []):
            group(item) if kind == "g" else node(item)

    walk(None)
    edges = sorted(lay.edges, key=lambda e: int(e.gid[1:]))
    out.append('<g data-name="connectors">')
    for e in edges:
        denied = e.style == "denied"
        color = DENIED_COLOR if denied else EDGE_COLOR
        dash = f' stroke-dasharray="{DASH}"' if (e.optional or denied) else ""
        mk = "gac-arrow-denied" if denied else "gac-arrow"
        start = f' marker-start="url(#{mk}-rev)"' if e.bidirectional else ""
        d = "M" + " L".join(f"{_n(x)} {_n(y)}" for x, y in e.points)
        out.append(f'<g id="{e.gid}"><path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" '
                   f'stroke-linejoin="round"{dash} marker-end="url(#{mk})"{start}/></g>')
    out.append("</g>")
    out.append('<g data-name="arrow-labels">')
    for i, e in enumerate(edges):
        if e.label and e.label_box:
            lb = e.label_box
            out.append(f'<g data-arrow="{i}"><text x="{_n(lb.cx)}" y="{_n(lb.y + 12)}" '
                       f'font-size="{FONT}" fill="{TEXT_COLOR}" text-anchor="middle" '
                       f'stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round" '
                       f'paint-order="stroke">{_esc(e.label)}</text></g>')
    out.append("</g>")
    for t in lay.legend:
        out.append(_text(t.box.x, t.box.y + 12, t.text, bold=t.bold))
    for kind, (x1, y1, x2, y2) in lay.legend_lines:
        if kind == "outline":
            color = OPTIONAL_COLOR
            a, b, h = x1 + 6, x1 + 22, 8
            out.append(f'<path d="M{_n(a)} {_n(y1 - h)} H{_n(b)} V{_n(y1 + h)} H{_n(a)} Z" '
                       f'fill="none" stroke="{color}" stroke-width="1.5" stroke-dasharray="{DASH}"/>')
            continue
        color = DENIED_COLOR if kind == "denied" else EDGE_COLOR
        dash = f' stroke-dasharray="{DASH}"' if kind != "solid" else ""
        out.append(f'<path d="M{_n(x1)} {_n(y1)} L{_n(x2)} {_n(y2)}" fill="none" stroke="{color}" '
                   f'stroke-width="1.5"{dash}/>')
    if lay.caption:
        c = lay.caption
        out.append(_text(c.box.x, c.box.y + 12, c.text, color=SECONDARY_COLOR))
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"
