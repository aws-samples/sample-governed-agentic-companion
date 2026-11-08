"""drawio emitter (design D6.6): the same form as the kit's 3.2.2 figures
(docs/architecture/diagrams/).

Every node and group icon is a `shape=image` cell whose image is the sanitized catalog SVG as a
percent-encoded data URI (encodeURIComponent's character set, so the style value holds no `;`,
`=`, `"`, `<` or `>`). No draw.io `mxgraph.aws4` shape is used. Same geometry and generated ids
as the SVG (C28): groups g1.., nodes n1.., edges e1..; root/layer, edge-label, legend and
caption cells use reserved numbers in the same id forms. Every value passes _esc().
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from .assets import IconRef
from .emit_svg import OUTLINE, _esc, _n, group_color
from .layout import Box, Layout
from .route import seg_len, segments
from .style import DENIED_COLOR, EDGE_COLOR, OPTIONAL_COLOR, SECONDARY_COLOR, TEXT_COLOR

_SAFE = "-_.!~*'()"


def data_uri(ref: IconRef) -> str:
    return "data:image/svg+xml," + quote(ref.standalone, safe=_SAFE)


def _geom(x: float, y: float, w: float, h: float) -> str:
    return (f'<mxGeometry x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
            f'as="geometry"/>')


def _side_frac(icon: Box, p: Tuple[float, float]) -> Tuple[str, str, str, str]:
    """(x, y, dx, dy) perimeter constraint for a port point relative to the icon tile."""
    x, y = p
    fx = min(1.0, max(0.0, (x - icon.x) / icon.w))
    fy = min(1.0, max(0.0, (y - icon.y) / icon.h))
    dx = x - (icon.x + fx * icon.w)
    dy = y - (icon.y + fy * icon.h)
    return _n(fx), _n(fy), _n(dx), _n(dy)


def _label_offset(points: List[Tuple[float, float]], box: Box) -> Tuple[float, float, float]:
    """drawio relative label geometry: position along the path in [-1, 1] and the offset of the
    label centre from that point."""
    segs = segments(points)
    total = sum(seg_len(s) for s in segs) or 1.0
    cx, cy = box.cx, box.cy
    best = None
    walked = 0.0
    for (x1, y1), (x2, y2) in segs:
        if y1 == y2:
            px = min(max(cx, min(x1, x2)), max(x1, x2))
            py = y1
        else:
            px = x1
            py = min(max(cy, min(y1, y2)), max(y1, y2))
        d = abs(px - cx) + abs(py - cy)
        along = walked + abs(px - x1) + abs(py - y1)
        if best is None or d < best[0]:
            best = (d, along, px, py)
        walked += seg_len(((x1, y1), (x2, y2)))
    _d, along, px, py = best
    return 2 * along / total - 1, cx - px, cy - py


def emit_drawio(lay: Layout, name: str, diagram_name: str, icons: Dict[str, IconRef],
                group_icons: Dict[str, Optional[IconRef]],
                group_colors: Dict[str, Optional[str]]) -> str:
    W, H = lay.width, lay.height
    cells: List[str] = ['<mxCell id="g0"/>', '<mxCell id="g00" parent="g0"/>']
    gbox = {g.key: g for g in lay.groups}
    reserved = 900

    def rid(prefix: str) -> str:
        nonlocal reserved
        reserved += 1
        return f"{prefix}{reserved}"

    def parent_of(key: Optional[str]) -> Tuple[str, float, float]:
        if key and key in gbox:
            g = gbox[key]
            return g.gid, g.box.x, g.box.y
        return "g00", 0.0, 0.0

    icon_n = 0
    for g in sorted(lay.groups, key=lambda g: (g.depth, int(g.gid[1:]))):
        color, dashed = group_color(g.type, group_colors.get(g.type))
        ref = group_icons.get(g.key)
        has_icon = ref is not None and g.icon_box is not None
        style = (f"rounded=1;absoluteArcSize=1;arcSize=8;html=1;strokeColor={color};"
                 f"fillColor=none;dashed={1 if dashed else 0};verticalAlign=top;align=left;"
                 f"spacingLeft={40 if g.type != 'generic' else 8};spacingTop=4;fontColor={color};"
                 f"fontSize=13;fontStyle=1;container=1;collapsible=0;")
        pid, px, py = parent_of(g.parent)
        b = g.box
        cells.append(f'<mxCell id="{g.gid}" value="{_esc(g.label)}" style="{_esc(style)}" '
                     f'vertex="1" parent="{pid}">{_geom(b.x - px, b.y - py, b.w, b.h)}</mxCell>')
        if has_icon:
            icon_n += 1
            istyle = ("shape=image;imageAspect=1;aspect=fixed;html=1;"
                      f"image={data_uri(ref)};")
            cells.append(f'<mxCell id="i{icon_n}-icon" value="" style="{_esc(istyle)}" vertex="1" '
                         f'parent="{g.gid}">{_geom(8, 8, 24, 24)}</mxCell>')
    for n in sorted(lay.nodes, key=lambda n: int(n.gid[1:])):
        style = ("shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=1;"
                 f"aspect=fixed;html=1;fontSize=13;fontColor={TEXT_COLOR};"
                 f"image={data_uri(icons[n.key])};")
        pid, px, py = parent_of(n.parent)
        if n.optional:
            o = n.icon.inflate(OUTLINE)
            ostyle = (f"rounded=0;html=1;fillColor=none;dashed=1;strokeColor={OPTIONAL_COLOR};"
                      "strokeWidth=1.5;connectable=0;")
            cells.append(f'<mxCell id="{rid("n")}" value="" style="{_esc(ostyle)}" vertex="1" '
                         f'parent="{pid}">{_geom(o.x - px, o.y - py, o.w, o.h)}</mxCell>')
        value = "<br>".join(_esc(line) for line in n.lines)
        cells.append(f'<mxCell id="{n.gid}" value="{_esc(value)}" style="{_esc(style)}" '
                     f'vertex="1" parent="{pid}">'
                     f'{_geom(n.icon.x - px, n.icon.y - py, 48, 48)}</mxCell>')
    nodes = {n.key: n for n in lay.nodes}
    for e in sorted(lay.edges, key=lambda e: int(e.gid[1:])):
        color = DENIED_COLOR if e.style == "denied" else EDGE_COLOR
        dashed = 1 if (e.optional or e.style == "denied") else 0
        a, b = nodes[e.src], nodes[e.dst]
        ex, ey, edx, edy = _side_frac(a.icon, e.points[0])
        nx, ny, ndx, ndy = _side_frac(b.icon, e.points[-1])
        start = "open" if e.bidirectional else "none"
        style = (f"edgeStyle=orthogonalEdgeStyle;html=1;rounded=1;arcSize=6;jettySize=auto;"
                 f"startArrow={start};startFill=0;endArrow=open;endFill=0;endSize=8;startSize=8;"
                 f"strokeColor={color};strokeWidth=1.5;dashed={dashed};"
                 f"exitX={ex};exitY={ey};exitDx={edx};exitDy={edy};exitPerimeter=0;"
                 f"entryX={nx};entryY={ny};entryDx={ndx};entryDy={ndy};entryPerimeter=0;")
        pts = "".join(f'<mxPoint x="{_n(x)}" y="{_n(y)}"/>' for x, y in e.points[1:-1])
        arr = f'<Array as="points">{pts}</Array>' if pts else ""
        cells.append(f'<mxCell id="{e.gid}" value="" style="{_esc(style)}" edge="1" parent="g00" '
                     f'source="{a.gid}" target="{b.gid}"><mxGeometry relative="1" as="geometry">'
                     f'{arr}</mxGeometry></mxCell>')
        if e.label and e.label_box:
            t, ox, oy = _label_offset(e.points, e.label_box)
            lstyle = ("edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;rounded=1;"
                      "arcSize=40;fillColor=#FFFFFF;opacity=50;strokeColor=none;textOpacity=100;"
                      f"fontColor={TEXT_COLOR};fontSize=13;")
            lid = f"e{100 + int(e.gid[1:])}"
            cells.append(f'<mxCell id="{lid}" value="{_esc(e.label)}" style="{_esc(lstyle)}" '
                         f'vertex="1" connectable="0" parent="{e.gid}"><mxGeometry x="{_n(t)}" '
                         f'relative="1" as="geometry"><mxPoint x="{_n(ox)}" y="{_n(oy)}" '
                         f'as="offset"/></mxGeometry></mxCell>')
    text_style = ("text;html=1;align=left;verticalAlign=middle;strokeColor=none;fillColor=none;"
                  "fontSize=13;fontColor={color};{bold}")
    if lay.title:
        t = lay.title
        st = text_style.format(color=TEXT_COLOR, bold="fontStyle=1;").replace("fontSize=13", "fontSize=18")
        cells.append(f'<mxCell id="{rid("g")}" value="{_esc(t.text)}" style="{_esc(st)}" vertex="1" '
                     f'parent="g00">{_geom(t.box.x, t.box.y, t.box.w + 16, t.box.h)}</mxCell>')
    for t in lay.legend:
        st = text_style.format(color=TEXT_COLOR, bold="fontStyle=1;" if t.bold else "")
        cells.append(f'<mxCell id="{rid("g")}" value="{_esc(t.text)}" style="{_esc(st)}" vertex="1" '
                     f'parent="g00">{_geom(t.box.x, t.box.y, t.box.w + 16, t.box.h)}</mxCell>')
    for kind, (x1, y1, x2, y2) in lay.legend_lines:
        if kind == "outline":
            color = OPTIONAL_COLOR
            a, b, h = x1 + 6, x1 + 22, 8
            st = f"rounded=0;html=1;fillColor=none;dashed=1;strokeColor={color};strokeWidth=1.5;"
            cells.append(f'<mxCell id="{rid("g")}" value="" style="{_esc(st)}" vertex="1" '
                         f'parent="g00">{_geom(a, y1 - h, b - a, 2 * h)}</mxCell>')
            continue
        color = DENIED_COLOR if kind == "denied" else EDGE_COLOR
        st = (f"html=1;endArrow=none;strokeColor={color};strokeWidth=1.5;"
              f"dashed={0 if kind == 'solid' else 1};")
        cells.append(f'<mxCell id="{rid("e")}" value="" style="{_esc(st)}" edge="1" parent="g00">'
                     f'<mxGeometry relative="1" as="geometry"><mxPoint x="{_n(x1)}" y="{_n(y1)}" '
                     f'as="sourcePoint"/><mxPoint x="{_n(x2)}" y="{_n(y2)}" as="targetPoint"/>'
                     f'</mxGeometry></mxCell>')
    if lay.caption:
        c = lay.caption
        st = text_style.format(color=SECONDARY_COLOR, bold="")
        cells.append(f'<mxCell id="{rid("g")}" value="{_esc(c.text)}" style="{_esc(st)}" vertex="1" '
                     f'parent="g00">{_geom(c.box.x, c.box.y, c.box.w + 16, c.box.h)}</mxCell>')
    head = (f'<mxfile host="gac" type="device" compressed="false"><diagram id="{_esc(name)}" '
            f'name="{_esc(diagram_name)}"><mxGraphModel dx="{W}" dy="{H}" grid="1" gridSize="8" '
            f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="{W}" pageHeight="{H}" math="0" shadow="0"><root>')
    return head + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n"
