"""Subgraph closure (design D3.4; plan item 6): seed, endpoints, connect, induced edges with
other-flow, boundaries, isolated nodes, the closure cap and the selection bound (N15)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from diagram_fixtures import shipped_catalogue  # noqa: E402
from agents.diagram.catalogue import build_catalogue  # noqa: E402
from agents.diagram.closure import close  # noqa: E402
from agents.diagram.limits import MAX_SELECTION_NODES  # noqa: E402
from agents.diagram.plan import Plan  # noqa: E402
from agents.specialists.diagram_specialist import DiagramSpecialist  # noqa: E402


def _cat(facts, vocab=None):
    d = DiagramSpecialist()
    d._knowledge = {"tier_b/diagram/diagram_facts": {"diagram_facts": facts},
                    "tier_b/diagram/vocabulary": vocab or {
                        "roles": {"connector_tags": ["glue"]}}}
    return build_catalogue(d, {})


def _n(i, group=None, tags=None):
    f = {"kind": "node", "id": i, "label": f"Node {i}", "icon": "Client"}
    if group:
        f["group"] = group
    if tags:
        f["tags"] = tags
    return f


def _e(i, a, b, tags=None, **kw):
    f = {"kind": "edge", "id": i, "from": a, "to": b, **kw}
    if tags:
        f["tags"] = tags
    return f


def _s(qs):
    return {q[2:] for q in qs}


def test_group_member_endpoint_and_boundary():
    cat = _cat([{"kind": "group", "id": "outer", "type": "region", "label": "Outer"},
                {"kind": "group", "id": "inner", "type": "vpc", "label": "Inner", "parent": "outer"},
                _n("a", "inner"), _n("b", "inner"), _n("c"), _e("a-b", "a", "b"),
                _e("b-c", "b", "c")])
    c = close(Plan(selected=["g:inner", "e:b-c"], scope="focus"), cat)
    assert c.added_by == {"n:a": "closure:group-member", "n:b": "closure:group-member",
                          "n:c": "closure:endpoint", "e:a-b": "closure:path",
                          "g:outer": "closure:boundary"}
    assert _s(c.nodes) == {"a", "b", "c"} and _s(c.groups) == {"inner", "outer"}


def test_connect_adds_shortest_path_and_respects_cap():
    chain = [_n("a"), _n("m1"), _n("b"), _n("x"), _n("far"),
             _e("a-m1", "a", "m1", tags=["glue"]), _e("m1-b", "m1", "b", tags=["glue"]),
             _e("b-x", "b", "x"), _e("far-x", "far", "x", tags=["other"], optional=True)]
    cat = _cat(chain)
    c = close(Plan(selected=["n:a", "e:b-x", "n:far"], scope="focus"), cat)
    assert c.added_by["e:a-m1"] == c.added_by["e:m1-b"] == c.added_by["n:m1"] == "closure:path"
    # far's only link is tagged and optional (not admissible): it stays apart, then the
    # induced step lists that link as other-flow
    assert _s(c.nodes) == {"a", "m1", "b", "x", "far"}
    assert c.excluded == {"e:far-x": "other-flow"}


def test_connect_skipped_for_security_and_end_to_end():
    cat = _cat([_n("a", tags=["security"]), _n("m"), _n("b", tags=["security"]),
                _e("a-m", "a", "m", tags=["glue"]), _e("m-b", "m", "b", tags=["glue"])],
               {"roles": {"connector_tags": ["glue"], "security_tags": ["security"]}})
    c = close(Plan(selected=["n:a", "n:b"], scope="security"), cat)
    assert _s(c.nodes) == {"a", "b"} and c.edges == []


def test_connect_cap_of_eight_added_nodes():
    # 3 hops per component, 3 components far away: at most 8 added nodes in total
    facts = [_n("hub"), _n("hub2"), _e("h-h2", "hub", "hub2", tags=["glue"])]
    for k in range(5):
        facts += [_n(f"s{k}"), _n(f"p{k}a"), _n(f"p{k}b"),
                  _e(f"s{k}-a", f"s{k}", f"p{k}a", tags=["glue"]),
                  _e(f"a{k}-b", f"p{k}a", f"p{k}b", tags=["glue"]),
                  _e(f"b{k}-hub", f"p{k}b", "hub", tags=["glue"])]
    cat = _cat(facts)
    sel = ["n:hub", "n:hub2", "e:h-h2"] + [f"n:s{k}" for k in range(5)]
    c = close(Plan(selected=sel, scope="focus", focus=["n:hub"]), cat)
    added = [q for q, why in c.added_by.items() if why == "closure:path" and q.startswith("n:")]
    assert len(added) == 8


def test_induced_other_flow_and_plan_excluded():
    cat = _cat([_n("a", tags=["t1"]), _n("b"), _n("c"),
                _e("a-b", "a", "b", tags=["web"]), _e("b-c", "b", "c", tags=["data"],
                                                       optional=True),
                _e("a-c", "a", "c", tags=["data"])])
    c = close(Plan(selected=["n:a", "n:b", "n:c"], scope="focus", focus=["n:b"],
                   excluded=[("e:a-c", "not-relevant")], active=["web"]), cat)
    # a-b touches the anchor b; b-c touches it too; a-c is the plan's own exclusion
    assert _s(c.edges) == {"a-b", "b-c"} and c.excluded == {"e:a-c": "not-relevant"}
    c = close(Plan(selected=["n:a", "n:b", "n:c"], scope="focus", active=["web"]), cat)
    assert _s(c.edges) == {"a-b"}
    assert c.excluded == {"e:b-c": "other-flow", "e:a-c": "other-flow"}


def test_denied_edges_only_for_security_end_to_end_or_selected():
    cat = _cat([_n("a"), _n("b"), _e("a-b", "a", "b", style="denied")])
    assert close(Plan(selected=["n:a", "n:b"], scope="focus"), cat).edges == []
    assert close(Plan(selected=["n:a", "n:b"], scope="security"), cat).edges == ["e:a-b"]
    assert close(Plan(selected=["e:a-b"], scope="focus"), cat).edges == ["e:a-b"]


def test_isolated_nodes():
    cat = _cat([_n("a"), _n("b"), _n("lonely"), _e("a-b", "a", "b")])
    c = close(Plan(selected=["n:a", "n:b", "n:lonely", "e:a-b"], scope="focus"), cat)
    assert "n:lonely" in c.nodes  # selected: kept
    c = close(Plan(selected=["e:a-b"], scope="end-to-end"), cat)
    assert "n:lonely" not in c.nodes
    allq = list(cat.facts)
    c = close(Plan(selected=allq, scope="end-to-end"), cat)
    assert c.excluded == {"n:lonely": "isolated"} and "n:lonely" not in c.nodes


def test_preset_plan_draws_the_preset():
    cat = shipped_catalogue()
    c = close(Plan(selected=["n:c2"], scope="focus", preset="gateway-to-runtimes",
                   planner="llm"), cat)
    assert c.mode == "preset" and set(c.facts) == set(cat.preset_facts("gateway-to-runtimes"))


def test_selection_bound_over_96_nodes():
    """N15: a synthetic selection above MAX_SELECTION_NODES is a bounds result."""
    facts = [_n(f"n{i}") for i in range(MAX_SELECTION_NODES + 4)]
    facts += [_e(f"e{i}", f"n{i}", f"n{i + 1}") for i in range(MAX_SELECTION_NODES + 3)]
    cat = _cat(facts)
    c = close(Plan(selected=list(cat.facts), scope="end-to-end"), cat)
    assert len(c.nodes) == MAX_SELECTION_NODES + 4 and c.bounds
    small = close(Plan(selected=list(cat.facts)[:10], scope="focus"), cat)
    assert not small.bounds


def test_closure_never_adds_a_non_catalogue_id():
    cat = shipped_catalogue()
    c = close(Plan(selected=["n:gateway", "n:ghost", "e:nope"], scope="focus",
                   focus=["n:gateway"]), cat)
    assert all(q in cat.facts for q in c.facts)
