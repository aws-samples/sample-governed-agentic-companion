"""The golden natural-language prompt set, deterministic planner (design D8; AC 1, 2, 6, 7, 8,
18, 29). Every render row renders the exact view list, each view passes the quality check with
100% traceability, every "include" fact is drawn and every "exclude" fact is drawn nowhere.
Every decline row gives the pinned lines and 1 to 5 suggestions, and each suggestion is
replayed under the strict M8 rule (a preset suggestion replays to that preset; no unmatched
term; every view passes).

Mock mode (plan item 13; AC 3, 6, 8): the opt-in Bedrock planner with `MockPlanner` standing in
for the bedrock-runtime client. Rows 1 to 16 select the row's include set with the row's scope
(exploration zone, "Confidence: 100%", the llm cache-miss line); a preset-trigger row never
calls the model (design D2.1) and stays a preset. Row 2 replays the deterministic plan and
must give the same elements and drawio bytes. Rows 17 to 23 use adversarial or empty plans.
Every captured Converse call is checked for prose, refs, sources and sensitive spans."""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

pytest.importorskip("defusedxml")

from diagram_fixtures import (MockPlanner, bedrock_env, fixture_assets_dir,  # noqa: E402
                              load_fixture_kb)
from agents.diagram import decline as D  # noqa: E402
from agents.diagram import planner_bedrock as PB  # noqa: E402
from agents.diagram.assets import Assets  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402

ROWS = yaml.safe_load((ROOT / "tests" / "golden" / "diagram_prompts.yaml")
                      .read_text(encoding="utf-8"))["rows"]
BY_ID = {r["id"]: r for r in ROWS}
_ORCH = {}
_RUNS = {}


def access_key() -> str:
    """An access-key shape, assembled at run time (detect-secrets stays at 0)."""
    return ("AK" + "IA") + ("Q" * 12) + "7RST"


def _prompt(row) -> str:
    return row["prompt"].replace("__ACCESS_KEY__", access_key())


@pytest.fixture(scope="module")
def assets_dir(tmp_path_factory):
    return fixture_assets_dir(tmp_path_factory.mktemp("golden-icons"))


def _orch(fixture: str, assets_dir) -> Orchestrator:
    if fixture not in _ORCH:
        o = Orchestrator()
        d = o._specialists["diagram"][0]
        d._assets = Assets(str(assets_dir))
        if fixture:
            load_fixture_kb(fixture, d)
        _ORCH[fixture] = o
    return _ORCH[fixture]


def run(prompt: str, fixture: str, assets_dir):
    key = (fixture, prompt)
    if key not in _RUNS:
        o = _orch(fixture, assets_dir)
        text, bundles = o.handle_with_views(prompt, force="diagram", planner="deterministic")
        _RUNS[key] = (text, bundles, o.last_declined)
    return _RUNS[key]


def _report(bundle) -> dict:
    a = next(a for a in bundle["artifacts"] if a["file"].endswith(".report.json"))
    return json.loads(a["content"])


def _drawn(bundles):
    out = set()
    for b in bundles:
        for el in _report(b)["elements"]:
            for ref in el["kb_refs"]:
                out.add(f"{el['kind'][0]}:{ref.split('#', 1)[1]}")
    return out


def _all_nodes_edges(fixture, assets_dir):
    d = _orch(fixture, assets_dir)._specialists["diagram"][0]
    from agents.diagram.catalogue import build_catalogue
    cat = build_catalogue(d, d._peers())
    return set(cat.nodes()) | set(cat.edges())


def _suggestions(text):
    lines = text.split("\n")
    i = lines.index(D.SUGGEST_HEAD)
    out = []
    for line in lines[i + 1:]:
        if not line.startswith('- "'):
            break
        out.append(line[3:-1])
    return out


def _assert_views_pass(bundles):
    for b in bundles:
        q = b["quality"]
        assert q["pass"] and q["score"] >= 85 and not b["draft"], (b["name"], q["score"])
        assert not [f for f in q["findings"] if f.get("severity") in ("fail", "fix")]
        t = b["traceability"]
        assert t["rendered"] == t["cited"] and t["rendered"] > 0


RENDER = [r["id"] for r in ROWS if r["expect"] == "render"]
DECLINE = [r["id"] for r in ROWS if r["expect"] == "decline"]


