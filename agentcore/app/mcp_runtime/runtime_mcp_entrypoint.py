"""AgentCore MCP-Runtime entrypoint — Governed Companion (the /mcp face).

The SECOND protocol face of the same companion engine. Where the /invocations app
(../orchestrator/runtime_entrypoint.py) answers plain HTTP invocations, this face serves the
AgentCore MCP contract — a STATELESS streamable-HTTP MCP server on port 8000 at /mcp — so an
AgentCore Gateway (protocol-type None + an http-runtime target) can front the governed engine
directly, removing the need for the local stdio bridge.

Why stateless + port 8000: the AgentCore MCP-runtime service contract health-checks port 8000
and manages the session id; a stateful server (server.run()'s streamable-http default) or a
different port fails the health check. We therefore serve the STATELESS app under an ASGI
server.

Governance is preserved BY CONSTRUCTION: every tool wraps the SAME gated `Orchestrator.handle`,
so each tool result has already passed the always-on Governance Gate. The tools mirror the
local bridge (`ask_companion`, `companion_kb`) so both front doors expose one surface.

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


TOOLS = {
    "ask_companion": ask_companion,
    "companion_kb": companion_kb,
}


def build_app():
    """Build the STATELESS streamable-HTTP MCP ASGI app exposing the governed tools.

    Guarded: raises a clear error if the `mcp` SDK is absent (deploy-time dep). The tool
    functions above are importable and testable without the SDK.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "The `mcp` SDK (mcp>=1.23.0) is required to serve the MCP runtime; it is a "
            "deploy-time dependency. Install it in the container (see requirements.txt)."
        ) from e

    # stateless_http=True so the AgentCore Gateway http-runtime target can forward
    # self-contained requests (each request carries its own context).
    # Bind 0.0.0.0 is REQUIRED and safe here: the process runs inside the AgentCore
    # Runtime container, which is not directly internet-exposed — the platform health-checks
    # and reaches the runtime on this port. Binding 127.0.0.1 would make the runtime
    # unreachable and fail the health check. Network exposure is controlled at the platform
    # (gateway + security groups), not by this bind. (Bandit B104 false positive.)
    mcp = FastMCP("governed-companion", stateless_http=True, host="0.0.0.0", port=MCP_PORT)  # nosec B104
    mcp.tool()(ask_companion)
    mcp.tool()(companion_kb)
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
