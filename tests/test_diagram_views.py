"""The view planner, declines and footer lines (design D6, D7.1, D5.3; plan items 7 and 8)."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from diagram_fixtures import shipped_catalogue  # noqa: E402
from agents.diagram import decline as D  # noqa: E402
from agents.diagram import footer as F  # noqa: E402
from agents.diagram.closure import Closed, close  # noqa: E402
from agents.diagram.limits import Limits  # noqa: E402
from agents.diagram.planner_det import plan_request  # noqa: E402
from agents.diagram.views import (BoundsDecline, CoverageError, View, assign_names,  # noqa: E402
                                  eligible, plan_views, preset_view_facts, slug)
from agents.governance_gate import GovernanceGate  # noqa: E402

_CATS = {}


def _cat(fixture=""):
    if fixture not in _CATS:
        _CATS[fixture] = shipped_catalogue(fixture)
    return _CATS[fixture]


def _views(prompt, fixture="", passes=None, limits=Limits()):
    """Plan views; the generic try 'passes' per the callback (default: never tried)."""
    cat = _cat(fixture)
    p = plan_request(prompt, cat)
    c = close(p, cat)
    fn = None if passes is None else (lambda v: (passes, "layout"))
    return cat, c, plan_views(c, cat, limits, title=p.title, selected=p.selected, layout_fn=fn)


# -- envelope (D6.2) ---------------------------------------------------------------------------

def test_envelope_bounds():
    cat = _cat()
    small = close(plan_request("what talks to Bedrock", cat), cat)
    assert eligible(small, cat)
    e2e = close(plan_request("draw the architecture end to end", cat), cat)
    assert not eligible(e2e, cat)                    # end-to-end never takes the generic try
    seven = close(plan_request("show how a developer request reaches the knowledge base", cat),
                  cat)
    assert len(seven.nodes) == 7 and not eligible(seven, cat)   # > 6 nodes
    many_edges = Closed(nodes=small.nodes, groups=[], edges=cat.edges()[:9], scope="focus",
                        mode="generic")
    assert not eligible(many_edges, cat)             # > 8 edges


def test_envelope_rejects_duplicate_label_and_icon(monkeypatch):
    cat = _cat()
    c = close(plan_request("what talks to Bedrock", cat), cat)
    a, b = c.nodes[0], c.nodes[1]
    monkeypatch.setattr(cat.facts[b], "label", cat.facts[a].label)
    monkeypatch.setattr(cat.facts[b], "icon", cat.facts[a].icon)
    assert not eligible(c, cat)


def test_generic_view_when_the_try_passes():
    _cat_, _c, vp = _views("what talks to Bedrock", passes=True)
    assert [v.id for v in vp.views] == ["generic"] and vp.generic_tried
    assert vp.views[0].rendered == "layout" and not vp.views[0].hints


def test_failed_generic_try_falls_back_to_the_cover():
    # golden row 12 with a failing generic try: presets cover it (row 6 now reads the KB
    # bucket too, past the generic envelope)
    _cat_, _c, vp = _views("draw how a builder signs in", passes=False)
    assert vp.generic_tried and [v.id for v in vp.views] == ["private-request-path",
                                                             "gateway-to-runtimes"]


# -- preset cover, order and ties (D6.3 steps 3 and 6) ----------------------------------------

def test_cover_order_and_preset_order():
    cat, _c, vp = _views("draw gac pvt architecture end to end")
    assert [v.id for v in vp.views] == ["private-request-path", "gateway-to-runtimes",
                                        "kb-ingestion", "bedrock-calls"]
    assert all(v.kind == "preset" and v.hints for v in vp.views)


def test_cover_tie_breaks_by_preset_order():
    # golden row 14: after BC, PRP, GTR and KBI tie at 5 and preset order picks PRP
    _cat_, _c, vp = _views("draw the vpc endpoints")
    assert [v.id for v in vp.views] == ["private-request-path", "gateway-to-runtimes",
                                        "kb-ingestion", "bedrock-calls"]


def test_preset_plan_is_one_preset_view():
    cat, c, vp = _views("draw our private network path")
    assert c.mode == "preset" and [v.id for v in vp.views] == ["private-request-path"]
    assert vp.views[0].facts == preset_view_facts(cat, "private-request-path")


def test_context_status_for_closure_excluded_facts():
    # golden row 11: the three other-flow edges are drawn by the preset views as context
    _cat_, _c, vp = _views("what does the builder ide connect to")
    for e in ("cognito-to-orchestrator", "cognito-to-gateway", "gateway-to-mcp-runtime"):
        assert vp.status["e:" + e] == ("context", "")
    assert vp.context_count >= 3


def test_excluded_status_stays_when_no_view_draws_it():
    # golden row 12: one generic view; the other-flow edges are in no view
    _cat_, _c, vp = _views("draw how a builder signs in", passes=True)
    assert vp.status["e:dataplane-to-orchestrator"] == ("excluded", "other-flow")
    assert vp.status["e:gateway-endpoint-to-gateway"] == ("excluded", "other-flow")
    assert vp.status["n:kb-bucket"] == ("excluded", "not-selected")


# -- flow partitions and merge (D6.3 step 4) ---------------------------------------------------

def test_flow_partition_and_merge_orders_fixture():
    cat, c, vp = _views("draw the orders end to end", fixture="orders")
    assert [(v.id, v.title) for v in vp.views] == [("web-flow", "Web flow"),
                                                   ("async-flow", "Async flow")]
    web, async_ = vp.views
    assert {q[2:] for q in web.nodes} == {"browser", "storefront", "orders-api", "orders-db"}
    assert {q[2:] for q in web.edges} == {"browser-to-storefront", "storefront-to-api",
                                          "api-to-db"}
    assert {q[2:] for q in async_.edges} == {"api-to-queue", "queue-to-fulfilment",
                                             "fulfilment-to-archive"}
    assert all(not v.hints for v in vp.views)


def test_four_view_bound_declines_with_flow_suggestions():
    _cat_, _c, r = _views("draw everything end to end", fixture="overbroad")
    assert isinstance(r, BoundsDecline) and (r.needed, r.limit) == (8, 4)
    assert [s for s, _ in r.suggestions][:5] == [
        "draw the alpha flow", "draw the bravo flow", "draw the charlie flow",
        "draw the delta flow", "draw the echo flow"]


def test_max_views_one_never_splits():
    _cat_, _c, r = _views("draw gac pvt architecture end to end", limits=Limits(max_views=1))
    assert isinstance(r, BoundsDecline) and r.needed == 4 and r.limit == 1


# -- coverage invariant (D6.3 step 8) ----------------------------------------------------------

@pytest.mark.parametrize("prompt,fixture,passes", [
    ("draw gac pvt architecture end to end", "", None),
    ("show how a developer request reaches the knowledge base", "", None),
    ("diagram our security boundaries", "", None),
    ("what talks to Bedrock", "", True),
    ("show the gateway, runtimes and what they depend on", "", False),
    ("draw the vpc endpoints", "", None),
    ("draw the orders end to end", "orders", None),
])
def test_coverage_invariant(prompt, fixture, passes):
    cat, c, vp = _views(prompt, fixture, passes)
    drawn = {q for v in vp.views for q in v.facts}
    for q in c.nodes + c.edges:
        assert q in drawn, q
    assert set(vp.status) == set(cat.facts)
    for q, (s, r) in vp.status.items():
        assert s in ("selected", "added", "context", "excluded")
        assert (s == "excluded") == bool(r) or s == "added"


def test_coverage_error_when_a_fact_is_lost(monkeypatch):
    import agents.diagram.views as V
    cat = _cat()
    c = close(plan_request("draw gac pvt architecture end to end", cat), cat)
    monkeypatch.setattr(V, "preset_view_facts", lambda cat_, pid: [])
    real = V._merge_flows
    monkeypatch.setattr(V, "_merge_flows", lambda cat_, parts: real(cat_, parts)[:1])
    with pytest.raises(CoverageError):          # a lost partition is a bug, never silent
        plan_views(c, cat, Limits())


# -- names and titles (D6.4) -------------------------------------------------------------------

def test_names_single_and_multi():
    v = [View("private-request-path", "preset", "T", [], preset="private-request-path")]
    assign_names(v, caller_name="", title="Private request path, no gateway")
    assert v[0].name == "private-request-path"
    g = [View("generic", "generic", "What talks to Amazon Bedrock", [])]
    assign_names(g, caller_name="", title="What talks to Amazon Bedrock")
    assert g[0].name == "what-talks-to-amazon-bedrock"
    m = [View(i, "preset", i, [], preset=i) for i in ("private-request-path", "kb-ingestion")]
    assign_names(m, caller_name="", title="End-to-end architecture")
    assert [x.name for x in m] == ["end-to-end-architecture-1-private-request-path",
                                   "end-to-end-architecture-2-kb-ingestion"]
    assign_names(m, caller_name="mine", title="ignored")
    assert m[0].name == "mine-1-private-request-path"
    assert len(slug("x" * 80, 32)) == 32


# -- the deadline (D6.3 step 9) ----------------------------------------------------------------

def test_render_deadline_omits_later_views(tmp_path, monkeypatch):
    from diagram_fixtures import fixture_assets_dir
    from agents.diagram.assets import Assets
    from agents.specialists import build_specialists
    from agents.specialists import diagram_specialist as DS

    now = [0.0]
    d = build_specialists()["diagram"][0]
    peers = {n: a for n, (a, _) in build_specialists().items() if n != "diagram"}
    d.bind_peers(lambda: peers)
    d._assets = Assets(str(fixture_assets_dir(tmp_path)))
    d.clock = lambda: now[0]
    real = DS.emit_svg

    def slow_svg(*a, **k):
        out = real(*a, **k)
        now[0] += 1000.0          # the first view uses up the whole deadline
        return out

    monkeypatch.setattr(DS, "emit_svg", slow_svg)
    rr = d.render_request("draw gac pvt architecture end to end", deliver=True)
    assert len(rr.bundles) == 1
    assert rr.raw_text.count("- not delivered (render_deadline)") == 3
    assert "  Diagram views: 1 (private-request-path), 0 context elements" in rr.footer_lines


# -- declines (D7.1, M8) -----------------------------------------------------------------------

def test_decline_text_pinned_lines():
    t = D.no_plan_text(reason="no-match", terms=["memory"], unmatched=["memory"],
                       example_label="gateway", sugg=["draw our private network path"])
    assert t.split("\n") == [
        "[diagram] I can't draw this from the knowledge base, so no file was produced.",
        "Asked for: nothing I recognise",
        'Missing: no diagram facts mention "memory"',
        "I can draw, for example:",
        '- "draw our private network path"',
        'Add facts for your own architecture: docs/ARCHITECTURE.md, "Describe your topology '
        'for diagrams".']
    t = D.no_plan_text(reason="no-anchor", terms=[], unmatched=[], example_label="gateway",
                       sugg=["x"])
    assert ('Missing: no component named in the request; name one, for example "gateway"'
            in t)
    t = D.no_plan_text(reason="no-match", terms=["bucket", "kafka"], unmatched=["kafka"],
                       example_label="g", sugg=["x"])
    assert 'Asked for: "bucket"' in t


def test_suggestion_candidates_order():
    cat = _cat()
    c = [p for p, _ in D.candidates(cat)]
    assert c[:4] == ["draw our private network path",
                     "diagram how the gateway reaches the runtimes", "show our KB ingestion",
                     "draw the bedrock calls"]
    # the end-to-end prompt comes right after the presets, so it fits the 5 slots (NL-1)
    assert c[4] == D.E2E and all(p.startswith("what talks to ") for p in c[5:]) and len(c) == 8


def test_m8_keep_rule():
    ok = D.Replay("preset", "kb-ingestion", [], 1, True)
    assert D.keeps("kb-ingestion", ok)
    assert not D.keeps("bedrock-calls", ok)                                  # wrong preset
    assert not D.keeps("kb-ingestion", D.Replay("deterministic", "", [], 1, True))
    assert not D.keeps("", D.Replay("deterministic", "", ["opt"], 1, True))  # unmatched noise
    assert not D.keeps("", D.Replay("deterministic", "", [], 2, False))      # a draft view
    assert not D.keeps("", None) and D.keeps("", D.Replay("deterministic", "", [], 1, True))


def test_kb_lines():
    assert D.not_in_kb_line([]) == ""
    assert D.not_in_kb_line(["kafka", "cluster"]) == \
        'Not in the knowledge base: "kafka", "cluster"'
    assert D.drawn_from_line("") == D.DRAWN_FROM
    assert D.drawn_from_line("Note.") == f"{D.DRAWN_FROM} Note."


# -- footer lines (D5.3) -----------------------------------------------------------------------

FALLBACKS = ("request-secret", "no-model", "no-guardrail", "no-boto3", "breaker-open",
             "planner-timeout", "planner-network", "planner-denied", "planner-invalid-request",
             "guardrail-intervened", "plan-truncated", "plan-missing", "plan-invalid")
IDS = ("private-request-path", "gateway-to-runtimes", "kb-ingestion", "bedrock-calls")


def _variants():
    out = [F.planner_line("preset"), F.planner_line("deterministic"), F.SOURCE_LINE,
           F.planner_line("llm", cache="hit"), F.planner_line("llm", cache="miss"),
           F.facts_line(40, 6, 3, 2)]
    out += [F.planner_line("deterministic", fallback=c) for c in FALLBACKS]
    out += [F.views_line(IDS[:n], n) for n in range(1, 5)]
    out += [F.views_line(["generic"], 0), F.views_line(["web-flow", "async-flow"], 0)]
    return out


def test_every_footer_variant_matches_the_pattern_and_appears():
    gate = GovernanceGate()
    lines = _variants()
    for line in lines:
        assert F.valid(line), line
    r = gate.enforce("[diagram] fine", agent="diagram", footer_lines=lines)
    assert not r.blocked
    body = r.response.split("\n")
    sources = next(i for i, x in enumerate(body) if x.startswith("  Sources"))
    status = next(i for i, x in enumerate(body) if x.startswith("  Gate status"))
    assert body[sources + 1:status] == lines
    assert "  Diagram planner: llm (model-selected facts, every element traced), cache miss" \
        in body
    assert "  Diagram planner: deterministic (fallback: planner-timeout)" in body


def test_long_view_ids_stay_within_the_pattern():
    line = F.views_line(["a" * 48, "b" * 48, "c" * 48, "d" * 48], 2)
    assert F.valid(line) and line.endswith("), 2 context elements")


def test_lines_for_decline_and_render():
    assert F.lines_for(planner="deterministic", rendered=False) == [
        "  Diagram planner: deterministic", F.SOURCE_LINE]
    r = F.lines_for(planner="preset", selected=10, view_ids=["kb-ingestion"])
    assert r[1] == ("  Diagram facts: 10 included (10 selected, 0 added by closure), 0 excluded, "
                    "0 unknown ids")


def test_audit_extra_filter():
    got = F.audit_extra({"planner": "deterministic", "included": 4, "views": True,
                         "fallback": "x;y", "label": "Prod DB", "input_tokens": 12})
    assert got == {"planner": "deterministic", "included": 4, "input_tokens": 12}
    assert re.match(r"^[a-z_]+$", "".join(F.AUDIT_KEYS).replace("-", ""))