def test_the_set_has_26_rows():
    # 23 D8 rows plus the generic phrasings and plain verbs of review NL-1 and NL-2
    assert sorted(BY_ID) == list(range(1, 27))
    assert all(r["expect"] in ("render", "decline") for r in ROWS)


@pytest.mark.parametrize("rid", RENDER)
def test_render_row(rid, assets_dir):
    row = BY_ID[rid]
    text, bundles, declined = run(_prompt(row), row["fixture"], assets_dir)
    assert bundles and not declined, text[:400]
    rep = _report(bundles[0])
    views = [v["id"] for v in rep["plan"]["views"]]
    assert views == row["views"]
    assert len(bundles) == len(row["views"]) == len(row["quality"])
    if row["scope"]:
        assert rep["plan"]["scope"] == row["scope"]
    _assert_views_pass(bundles)
    drawn = _drawn(bundles)
    include = set(row["include"])
    if "*" in include:
        include = _all_nodes_edges(row["fixture"], assets_dir) - set(row["exclude"])
    assert include <= drawn, sorted(include - drawn)
    status = rep["plan"]["status"]
    for q in row["exclude"]:
        assert q not in drawn
        if q in status:
            assert status[q]["status"] == "excluded"
    for s in row["text_contains"]:
        assert s in text, s
    for s in row["text_absent"]:
        assert s not in text, s
    # AC 29: every rendered response says where the drawing comes from
    assert "Drawn from the knowledge base, not from the live deployment." in text
    assert "  Diagram source: knowledge base, not the live deployment" in text
    assert "Gate status: ✓ PASS" in text


@pytest.mark.parametrize("rid", DECLINE)
def test_decline_row(rid, assets_dir):
    row = BY_ID[rid]
    text, bundles, declined = run(_prompt(row), row["fixture"], assets_dir)
    assert not bundles and declined
    body = text.split("── Governance Outcome ──")[0]
    if rid != 20:
        assert body.startswith(D.HEADER)
        assert D.ADD_FACTS in body
    for s in row["text_contains"]:
        assert s in body, s
    for s in row["text_absent"]:
        assert s not in text, s
    sugg = _suggestions(body)
    assert 1 <= len(sugg) <= 5
    if "suggestions" in row:
        assert sugg == row["suggestions"]
    assert "  Diagram planner: deterministic" in text and "Gate status: ✓ PASS" in text


def _replay_ok(prompt, fixture, assets_dir):
    text, bundles, declined = run(prompt, fixture, assets_dir)
    assert bundles and not declined, prompt
    _assert_views_pass(bundles)
    plan = _report(bundles[0])["plan"]
    assert plan["unmatched"] == [] and "Not in the knowledge base" not in text, prompt
    return plan


@pytest.mark.parametrize("rid", DECLINE)
def test_every_suggestion_replays_under_m8(rid, assets_dir):
    row = BY_ID[rid]
    text, _b, _d = run(_prompt(row), row["fixture"], assets_dir)
    cat_presets = {p.suggestion: p.id for p in
                   _orch(row["fixture"], assets_dir)._specialists["diagram"][0].topics()[0]}
    for s in _suggestions(text):
        plan = _replay_ok(s, row["fixture"], assets_dir)
        if s in cat_presets:
            assert plan["planner"] == "preset" and plan["preset"] == cat_presets[s], s


def test_row_2_has_no_unmatched_terms(assets_dir):
    text, bundles, _ = run(BY_ID[2]["prompt"], "", assets_dir)
    assert _report(bundles[0])["plan"]["unmatched"] == []


def test_row_15_records_the_unmatched_terms(assets_dir):
    _t, bundles, _ = run(BY_ID[15]["prompt"], "", assets_dir)
    assert _report(bundles[0])["plan"]["unmatched"] == ["kafka", "cluster"]


def test_row_17_injection_never_rendered(assets_dir):
    text, bundles, _ = run(BY_ID[17]["prompt"], "injection", assets_dir)
    status = _report(bundles[0])["plan"]["status"]
    assert status["n:attacker-note"] == {"status": "excluded", "reason": "isolated"}
    for b in bundles:
        for a in b["artifacts"]:
            if a["encoding"] == "utf-8":
                assert "C2" not in a["content"] and "ignore previous" not in a["content"]


