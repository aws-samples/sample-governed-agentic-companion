"""AgentCore MCP-Runtime entrypoint — Governed Companion (the /mcp face).

The SECOND protocol face of the same companion engine. Where the /invocations app
(../orchestrator/runtime_entrypoint.py) answers plain HTTP invocations, this face serves the
AgentCore MCP contract — a STATELESS streamable-HTTP MCP server on port 8000 at /mcp — so an
AgentCore Gateway (protocol_type=MCP with an mcp_server target pointing at this /mcp URL) can
front the governed engine directly, removing the need for the local stdio bridge.

Why port 8000 at /mcp: the AgentCore MCP protocol contract requires the server on
0.0.0.0:8000 at /mcp. Why stateless: a design choice, not a platform requirement. AgentCore
supports both stateless and stateful streamable-HTTP MCP servers; stateless_http=True is the
AWS-recommended default, and the platform generates the Mcp-Session-Id header (the server must
accept it). The companion's tools are single request/response calls with no elicitation,
sampling, or progress notifications, so stateful mode is not needed. We serve the STATELESS
app under an ASGI server.

Governance is preserved BY CONSTRUCTION: every tool wraps the SAME gated `Orchestrator.handle`,
so each tool result has already passed the always-on Governance Gate. The tools mirror the
local bridge (`ask_companion`, `companion_kb`, `draw_diagram`) so both front doors expose one
surface.

The `mcp` SDK import is guarded so this file stays importable/unit-testable without the SDK
(the SDK is a deploy-time dependency; see requirements.txt).
"""

import logging
import os
import sys
from pathlib import Path

# ── Locate the companion engine (single source of truth) ────────────────────
_ENGINE_ROOT = os.getenv("GAC_ENGINE_ROOT")
_engine_path = Path(_ENGINE_ROOT) if _ENGINE_ROOT else Path(__file__).resolve().parents[3]
if str(_engine_path) not in sys.path:
    sys.path.insert(0, str(_engine_path))

logger = logging.getLogger("gac.agentcore.mcp_runtime")
logging.basicConfig(level=logging.INFO)

MCP_PORT = int(os.getenv("GAC_MCP_PORT", "8000"))


# ── Governed tool implementations (SDK-independent; unit-testable) ───────────
# Each tool routes through the ONE gated entry point. No tool returns un-gated text.

def ask_companion(prompt: str, environment: str = "dev") -> str:
    """Route a question to the governed orchestrator; returns the GATED answer."""
    from agents.orchestrator import Orchestrator
    return Orchestrator().handle(prompt, environment=environment)


def companion_kb(query: str, environment: str = "dev") -> str:
    """Read-only knowledge query, routed through the governed orchestrator (gated)."""
    from agents.orchestrator import Orchestrator
    return Orchestrator().handle(query, environment=environment)


def draw_diagram(prompt: str, environment: str = "dev", title: str = "", name: str = "",
                 include_artifacts: bool = False, planner: str = "") -> str:
    """Draw AWS architecture diagrams of this companion's implementation from its knowledge
    base. Accepts natural-language questions as well as draw requests, for example 'draw the
    architecture end to end', 'what talks to Bedrock', 'how does a request reach the knowledge
    base', 'diagram our security boundaries'. Returns the governed text and, when the knowledge
    cannot support the request, an honest decline with prompts that work.

    Returns a JSON string: {"response": <gated text>} plus "diagram" (the .drawio, .svg, .png
    and report artefacts with sha256 and sizes) when include_artifacts is true, or
    "diagram_manifest" (names, sizes and short hashes only, at most 16 KiB in total) by default.
    A multi-view answer adds "diagram_views" (views 2 to n) next to "diagram", or "views" inside
    "diagram_manifest". planner: "" or "deterministic" (it can only downgrade the planner).
    The local bridge requests the artefacts and writes them on the developer's machine."""
    import json
    from agents.orchestrator import Orchestrator
    include = include_artifacts is True
    orch = Orchestrator()
    text, bundles = orch.handle_with_views(
        prompt, environment, force="diagram", deliver=include,
        title=title if isinstance(title, str) else "", name=name if isinstance(name, str) else "",
        planner="deterministic" if planner == "deterministic" else "")
    out = {"response": text}
    if include and bundles:
        out["diagram"] = bundles[0]
        if len(bundles) > 1:
            out["diagram_views"] = bundles[1:]
    elif not include and orch.last_manifest is not None:
        out["diagram_manifest"] = orch.last_manifest
    return json.dumps(out, separators=(",", ":"))


TOOLS = {
    "ask_companion": ask_companion,
    "companion_kb": companion_kb,
    "draw_diagram": draw_diagram,
}

_NO_STRUCTURED = ("draw_diagram not registered: this MCP SDK cannot disable structured output; "
                  "the payload would be sent twice")


def register_tools(mcp) -> None:
    """Register the governed tools. draw_diagram is registered with structured_output=False so
    its (large) result is carried once; an SDK that rejects the flag leaves it unregistered
    (fail closed) and the other tools register as before."""
    mcp.tool()(ask_companion)
    mcp.tool()(companion_kb)
    try:
        mcp.tool(structured_output=False)(draw_diagram)
    except TypeError:
        logger.error(_NO_STRUCTURED)


def build_app():
    """Build the STATELESS streamable-HTTP MCP ASGI app exposing the governed tools.

    Guarded: raises a clear error if the `mcp` SDK is absent (deploy-time dep). The tool
    functions above are importable and testable without the SDK.
    """
    # The server class was renamed FastMCP -> MCPServer in mcp SDK 2.x. Support BOTH so the
    # runtime works regardless of which major the container resolves: try the 2.x path first,
    # fall back to the 1.x path. Both expose the same .tool()/.streamable_http_app() surface.
    try:
        from mcp.server.mcpserver import MCPServer as _McpServer  # mcp >= 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as _McpServer  # mcp 1.x
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "The `mcp` SDK (mcp==1.30.0) is required to serve the MCP runtime; it is a "
                "deploy-time dependency. Install it in the container (see requirements.txt)."
            ) from e

    # stateless_http=True so the AgentCore Gateway mcp_server target can forward
    # self-contained requests (each request carries its own context).
    # Bind 0.0.0.0 is REQUIRED and safe here: the process runs inside the AgentCore
    # Runtime container, which is not directly internet-exposed — the platform health-checks
    # and reaches the runtime on this port. Binding 127.0.0.1 would make the runtime
    # unreachable and fail the health check. Network exposure is controlled at the platform
    # (gateway + security groups), not by this bind. (Bandit B104 false positive.)
    mcp = _McpServer("governed-companion", stateless_http=True, host="0.0.0.0", port=MCP_PORT)  # nosec B104
    register_tools(mcp)
    return mcp.streamable_http_app()


# ASGI servers (uvicorn) import `app` from this module.
try:  # pragma: no cover - requires the mcp SDK
    app = build_app()
except Exception:  # keep the module importable locally without the SDK
    app = None


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    if app is None:
        app = build_app()  # surface the clear error if the SDK is missing
    logger.info("Serving stateless MCP runtime on 0.0.0.0:%s/mcp", MCP_PORT)
    # See the FastMCP bind above: 0.0.0.0 is required inside the AgentCore container so the
    # platform can reach the runtime on this port; exposure is controlled at the platform,
    # not this bind. (Bandit B104 false positive.)
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)  # nosec B104
