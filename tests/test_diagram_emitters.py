"""SVG and drawio emitters (design D6.5, D6.6; acceptance 8, 9, 30, C28)."""

import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

DET = pytest.importorskip("defusedxml.ElementTree")

from diagram_fixtures import fixture_assets_dir  # noqa: E402
from test_diagram_layout_quality import _scene  # noqa: E402
from agents.diagram.assets import Assets  # noqa: E402
from agents.diagram.emit_drawio import data_uri, emit_drawio  # noqa: E402
from agents.diagram.emit_svg import emit_svg, prefix_ids  # noqa: E402
from agents.diagram.layout import best_layout  # noqa: E402
from agents.diagram.style import (CAPTION, GROUP_STYLE, LEGEND_DASHED, LEGEND_DENIED,  # noqa: E402
                                  LEGEND_OUTLINE, LEGEND_SOLID, LEGEND_TITLE)

TOPICS = ["private-request-path", "gateway-to-runtimes", "kb-ingestion"]
ID_RE = re.compile(r"^(?:[gne][0-9]{1,3}|i[0-9]{1,3}-.+|gac-arrow(?:-denied)?(?:-rev)?)$")


@pytest.fixture(scope="module")
def assets(tmp_path_factory):
    return Assets(str(fixture_assets_dir(tmp_path_factory.mktemp("cat"))))


def _render(topic, assets):
    s = _scene(topic)
    lay = best_layout(s, pass_score=85, max_attempts=3, deadline=1e18).layout
    icons = {n.id: assets.icon(n.icon) for n in s.nodes}
    gi = {g.id: assets.group_icon(g.type) for g in s.groups}
    colors = {"public-subnet": assets.color("PublicSubnet"), "account": assets.color("AWSAccount")}
    return s, lay, emit_svg(lay, icons, gi, colors), emit_drawio(lay, topic, s.topic_title, icons, gi, colors), icons, gi


def _allowed_strings(s):
    fixed = {CAPTION, LEGEND_TITLE, LEGEND_SOLID, LEGEND_DASHED, LEGEND_DENIED, LEGEND_OUTLINE,
             s.topic_title, ""}
    labels = {e.label for e in s.groups + s.nodes + s.edges if e.label}
    return fixed, labels


@pytest.mark.parametrize("topic", TOPICS)
def test_svg_parses_and_is_safe(topic, assets):
    s, lay, svg, _d, _i, _g = _render(topic, assets)
    root = DET.fromstring(svg)
    low = svg.lower()
    for bad in ("<script", "<foreignobject", "<image", "<style"):
        assert bad not in low
    assert not re.search(r"\son[a-z]+=", svg)
    assert all(m.startswith("#") for m in re.findall(r"url\(([^)]*)\)", svg))
    assert all(h.startswith("#") for h in re.findall(r'href="([^"]*)"', svg))
    fixed, labels = _allowed_strings(s)
    words = set()
    for lab in labels:
        words.update(lab.split(" "))
    for el in root.iter():
        if el.tag.endswith("text"):
            t = el.text or ""
            assert t in fixed or t in labels or all(w in words or w.endswith("...") for w in t.split(" "))
        if "id" in el.attrib:
            assert ID_RE.match(el.attrib["id"]), el.attrib["id"]
    assert CAPTION in svg


@pytest.mark.parametrize("topic", TOPICS)
def test_drawio_parses_icons_and_ids(topic, assets):
    s, lay, _svg, drawio, icons, gi = _render(topic, assets)
    root = DET.fromstring(drawio)
    assert "mxgraph.aws4" not in drawio
    cells = list(root.iter("mxCell"))
    for c in cells:
        assert ID_RE.match(c.attrib["id"]), c.attrib["id"]
    image_cells = [c for c in cells if "image=data:image/svg+xml," in c.attrib.get("style", "")]
    n_group_icons = sum(1 for g in s.groups if gi[g.id] is not None)
    assert len(image_cells) == len(s.nodes) + n_group_icons
    catalog_bodies = {ref.standalone for ref in list(icons.values()) + [r for r in gi.values() if r]}
    for c in image_cells:
        enc = c.attrib["style"].split("image=data:image/svg+xml,", 1)[1].rstrip(";")
        assert not re.search(r'[;="<>]', enc)
        assert unquote(enc) in catalog_bodies
    fixed, labels = _allowed_strings(s)
    for c in cells:
        v = c.attrib.get("value")
        if v is None:
            continue
        parts = v.split("<br>")
        assert v in fixed or v in labels or all(p in " ".join(labels) for p in parts)
    assert CAPTION in drawio