def test_row_22_secret_never_echoed(assets_dir):
    row = BY_ID[22]
    text, bundles, _ = run(_prompt(row), "secret", assets_dir)
    key = access_key()
    assert key not in text and "Gate status: ✓ PASS" in text
    for b in bundles:
        assert key not in json.dumps(b) and "hunter" not in json.dumps(b)
    assert _report(bundles[0])["plan"]["preset"] == "gateway-to-runtimes"


def test_presets_keep_their_names(assets_dir):
    """AC 18: the four presets pass and "draw our private network path" keeps its file names."""
    for prompt, name in (("draw our private network path", "private-request-path"),
                         ("diagram how the gateway reaches the runtimes", "gateway-to-runtimes"),
                         ("show our KB ingestion", "kb-ingestion"),
                         ("draw the bedrock calls", "bedrock-calls")):
        _t, bundles, _ = run(prompt, "", assets_dir)
        assert [b["name"] for b in bundles] == [name]
        _assert_views_pass(bundles)
        assert {a["file"] for a in bundles[0]["artifacts"]} >= {
            f"{name}.drawio", f"{name}.svg", f"{name}.report.json"}


# == mock-planner mode (plan item 13) =========================================================

LLM_LINE = "  Diagram planner: llm (model-selected facts, every element traced), cache miss"


def _catalogue(fixture, assets_dir):
    from agents.diagram.catalogue import build_catalogue
    d = _orch(fixture, assets_dir)._specialists["diagram"][0]
    return d, build_catalogue(d, d._peers())


def run_mock(prompt, fixture, assets_dir, mock, monkeypatch):
    bedrock_env(monkeypatch)
    PB.reset()
    o = _orch(fixture, assets_dir)
    d = o._specialists["diagram"][0]
    old = d._planner_factory
    d._planner_factory = mock
    try:
        text, bundles = o.handle_with_views(prompt, force="diagram")
    finally:
        d._planner_factory = old
        PB.reset()
    return text, bundles, o.last_declined


def _assert_clean_calls(mock, cat):
    """The captured Converse kwargs: pinned prompt and bounds, data only, no prose or secrets."""
    for kw in mock.calls:
        assert kw["system"] == [{"text": PB.SYSTEM_PROMPT}]
        assert kw["inferenceConfig"]["temperature"] == 0
        assert kw["inferenceConfig"]["maxTokens"] <= 1024
        assert kw["guardrailConfig"]["guardrailIdentifier"] == "gr-test"
        assert kw["toolConfig"]["toolChoice"] == {"tool": {"name": "select_facts"}}
    sent = mock.sent_text()
    assert "10.0.0.1" not in sent and access_key() not in sent and "AKIA" not in sent
    assert "tier_a/" not in sent and "tier_b/" not in sent and "kb_key" not in sent
    for f in cat.facts.values():
        for src in f.sources:
            assert src not in sent
    assert "ignore previous" not in sent.lower() and "c2 server" not in sent.lower()


def _mock_plan(row, cat):
    mp = dict(row["mock_plan"])
    if mp.pop("from_include", False):
        include = set(row["include"])
        if "*" in include:
            include = (set(cat.nodes()) | set(cat.edges())) - set(row["exclude"])
        mp["selected"] = [q for q in cat.facts if q in include]
    mp.pop("replay_deterministic", None)
    return mp


def _drawio_sha(bundles):
    import hashlib
    out = []
    for b in bundles:
        a = next(a for a in b["artifacts"] if a["file"].endswith(".drawio"))
        out.append((b["name"], hashlib.sha256(a["content"].encode("utf-8")).hexdigest()))
    return out


