"""Local stdio MCP server exposing the Governance Gate as a tool for ANY MCP harness.

Track 2 of the GAC v2 uplift — GAC as the unbypassable governance substrate under an agentic
SDLC. Unlike mcp_bridge.py (which relays to a DEPLOYED AgentCore runtime over an authenticated
HTTPS call), this server runs the gate LOCALLY and DETERMINISTICALLY: no AWS, no token, no
runtime. That is deliberate — the gate is a pure, in-process safety floor, so any harness can
run a candidate artifact (an answer, a diff, a plan, generated code) through it before accepting
it, with zero cloud dependency.

Tool exposed:
    govern_artifact(text, kind="artifact", front_door="mcp-gate") -> structured verdict
        Runs the always-on GovernanceGate over `text`. Returns a JSON-ish dict:
          { "blocked": bool, "verdict": "PASS"|"BLOCK", "findings": [...], "message": str }
        A BLOCK means an absolute boundary was crossed (Tenet 1 deploy-claim / Tenet 3
        system-of-record write / Tenet 6 secret leak), or — for an LLM artifact marked
        used_llm=true — the grounding bar was not met (Tenet 4). The harness should refuse to
        accept the artifact on a BLOCK. Enforcement cannot be disabled (no off switch).

This server never deploys or mutates anything (Tenet 1); it only inspects text. It is the
"review the artifact before you keep it" seam an agentic SDLC can call at any stage.

The `mcp` SDK is imported lazily in main() so the module stays importable/testable without it.
"""

import logging
import os
import sys

logging.basicConfig(level=os.getenv("GAC_GATE_MCP_LOG_LEVEL", "INFO"), stream=sys.stderr)
logger = logging.getLogger("companion.frontdoor.gate_mcp")

# Make the engine importable (repo root is the parent of frontdoor/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)


def govern(text: str, *, kind: str = "artifact", used_llm: bool = False,
           confidence=None, front_door: str = "mcp-gate") -> dict:
    """Run the deterministic gate over `text` and return a structured verdict. Pure function —
    importable and unit-testable without the MCP SDK. Enforcement is unconditional."""
    from agents.governance_gate import get_gate
    result = get_gate().enforce(
        text, agent="mcp-gate", request="", footer=False,
        used_llm=used_llm, confidence=confidence, front_door=front_door, stage=kind,
    )
    return {
        "blocked": result.blocked,
        "verdict": "BLOCK" if result.blocked else "PASS",
        "trust_zone": result.trust_zone,
        "findings": [
            {"tenet": f.tenet, "code": f.code, "severity": f.severity.value, "message": f.message}
            for f in result.findings
        ],
        "message": (result.response if result.blocked
                    else "PASS — no absolute governance boundary crossed. "
                         "(Review remains the human's; a human deploys — Tenet 1.)"),
    }


def main():
    """Run the stdio MCP server exposing the local gate tool."""
    Server = None
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server   # mcp 1.x
        except ImportError as e:
            raise SystemExit(
                "The MCP SDK is not installed in this environment. Install it:\n"
                "  pip install 'mcp>=1.28.0,<2'\n"
                f"(import error: {e})"
            )

    mcp = Server("companion-gate")

    @mcp.tool()
    def govern_artifact(text: str, kind: str = "artifact", used_llm: bool = False) -> dict:
        """Run an AI-produced artifact (answer, diff, plan, or generated code) through the
        Governed Agentic Companion's always-on governance gate BEFORE accepting it.

        Blocks (verdict=BLOCK) if the artifact claims a deployment/mutation was performed,
        claims a write to a read-only system of record, leaks secret material, or — when
        used_llm=true — is an ungrounded LLM answer below the fact bar. Returns a structured
        verdict with findings. The gate is deterministic and local; it never deploys or mutates
        anything, and it cannot be turned off."""
        return govern(text, kind=kind, used_llm=used_llm)

    logger.info("Companion gate MCP server starting (stdio, local deterministic gate).")
    mcp.run()


if __name__ == "__main__":
    main()
