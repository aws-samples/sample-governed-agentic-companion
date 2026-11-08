"""Test helpers: synthetic fixture icons (coloured squares, no AWS artwork) and catalog."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.diagram_fixture import (  # noqa: E402,F401
    GROUP, RESOURCE, SERVICE, build_fixture_catalog, fixture_zip_bytes, square_svg)


def fixture_assets_dir(tmp_path: Path, **kw) -> Path:
    out = tmp_path / "assets"
    build_fixture_catalog(out, **kw)
    return out


FIXTURE_KB_DIR = ROOT / "tests" / "golden" / "fixtures"


def secret_label() -> str:
    """A credential-shaped label, assembled at run time so the repository holds none."""
    return "pass" + "word=" + "hunter" + "2"


def load_fixture_kb(name: str, agent=None) -> dict:
    """Load tests/golden/fixtures/<name>/*.yaml as `tier_b/diagram/<stem>` knowledge keys.

    Mode (from `_fixture.yaml`, default `replace`): `replace` makes the fixture the whole
    diagram KB of `agent` (a DiagramSpecialist) and binds its peers to {}; `overlay` adds the keys
    to the agent's shipped knowledge and keeps its peers. Returns the fixture keys."""
    import yaml

    base = FIXTURE_KB_DIR / name
    mode = "replace"
    keys = {}
    for p in sorted(base.glob("*.yaml")):
        text = p.read_text(encoding="utf-8").replace("__SECRET_LABEL__", secret_label())
        data = yaml.safe_load(text)
        if p.stem == "_fixture":
            mode = (data or {}).get("mode", "replace")
            continue
        keys[f"tier_b/diagram/{p.stem}"] = data
    if agent is not None:
        if mode == "overlay":
            agent._knowledge.update(keys)
        else:
            agent._knowledge = dict(keys)
            agent.bind_peers(lambda: {})
    return keys


class MockPlanner:
    """A stand-in for the bedrock-runtime client, passed as `DiagramSpecialist(planner_factory=)`
    (the factory is called with (region, timeout_seconds) and returns this object). It records
    the exact `converse` kwargs. `plan` is the select_facts tool input; `response` replaces the
    whole Converse response; `raises` is raised from converse; `advance` moves `clock` forward
    during the call (to show the render deadline starts after planning)."""

    def __init__(self, plan=None, *, response=None, raises=None, clock=None, advance=0.0,
                 usage=(120, 30)):
        self.plan = plan
        self.response = response
        self.raises = raises
        self.clock = clock
        self.advance = advance
        self.usage = usage
        self.calls = []
        self.factory_calls = []

    def __call__(self, region, timeout_seconds):
        self.factory_calls.append((region, timeout_seconds))
        return self

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.clock is not None and self.advance:
            self.clock.t += self.advance
        if self.raises is not None:
            raise self.raises
        if self.response is not None:
            return self.response
        return {"output": {"message": {"role": "assistant", "content": [
                    {"toolUse": {"toolUseId": "t-1", "name": "select_facts",
                                 "input": self.plan}}]}},
                "stopReason": "tool_use",
                "usage": {"inputTokens": self.usage[0], "outputTokens": self.usage[1]}}

    def sent_text(self) -> str:
        """Everything the model would have seen, as one string."""
        import json
        return "\n".join(json.dumps(c) for c in self.calls)


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def bedrock_env(monkeypatch, model="test.model-v1", guardrail="gr-test"):
    """Opt the planner in for one test (env only; nothing reaches AWS)."""
    monkeypatch.setenv("GAC_DIAGRAM_PLANNER", "bedrock")
    monkeypatch.setenv("GAC_DIAGRAM_PLANNER_MODEL_ID", model)
    monkeypatch.setenv("GAC_GUARDRAIL_ID", guardrail)
    monkeypatch.setenv("GAC_GUARDRAIL_VERSION", "1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def shipped_catalogue(fixture: str = ""):
    """The catalogue of the shipped example KB (diagram specialist + its registry peers), or of
    a fixture KB loaded onto a fresh diagram specialist."""
    from agents.diagram.catalogue import build_catalogue
    from agents.specialists import build_specialists

    reg = build_specialists()
    d = reg["diagram"][0]
    peers = {n: a for n, (a, _) in reg.items() if n != "diagram"}
    d.bind_peers(lambda: peers)
    if fixture:
        load_fixture_kb(fixture, d)
    return build_catalogue(d, d._peers())
