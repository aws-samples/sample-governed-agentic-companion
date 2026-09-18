"""Local stdio MCP bridge — REFERENCE STUB.

The IDE (Kiro/Claude Code) spawns this as a stdio MCP server. It exposes governed tools and
relays each call to the DEPLOYED AgentCore runtime, using CognitoTokenProvider for a fresh
bearer token. Governance is unchanged: the bridge only relays; the runtime's response is already
gated in the cloud.

This is a STUB: wire `_invoke_runtime()` to your deployed runtime's invocation API (e.g.
bedrock-agentcore InvokeAgentRuntime), and expose the tools via your MCP SDK of choice
(`mcp>=1.23.0`). The token flow + tool surface are the reusable parts.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cognito_token import CognitoTokenProvider


def _invoke_runtime(token: str, payload: dict) -> dict:
    """Send an authenticated request to the deployed runtime. WIRE THIS to your runtime API."""
    raise NotImplementedError(
        "Wire mcp_bridge._invoke_runtime() to your deployed AgentCore runtime invocation API "
        "(e.g. bedrock-agentcore InvokeAgentRuntime with the bearer token), then expose the "
        "tools below via your MCP SDK. See frontdoor/README.md."
    )


def ask_companion(prompt: str, environment: str = "dev") -> str:
    token = CognitoTokenProvider().get_token()
    return _invoke_runtime(token, {"prompt": prompt, "environment": environment}).get("response", "")


def companion_kb(query: str) -> str:
    token = CognitoTokenProvider().get_token()
    return _invoke_runtime(token, {"action": "kb", "query": query}).get("response", "")


def main() -> int:
    # Replace with your MCP SDK server registration exposing ask_companion / companion_kb.
    sys.stderr.write(
        "mcp_bridge is a reference stub. Register ask_companion/companion_kb with your MCP SDK "
        "(mcp>=1.23.0) and wire _invoke_runtime() to the deployed runtime. See frontdoor/README.md.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
