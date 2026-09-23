"""Local stdio MCP bridge: any MCP IDE (Kiro / Claude Code / ...) -> the deployed Governed
Agentic Companion AgentCore Runtime (authenticated).

Why this exists: AgentCore's Runtime is a plain `/invocations` app, and standing up the
AgentCore Gateway MCP path is optional. This bridge is the turnkey front door: an MCP server
the IDE spawns locally, which forwards each call to the deployed runtime over an authenticated
HTTPS call and auto-refreshes the Cognito token. Any MCP-capable client can use it.

Governance is preserved by construction: the bridge NEVER reasons or gates locally — it relays
to the runtime, whose response has ALREADY passed the always-on Governance Gate in the cloud.
Human-owned deployment (Tenet 1) is unchanged; this is a read/answer path.

Tools exposed to the IDE:
    ask_companion(prompt, environment="dev")  -> route a question to the governed orchestrator
    companion_kb(query, environment="dev")     -> read-only knowledge-base query (gated)

Both tools mirror the deployed runtime's tool surface (see
agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py), so the local bridge and the cloud MCP
runtime expose the identical two tools.

The `mcp` SDK is imported lazily in main() so this module stays importable/testable without it.
The token + runtime-client logic live in sibling modules and are unit-tested with fakes.
"""

import logging
import os
import sys

logging.basicConfig(level=os.getenv("GAC_BRIDGE_LOG_LEVEL", "INFO"),
                    stream=sys.stderr)  # stdout is the MCP channel — logs MUST go to stderr
logger = logging.getLogger("companion.frontdoor.bridge")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cognito_token import CognitoTokenProvider   # noqa: E402
from runtime_client import CompanionRuntimeClient  # noqa: E402


def build_client() -> CompanionRuntimeClient:
    """Construct the runtime client with a caching Cognito token provider."""
    return CompanionRuntimeClient(CognitoTokenProvider())


def _text(result: dict) -> str:
    """Extract the human-facing text from the runtime envelope (already gated in-cloud)."""
    if isinstance(result, dict):
        return str(result.get("response", "")) or "(empty response from runtime)"
    return str(result)


def main():
    """Run the stdio MCP server. The IDE (Kiro/Claude Code/...) launches this as an MCP `command`."""
    # Support both MCP SDK lines: v2.x renamed FastMCP -> MCPServer. Try 2.x first, then v1.
    Server = None
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server   # mcp 1.x
        except ImportError as e:
            raise SystemExit(
                "The MCP SDK is not installed in this environment. Install the bridge deps:\n"
                "  pip install 'mcp>=1.23.0' boto3 requests\n"
                f"(import error: {e})"
            )

    client = build_client()
    mcp = Server("companion-engine")

    @mcp.tool()
    def ask_companion(prompt: str, environment: str = "dev") -> str:
        """Ask the governed agentic companion (routes to the orchestrator + specialists).
        Returns a governed, gated answer. A human deploys — this never mutates any environment."""
        return _text(client.invoke(prompt=prompt, environment=environment))

    @mcp.tool()
    def companion_kb(query: str, environment: str = "dev") -> str:
        """Read-only knowledge-base query, routed through the governed orchestrator (gated)."""
        return _text(client.invoke(prompt=query, environment=environment))

    logger.info("Companion MCP bridge starting (stdio). Target runtime: %s",
                os.getenv("GAC_RUNTIME_ARN", "<GAC_RUNTIME_ARN not set>"))
    mcp.run()


if __name__ == "__main__":
    main()