def test_group_styles_and_legend(assets):
    s, lay, svg, drawio, _i, _g = _render("private-request-path", assets)
    for gtype in ("aws-cloud", "region", "vpc", "private-subnet"):
        color, dashed = GROUP_STYLE[gtype]
        assert f"strokeColor={color};fillColor=none;dashed={1 if dashed else 0}" in drawio
    assert "#C7243A" in svg and LEGEND_DENIED in svg and LEGEND_DASHED in svg
    assert 'stroke-dasharray="6 4"' in svg


@pytest.mark.parametrize("topic", TOPICS)
def test_svg_structure_is_measurable(topic, assets):
    """The SVG carries the figure's structure, so an outside checker can measure it from the
    file alone: nested groups with their border rect first, nodes as titled groups with an
    `overflow="visible"` icon tile, arrows in `connectors` with marker heads, labels as halo text
    in `arrow-labels` keyed to their arrow's index."""
    s, lay, svg, _d, _i, _g = _render(topic, assets)
    tiles = re.findall(r'overflow="visible" x="[-\d.]+" y="[-\d.]+" width="48"', svg)
    assert len(tiles) == len(s.nodes)
    for g in s.groups:
        assert re.search(r'data-name="%s" data-kind="%s"><title>[^<]*</title><rect ' %
                         (re.escape(g.label), re.escape(g.type)), svg)
    conn = svg.split('data-name="connectors"', 1)[1].split('data-name="arrow-labels"', 1)[0]
    paths = re.findall(r'<path d="([^"]+)"([^>]*)>', conn)
    assert len(paths) == len(s.edges) and all("marker-end" in rest for _d, rest in paths)
    labels = svg.split('data-name="arrow-labels"', 1)[1]
    idx = [int(i) for i in re.findall(r'data-arrow="(\d+)"', labels)]
    assert len(idx) == sum(1 for e in s.edges if e.label) and all(0 <= i < len(paths) for i in idx)
    assert labels.count('paint-order="stroke"') == len(idx)


def test_optional_nodes_get_outline_and_legend(assets):
    s, lay, svg, drawio, _i, _g = _render("gateway-to-runtimes", assets)
    opt = [n for n in lay.nodes if n.optional]
    assert opt and LEGEND_OUTLINE in svg and LEGEND_OUTLINE in drawio
    # a grouping (AgentCore) is solid, so the legend has one dashed-box row only (opt-in)
    assert "grouping only" not in svg and "grouping only" not in drawio
    assert svg.count(f'stroke="#5A6B86" stroke-width="1.5" stroke-dasharray="6 4"/><svg') == len(opt)


def test_deterministic(assets):
    a = _render("kb-ingestion", assets)
    b = _render("kb-ingestion", assets)
    assert hashlib.sha256(a[2].encode()).hexdigest() == hashlib.sha256(b[2].encode()).hexdigest()
    assert a[3] == b[3]


def test_prefix_ids():
    body = '<clipPath id="c"><rect/></clipPath><g clip-path="url(#c)"><use href="#c"/></g>'
    out = prefix_ids(body, "i3-")
    assert 'id="i3-c"' in out and "url(#i3-c)" in out and 'href="#i3-c"' in out


def test_public_subnet_fallback_matches_fixture(assets):
    assert assets.color("PublicSubnet") == GROUP_STYLE["public-subnet"][0]
    assert assets.color("AWSAccount") == GROUP_STYLE["account"][0]


def test_drawio_size_bound_with_max_icons(tmp_path):
    a = Assets(str(fixture_assets_dir(tmp_path, pad_bytes=12_000)))
    ref = a.icon("AmazonVPCEndpoints")
    assert len(data_uri(ref)) * 36 < 2 * 1024 * 1024