@pytest.mark.parametrize("rid", list(range(1, 17)) + [24, 25, 26])
def test_mock_row(rid, assets_dir, monkeypatch):
    row = BY_ID[rid]
    _d, cat = _catalogue(row["fixture"], assets_dir)
    from agents.diagram.planner_det import plan_request
    det = plan_request(_prompt(row), cat)
    mock = MockPlanner(_mock_plan(row, cat))
    text, bundles, declined = run_mock(_prompt(row), row["fixture"], assets_dir, mock,
                                       monkeypatch)
    _assert_clean_calls(mock, cat)
    if getattr(det, "planner", "") == "preset":
        # D2.1: a preset trigger is tried first in both modes and never spends a model call.
        assert mock.calls == [] and "  Diagram planner: preset" in text
        assert bundles and [b["name"] for b in bundles] == row["views"]
        _assert_views_pass(bundles)
        return
    assert len(mock.calls) == 1
    if row["expect"] == "decline":
        assert not bundles and declined and text.startswith(D.HEADER)
        assert "Trust zone: verified" in text
        for s in row["text_contains"]:
            assert s in text, s
        return
    assert bundles and not declined, text[:400]
    _assert_views_pass(bundles)
    drawn = _drawn(bundles)
    include = set(row["include"])
    if "*" in include:
        include = _all_nodes_edges(row["fixture"], assets_dir) - set(row["exclude"])
    assert include <= drawn, sorted(include - drawn)
    for q in row["exclude"]:
        assert q not in drawn, q
    assert "Trust zone: exploration" in text and "Confidence: 100%" in text
    assert LLM_LINE in text and "Gate status: ✓ PASS" in text
    assert _report(bundles[0])["plan"]["planner"] == "llm"


def test_mock_row_2_replays_the_deterministic_plan(assets_dir, monkeypatch):
    """Closure determinism: the deterministic qids through the llm path give the same
    elements and the same drawio bytes per view."""
    row = BY_ID[2]
    _d, cat = _catalogue("", assets_dir)
    from agents.diagram.planner_det import plan_request
    det = plan_request(row["prompt"], cat)
    _t, det_bundles, _ = run(row["prompt"], "", assets_dir)
    mock = MockPlanner({"selected": list(det.selected), "focus": list(det.focus),
                        "scope": det.scope})
    text, bundles, _ = run_mock(row["prompt"], "", assets_dir, mock, monkeypatch)
    assert LLM_LINE in text and len(mock.calls) == 1
    assert [b["name"] for b in bundles] == [b["name"] for b in det_bundles]
    for a, b in zip(bundles, det_bundles):
        assert ({(e["kind"], tuple(e["kb_refs"])) for e in _report(a)["elements"]}
                == {(e["kind"], tuple(e["kb_refs"])) for e in _report(b)["elements"]})
    assert _drawio_sha(bundles) == _drawio_sha(det_bundles)


@pytest.mark.parametrize("rid", list(range(17, 24)))
def test_mock_adversarial_row(rid, assets_dir, monkeypatch):
    row = BY_ID[rid]
    _d, cat = _catalogue(row["fixture"], assets_dir)
    mock = MockPlanner(dict(row["mock_plan"]))
    text, bundles, declined = run_mock(_prompt(row), row["fixture"], assets_dir, mock,
                                       monkeypatch)
    _assert_clean_calls(mock, cat)
    for s in row["text_absent"]:
        assert s not in text, s
    for b in bundles:
        for a in b["artifacts"]:
            if a["encoding"] == "utf-8":
                c = a["content"]
                assert "C2" not in c and access_key() not in c and "10.0.0.1" not in c
    if rid == 17:
        # unknown ids dropped and counted; the valid preset id draws that preset
        assert len(mock.calls) == 1 and [b["name"] for b in bundles] == ["gateway-to-runtimes"]
        assert _report(bundles[0])["plan"]["unknown_ids"] == 2
        assert "2 unknown ids" in text and "Trust zone: exploration" in text
        assert LLM_LINE in text
        _assert_views_pass(bundles)
    elif rid == 22:
        # "gateway path" is a preset trigger: no model call; the key is never echoed
        assert mock.calls == [] and [b["name"] for b in bundles] == ["gateway-to-runtimes"]
        assert access_key() not in text
    else:
        # an empty model selection is an honest decline, verified zone
        assert len(mock.calls) == 1
        assert not bundles and declined and "Trust zone: verified" in text
        assert text.startswith(D.HEADER)
        # the over-broad fork fixture has no preset and no anchor-free suggestion that renders
        # within the view limit, so an empty model selection there offers none
        lo = 0 if rid == 20 else 1
        assert lo <= len(_suggestions(text.split("── Governance Outcome ──")[0])) <= 5
