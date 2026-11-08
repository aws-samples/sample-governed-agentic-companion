"""Routing pin for the six brief prompts (design AC 31). Auto-routing (`ask`) is unchanged:
two of the prompts route to the diagram specialist, four keep their prose specialist. The same
six prompts sent to draw_diagram (forced to the diagram specialist) give the D8 outcomes, and
both draw_diagram docstrings (MCP runtime and bridge) invite natural-language questions."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "mcp_runtime"))

pytest.importorskip("defusedxml")

from diagram_fixtures import fixture_assets_dir  # noqa: E402
from agents.diagram import assets as A  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402

#: prompt -> (auto-routes to diagram, D8 outcome: view ids or "decline")
BRIEF = {
    "draw gac pvt architecture end to end": (
        True, ["private-request-path", "gateway-to-runtimes", "kb-ingestion", "bedrock-calls"]),
    "diagram our security boundaries": (
        True, ["private-request-path", "gateway-to-runtimes", "kb-ingestion"]),
    "what talks to Bedrock": (False, ["generic"]),
    "how does memory get written": (False, "decline"),
    "show how a developer request reaches the knowledge base": (
        False, ["private-request-path", "kb-ingestion"]),
    "show the gateway, runtimes and what they depend on": (
        False, ["gateway-to-runtimes", "kb-ingestion", "bedrock-calls"]),
}
E2E_VIEWS = ["private-request-path", "gateway-to-runtimes", "kb-ingestion", "bedrock-calls"]
#: generic requests that name nothing (review NL-1): the whole picture, not a decline
GENERIC = ["draw our architecture", "diagram gac",
           "give me an architecture diagram of the companion"]


@pytest.mark.parametrize("prompt", GENERIC)
def test_generic_request_draws_end_to_end(orch, prompt):
    text, bundles = orch.handle_with_views(prompt, force="diagram")
    assert bundles and _view_ids(bundles) == E2E_VIEWS and not orch.last_declined
    assert "Not in the knowledge base" not in text


@pytest.fixture(scope="module")
def orch(tmp_path_factory):
    from agents.diagram.assets import Assets
    o = Orchestrator()
    o._specialists["diagram"][0]._assets = Assets(
        str(fixture_assets_dir(tmp_path_factory.mktemp("nl-icons"))))
    return o


def _view_ids(bundles):
    a = next(a for a in bundles[0]["artifacts"] if a["file"].endswith(".report.json"))
    return [v["id"] for v in json.loads(a["content"])["plan"]["views"]]


@pytest.mark.parametrize("prompt", sorted(BRIEF))
def test_auto_routing_pinned_as_today(prompt):
    assert (Orchestrator().route(prompt) == "diagram") is BRIEF[prompt][0]


@pytest.mark.parametrize("prompt", sorted(BRIEF))
def test_forced_draw_gives_the_d8_outcome(orch, prompt):
    text, bundles = orch.handle_with_views(prompt, force="diagram")
    want = BRIEF[prompt][1]
    if want == "decline":
        assert not bundles and orch.last_declined
        assert text.startswith("[diagram] I can't draw this from the knowledge base")
    else:
        assert bundles and _view_ids(bundles) == want and len(bundles) == len(want)


def test_mcp_draw_diagram_forced(tmp_path, monkeypatch):
    mrt = pytest.importorskip("runtime_mcp_entrypoint")
    monkeypatch.delenv("GAC_DIAGRAM_ASSETS_DIR", raising=False)
    monkeypatch.setattr(A, "LOCAL_ASSETS_DIR", fixture_assets_dir(tmp_path))
    out = json.loads(mrt.draw_diagram("what talks to Bedrock", include_artifacts=True))
    assert out["diagram"]["name"] == "what-talks-to-amazon-bedrock"
    out = json.loads(mrt.draw_diagram("how does memory get written", include_artifacts=True))
    assert "diagram" not in out and "Missing: no diagram facts mention" in out["response"]


def _docstring(path, fn="draw_diagram"):
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{fn} not found in {path}")


@pytest.mark.parametrize("path", [
    ROOT / "agentcore" / "app" / "mcp_runtime" / "runtime_mcp_entrypoint.py",
    ROOT / "frontdoor" / "mcp_bridge.py"])
def test_draw_diagram_docstrings_invite_questions(path):
    """AC 31: the tool description an MCP client sees says questions work (design D18)."""
    doc = " ".join(_docstring(path).split())
    assert "what talks to" in doc and "questions" in doc
    assert doc.startswith("Draw AWS architecture diagrams of this companion's implementation "
                          "from its knowledge base. Accepts natural-language questions")
    assert "an honest decline with prompts that work." in doc
