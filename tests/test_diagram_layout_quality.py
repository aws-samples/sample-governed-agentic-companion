"""Layout engine and the measured quality check (design D6.1 to D6.4, D14)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.base_agent import BaseAgent  # noqa: E402
from agents.diagram.compose import collect_facts, compose  # noqa: E402
from agents.diagram.layout import (Box, EdgeGeo, GroupGeo, Layout, NodeGeo, TextGeo,  # noqa: E402
                                   best_layout, build_layout, finish_canvas, text_width, wrap)
from agents.diagram.limits import resolve_limits  # noqa: E402
from agents.diagram.quality import MESSAGES, NOT_APPLICABLE, check, improves, score_of  # noqa: E402
from agents.diagram.route import route_edges  # noqa: E402
from agents.diagram.topics import merge_topics  # noqa: E402
from agents.specialists import build_specialists  # noqa: E402


def _scene(topic_id):
    class D(BaseAgent):
        knowledge_dirs = ["tier_a/diagram", "tier_b/diagram"]
    d = D("diagram")
    reg = build_specialists()
    topics, _ = merge_topics(d._knowledge)
    t = next(t for t in topics if t.id == topic_id)
    c = collect_facts(d, {n: a for n, (a, _) in reg.items() if n != "diagram"}, t.id)
    return compose(t.id, t.title, "", c, resolve_limits({})).scene


TOPICS = ["private-request-path", "gateway-to-runtimes", "kb-ingestion", "bedrock-calls"]


@pytest.mark.parametrize("topic", TOPICS)
def test_shipped_topics_pass(topic):
    r = best_layout(_scene(topic), pass_score=85, max_attempts=3, deadline=1e18)
    q = r.quality
    assert q.passed and q.score >= 85 and not q.hard, [(f.code, f.severity) for f in q.findings]
    assert r.layout.width <= 992 and r.layout.height <= 1400


def _geometry(topic, cand):
    s = _scene(topic)
    lay = build_layout(s, cand)
    route_edges(lay, s)
    finish_canvas(lay, s)
    return (lay.width, lay.height, [(n.gid, n.icon) for n in lay.nodes],
            [(e.gid, tuple(e.points), e.label_box) for e in lay.edges])


@pytest.mark.parametrize("topic", TOPICS)
def test_layout_deterministic(topic):
    assert _geometry(topic, "C") == _geometry(topic, "C")


def test_deadline_stops_loop_with_fake_clock():
    s = _scene("private-request-path")
    ticks = iter([0.0, 100.0, 200.0, 300.0])
    r = best_layout(s, pass_score=100, max_attempts=3, deadline=50.0, clock=lambda: next(ticks))
    assert r.attempts == 1 and r.timed_out


def test_candidate_loop_keeps_best_and_draft_when_none_pass():
    r = best_layout(_scene("private-request-path"), pass_score=100, max_attempts=3, deadline=1e18)
    assert r.attempts == 3 and not r.quality.passed


def test_chain_alignment_straightens_one_to_one():
    lay = build_layout(_scene("kb-ingestion"), "A")
    s = _scene("kb-ingestion")
    route_edges(lay, s)
    e = next(e for e in lay.edges if e.key == "s3-endpoint-to-eni")
    assert len(e.points) == 2


def test_wrap_and_measure():
    lines, trunc = wrap("Orchestrator runtime", 104)
    assert lines == ["Orchestrator", "runtime"] and not trunc
    lines, trunc = wrap("Supercalifragilisticexpialidocious", 104)
    assert trunc and lines[0].endswith("...")
    assert text_width("mmm") > text_width("iii")


# -- crafted geometry, one per code ----------------------------------------------------------

def N(gid, x, y, key=None, parent=None, lines=("Node",), truncated=False):
    icon = Box(x, y, 48, 48)
    cap = Box(x - 8, y + 54, 64, 16 * len(lines))
    return NodeGeo(gid, key or gid, "Node", list(lines), truncated, icon, cap,
                   Box(x - 32, y, 112, 88), "", parent)


def G(gid, x, y, w, h, parent=None, gtype="generic", depth=0, title_bound=False):
    return GroupGeo(gid, gid, gtype, "Group", Box(x, y, w, h), Box(x + 8, y + 12, 40, 16), None,
                    parent, depth, title_bound)


def E(gid, src, dst, pts, label="", lb=None, optional=False, style="data", bi=False):
    return EdgeGeo(gid, gid, src, dst, list(pts), optional, style, bi, label, lb)


def L(nodes=(), groups=(), edges=(), w=900, h=600, legend=("solid",), title=None, tsw=0.0):
    lay = Layout(w, h, "A", list(nodes), list(groups), list(edges))
    lay.legend_lines = [(k, (0, 0, 0, 0)) for k in legend]
    if title:
        lay.title = TextGeo(title, Box(16, 16, 100, 22), 18, True)
    lay.title_sets_width = tsw
    return lay


def codes(lay, **kw):
    return {(f.code, f.severity) for f in check(lay, **kw).findings}


A, B, C3 = N("n1", 100, 100), N("n2", 400, 100), N("n3", 250, 100)

CASES = {
    ("DQ-P101", "fail"): L([A, B, C3], edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)])]),
    ("DQ-P102", "fail"): L([A, B], edges=[E("e1", "n1", "n2", [(148, 124), (148, 124), (400, 124)])]),
    ("DQ-L101", "fail"): L([A, N("n2", 110, 110)]),
    ("DQ-L102", "fail"): L([A], [G("g1", 120, 50, 300, 300)]),
    ("DQ-L103", "fail"): L([A, B], [G("g1", 300, 50, 300, 300)],
                           [E("e1", "n1", "n2", [(148, 124), (400, 124)], "x", Box(290, 100, 30, 16))]),
    ("DQ-L104", "warn"): L([N("n1", 100, 100), N("n2", 300, 100), N("n3", 100, 150), N("n4", 300, 150)]),
    ("DQ-L105", "warn"): L([N("n1", 100, 100), N("n2", 200, 100), N("n3", 600, 100)]),
    ("DQ-L106", "warn"): L([N("n1", 100, 100)], [G("g1", 300, 140, 200, 200)]),
    ("DQ-L107", "warn"): L([A], w=900, h=200),
    ("DQ-L108", "fix"): L([N("n1", 100, 100), N("n2", 500, 100)]),
    ("DQ-L108", "warn"): L([N("n1", 100, 100), N("n2", 360, 100), N("n3", 620, 100)]),
    ("DQ-L109", "fail"): L([A, B, N("n3", 360, 160)], edges=[E("e1", "n1", "n2", [(148, 124), (352, 124), (352, 210), (400, 210)])]),
    ("DQ-L109", "warn"): L([A, N("n2", 100, 300)], edges=[E("e1", "n2", "n1", [(124, 300), (124, 174)])]),
    ("DQ-L110", "warn"): L([], [G("g1", 50, 50, 200, 200), G("g2", 300, 50, 200, 260)]),
    ("DQ-L111", "fix"): L([N("n1", 100, 100, parent="g1")], [G("g1", 50, 50, 600, 500)]),
    ("DQ-L112", "warn"): L([A, N("n2", 400, 300)], edges=[E("e1", "n1", "n2", [(148, 124), (160, 124), (160, 324), (400, 324)])]),
    ("DQ-L113", "fail"): L([A, B], edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)], "1 API calls", Box(200, 100, 70, 16))]),
    ("DQ-L114", "fix"): L([A, N("n2", 400, 200)], edges=[E("e1", "n1", "n2", [(148, 124), (270, 124), (270, 224), (400, 224)])]),
    ("DQ-L115", "fix"): L([A], w=1300, h=800),
    ("DQ-L116", "fail"): L([A, B, N("n3", 100, 300), N("n4", 400, 300)],
                           edges=[E("e1", "n1", "n2", [(148, 124), (250, 124), (250, 324), (400, 324)]),
                                  E("e2", "n3", "n4", [(148, 324), (252, 324), (252, 128), (400, 128)])]),
    ("DQ-L117", "fix"): L([A, B, N("n3", 250, 60)], edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)])]),
    ("DQ-L118", "fix"): L([A, B], [G("g1", 160, 112, 200, 200)], [E("e1", "n1", "n2", [(148, 124), (400, 124)])]),
    ("DQ-L119", "fix"): L([A, N("n2", 300, 200)], edges=[E("e1", "n1", "n2", [(148, 124), (324, 124), (324, 200)])]),
    ("DQ-L120", "fix"): L([A, B, N("n3", 250, 128)], edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)])]),
    ("DQ-L121", "fix"): L([A, B, N("n3", 250, 300)], edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)], "x", Box(250, 300, 30, 16))]),
    ("DQ-L122", "fail"): L([A, B, N("n3", 400, 300)],
                           edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)]),
                                  E("e2", "n3", "n2", [(400, 300), (400, 124)])]),
    ("DQ-L123", "warn"): L([], [G("g1", 50, 50, 200, 100, gtype="private-subnet"),
                                G("g2", 50, 200, 210, 100, gtype="private-subnet")]),
    ("DQ-L124", "warn"): L([A], title="T", tsw=64),
    ("DQ-L125", "warn"): L([A, B, N("n3", 100, 300), N("n4", 400, 300)],
                           edges=[E("e1", "n1", "n2", [(148, 124), (250, 124), (250, 324), (400, 324)]),
                                  E("e2", "n3", "n4", [(148, 324), (260, 324), (260, 128), (400, 128)])]),
    ("DQ-L126", "warn"): L([A, N("n2", 158, 100)], edges=[E("e1", "n1", "n2", [(148, 124), (158, 124)])]),
    ("DQ-R101", "fail"): L([A], w=2200, h=1800),
    ("DQ-R102", "fix"): L([A], w=1500, h=1200),
    ("DQ-R103", "fix"): L([A], w=2000, h=1600),
    ("DQ-R104", "warn"): L([A], w=1400, h=1200),
    ("DQ-A101", "fix"): L([A], w=1100, h=800),
    ("DQ-A102", "fix"): L([N("n1", 100, 100, parent="g1")], [G("g1", 36, 50, 220, 160)]),
    ("DQ-A103", "fix"): L([A], w=1000, h=800),
    ("DQ-G101", "fail"): L([], [G("g1", 50, 50, 200, 200), G("g2", 150, 150, 200, 200)]),
    ("DQ-LG101", "fail"): L([A, B], edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)], optional=True)]),
    ("DQ-X101", "fix"): L([A, B, N("n3", 250, 0), N("n4", 250, 300)],
                           edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)]),
                                  E("e2", "n3", "n4", [(274, 48), (274, 300)])]),
    ("DQ-T101", "warn"): L([N("n1", 100, 100, truncated=True)]),
}


@pytest.mark.parametrize("key", sorted(CASES), ids=lambda k: f"{k[0]}-{k[1]}")
def test_crafted_geometry_triggers_code(key):
    assert key in codes(CASES[key])


@pytest.mark.parametrize("code", ["DQ-I101", "DQ-I103"])
def test_icon_warnings(code):
    assert (code, "warn") in codes(L([A]), icon_warnings={"n1": code})


def test_every_code_has_a_case_or_is_not_applicable():
    covered = {c for c, _ in CASES} | {"DQ-I101", "DQ-I103"}
    assert covered == set(MESSAGES)
    assert all(len(m) <= 48 for _, m in MESSAGES.values())


def test_not_applicable_codes_never_emitted():
    emitted = set()
    for lay in CASES.values():
        emitted |= {c for c, _ in codes(lay)}
    for code in NOT_APPLICABLE:
        assert code not in emitted and f"DQ-{code}" not in MESSAGES


def test_score_formula_and_improves():
    from agents.diagram.quality import QFinding, QualityResult
    fs = [QFinding("DQ-L101", "fail"), QFinding("DQ-L111", "fix")] + [QFinding("DQ-X101", "warn")] * 20
    assert score_of(fs) == 100 - 15 - 6 - 15
    a = QualityResult(90, True, 85, [])
    b = QualityResult(80, False, 85, [QFinding("DQ-L111", "fix")])
    assert improves(a, b) and not improves(b, a)


def test_long_arrow_is_must_fix():
    """A straight run over 2 pitches and 240 px of empty line is a hard finding (L108)."""
    lay = L([N("n1", 100, 100), N("n2", 260, 100), N("n3", 420, 100), N("n4", 900, 300)],
            edges=[E("e1", "n1", "n4", [(148, 124), (700, 124), (700, 324), (900, 324)])])
    assert ("DQ-L108", "fix") in codes(lay)


def test_crossing_is_hard():
    from agents.diagram.quality import QFinding, _is_hard
    assert _is_hard(QFinding("DQ-X101", "fix"))


def test_denied_only_caller_sits_below_every_group():
    """The outside caller is drawn under its target, below every boundary, so the denied arrow is
    one straight run that never travels inside a network it does not belong to."""
    for topic in ("private-request-path", "gateway-to-runtimes"):
        s = _scene(topic)
        lay = build_layout(s, "B")
        route_edges(lay, s)
        caller = lay.node("outside-caller")
        assert caller.icon.y > max(g.box.b for g in lay.groups)
        e = next(e for e in lay.edges if e.style == "denied")
        assert len(e.points) == 2 and e.points[0][0] == e.points[1][0]


def test_report_lists_every_element_with_its_citation(tmp_path, monkeypatch):
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from diagram_fixtures import fixture_assets_dir
    monkeypatch.delenv("GAC_DIAGRAM_ASSETS_DIR", raising=False)
    d = build_specialists()["diagram"][0]
    d._assets_dir = str(fixture_assets_dir(tmp_path))   # local mode, test-fixture icons
    r = d.render_request("show our KB ingestion", deliver=True)
    rep = json.loads(r.files["report.json"])
    els = rep["elements"]
    assert len(els) == rep["traceability"]["rendered"]
    assert all(e["kb_refs"] and e["refs"] for e in els)
    svg = r.files["svg"].decode()
    assert all(f'id="{e["id"]}"' in svg for e in els)


# -- arrows through their own end node (review DR-01, DR-02, DR-03) ---------------------------

def test_p101_own_source_cut_fails():
    # DR-01 geometry: the egress arrow leaves its source's left face, loops down and runs right
    # along the bottom of the source icon (between icon and caption), through its own node
    src, dst = N("n1", 96, 168), N("n2", 384, 192)
    lay = L([src, dst], edges=[E("e1", "n1", "n2",
                                 [(96, 192), (64, 192), (64, 216), (384, 216)])])
    assert ("DQ-P101", "fail") in codes(lay)
    assert not check(lay).passed


def test_p101_own_target_cut_fails():
    # DR-02 analogue at the arrow's end: a run under the target icon before it enters
    src, dst = N("n1", 96, 100), N("n2", 400, 100)
    lay = L([src, dst], edges=[E("e1", "n1", "n2",
                                 [(144, 124), (200, 124), (200, 148), (480, 148), (480, 124),
                                  (448, 124)])])
    assert ("DQ-P101", "fail") in codes(lay)


def test_p101_port_run_is_not_a_cut():
    # leaving from the right face and entering the left face is the normal case
    lay = L([N("n1", 100, 100), N("n2", 400, 100)],
            edges=[E("e1", "n1", "n2", [(148, 124), (400, 124)])])
    assert ("DQ-P101", "fail") not in codes(lay)


def test_l120_run_along_own_target_is_fix():
    # DR2-01 geometry: the arrow runs 8 px above its target's icon across the whole node (on
    # the dashed opt-in outline), overshoots and hooks back into the right face
    src, dst = N("n1", 96, 208), N("n2", 400, 240)
    lay = L([src, dst], edges=[E("e1", "n1", "n2",
                                 [(144, 232), (480, 232), (480, 264), (448, 264)])])
    assert ("DQ-L120", "fix") in codes(lay)


def test_l120_l_into_top_face_is_clean():
    src, dst = N("n1", 96, 208), N("n2", 400, 288)
    lay = L([src, dst], edges=[E("e1", "n1", "n2", [(144, 232), (424, 232), (424, 288)])])
    assert ("DQ-L120", "fix") not in codes(lay)


def test_bedrock_calls_apply_guardrail_is_one_bend_into_the_top_face():
    # review DR2-01: ApplyGuardrail neither grazes the guardrail nor overshoots past it
    from agents.diagram.route import grazes_own
    r = best_layout(_scene("bedrock-calls"), pass_score=85, max_attempts=3, deadline=1e18)
    by = {n.key: n for n in r.layout.nodes}
    e = next(e for e in r.layout.edges if e.key == "bedrock-endpoint-to-guardrail")
    g = by["bedrock-guardrail"]
    assert len(e.points) == 3 and e.points[-1][1] == g.icon.y, e.points
    assert max(x for x, _ in e.points) <= g.icon.r
    assert not grazes_own(e.points, g, False, True)
    assert by["bedrock-guardrail"].label == "Bedrock Guardrails"


@pytest.mark.parametrize("topic", TOPICS)
def test_shipped_captions_fit_without_truncation(topic):
    for n in _scene(topic).nodes:
        assert not wrap(n.label, 104)[1], n.label


@pytest.mark.parametrize("topic", TOPICS)
@pytest.mark.parametrize("cand", ["A", "B", "C"])
def test_router_never_grazes_its_own_end(topic, cand):
    from agents.diagram.route import grazes_own
    s = _scene(topic)
    lay = build_layout(s, cand)
    route_edges(lay, s)
    by = {n.key: n for n in lay.nodes}
    for e in lay.edges:
        assert not grazes_own(e.points, by[e.dst], False, True), (topic, cand, e.key)


@pytest.mark.parametrize("topic", TOPICS)
@pytest.mark.parametrize("cand", ["A", "B", "C"])
def test_router_never_cuts_its_own_end(topic, cand):
    from agents.diagram.route import cuts_own
    s = _scene(topic)
    lay = build_layout(s, cand)
    route_edges(lay, s)
    by = {n.key: n for n in lay.nodes}
    for e in lay.edges:
        assert not cuts_own(e.points, by[e.src], True, False), (topic, cand, e.key)
        assert not cuts_own(e.points, by[e.dst], False, True), (topic, cand, e.key)
