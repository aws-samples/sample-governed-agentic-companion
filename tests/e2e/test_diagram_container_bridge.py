"""Container e2e (marker `container`): the real bridge result builder against the locally built
runtime images, published on 127.0.0.1 only. Skipped unless GAC_E2E_ORCHESTRATOR_URL and/or
GAC_E2E_MCP_URL are http://127.0.0.1:<port> URLs (acceptance 21). Optional GAC_E2E_OUT_DIR keeps
the written files (used for the review samples)."""

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "frontdoor"))

pytestmark = pytest.mark.container
LOCAL = re.compile(r"^http://127\.0\.0\.1:[0-9]{2,5}$")
REQUESTS = {"private-request-path": "draw our private network path",
            "gateway-to-runtimes": "diagram how the gateway reaches the runtimes",
            "kb-ingestion": "show our KB ingestion"}


def _url(var):
    v = os.getenv(var, "")
    if not LOCAL.match(v):
        pytest.skip(f"{var} not set to a 127.0.0.1 URL")
    return v


def _workspace(tmp_path, front, topic):
    keep = os.getenv("GAC_E2E_OUT_DIR")
    root = Path(keep) / front if keep else tmp_path / front
    root.mkdir(parents=True, exist_ok=True)
    return root


def _check(root, topic, envelope, text):
    d = root / "outputs" / "diagrams" / topic
    arts = envelope["diagram"]["artifacts"]
    assert {a["file"].rsplit(".", 1)[-1] for a in arts} >= {"drawio", "svg", "png", "json"}
    for a in arts:
        data = (d / a["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == a["sha256"] and len(data) == a["bytes"]
    (root / f"{topic}.response.txt").write_text(text, encoding="utf-8")
    (root / f"{topic}.payload.json").write_text(json.dumps(envelope, indent=1), encoding="utf-8")


@pytest.mark.parametrize("topic", sorted(REQUESTS))
def test_orchestrator_container(topic, tmp_path, monkeypatch):
    base = _url("GAC_E2E_ORCHESTRATOR_URL")
    import urllib.request
    import mcp_bridge as bridge
    root = _workspace(tmp_path, "bridge-runtime", topic)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(root))
    payload = {"prompt": REQUESTS[topic], "environment": "dev", "action": "diagram",
               "include_artifacts": True}
    req = urllib.request.Request(base + "/invocations", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310 - 127.0.0.1 only
        envelope = json.loads(resp.read())
    text = bridge.build_draw_result(envelope, overwrite=True).text
    assert "Saved locally:" in text
    _check(root, topic, envelope, text)


@pytest.mark.parametrize("topic", sorted(REQUESTS))
def test_mcp_container_via_relay(topic, tmp_path, monkeypatch):
    base = _url("GAC_E2E_MCP_URL")
    from contextlib import asynccontextmanager
    import mcp_bridge as bridge
    root = _workspace(tmp_path, "bridge-mcp", topic)
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(root))
    captured = {}

    @asynccontextmanager
    async def factory():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async with streamablehttp_client(base + "/mcp") as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()

                class Spy:
                    async def call_tool(self, name, args):
                        res = await s.call_tool(name, args)
                        captured["text"] = res.content[0].text
                        return res
                yield Spy()

    monkeypatch.setenv("GAC_GATEWAY_TOOL_PREFIX", "x")
    import diagram_relay
    monkeypatch.setattr(diagram_relay, "tool_prefix", lambda: "")
    text = asyncio.run(bridge.draw_via_gateway(REQUESTS[topic], overwrite=True,
                                               session_factory=factory)).text
    envelope = json.loads(captured["text"])
    assert "Saved locally:" in text
    print(f"{topic}: wire_bytes={envelope['diagram']['limits']['wire_bytes']} "
          f"text_bytes={len(captured['text'].encode())}")
    _check(root, topic, envelope, text)
