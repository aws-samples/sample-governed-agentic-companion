"""AgentCore Runtime entrypoint — Governed Companion Orchestrator (/invocations face).

A THIN adapter that wraps the companion engine (the Orchestrator, which routes to the
specialists) as a Bedrock AgentCore Runtime application. It re-implements NO reasoning and NO
governance: it receives the AgentCore invocation payload, hands the request to
`Orchestrator.handle(...)`, and returns the result.

Governance is preserved BY CONSTRUCTION: `Orchestrator.handle` -> specialist -> `gate_output`,
so every response returned here has already passed the always-on Governance Gate (Tenets
1/3/4/6/11/12/13). There is no path in this wrapper that returns an un-gated response.

Human-owned deployment (Tenet 1) is unchanged: this process ANSWERS requests and produces
review-ready artifacts. It never deploys, applies, promotes, or mutates any environment or
system of record — that boundary lives inside the engine + the gate, which ship in this same
container.

The `bedrock_agentcore` import is guarded so this file stays importable/unit-testable locally
without the runtime SDK; the SDK is a deploy-time dependency (see requirements.txt).
"""

import logging
import os
import re
import sys
from pathlib import Path

# ── Locate the companion engine (single source of truth) ────────────────────
# The engine lives at the repository root. In the deployed container it is copied in alongside
# this app (see Dockerfile); locally it is three parents up. GAC_ENGINE_ROOT overrides.
_ENGINE_ROOT = os.getenv("GAC_ENGINE_ROOT")
_engine_path = Path(_ENGINE_ROOT) if _ENGINE_ROOT else Path(__file__).resolve().parents[3]
if str(_engine_path) not in sys.path:
    sys.path.insert(0, str(_engine_path))

logger = logging.getLogger("gac.agentcore.orchestrator")
logging.basicConfig(level=logging.INFO)

# ── AgentCore Runtime SDK (guarded so the module imports without it) ─────────
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()
except ImportError:  # pragma: no cover - only when the runtime SDK is absent (local/tests)
    BedrockAgentCoreApp = None
    app = None


def _extract_request(payload: dict) -> tuple[str, str]:
    """Pull (prompt, environment) from the AgentCore invocation payload.

    Accepts either {"prompt": "...", "environment": "dev"} or a harness-style
    {"messages": [{"role": "user", "content": "..."}]}. Environment defaults to 'dev'.
    """
    environment = (payload or {}).get("environment", "dev")
    prompt = (payload or {}).get("prompt")
    if not prompt and isinstance((payload or {}).get("messages"), list):
        for msg in reversed(payload["messages"]):
            if msg.get("role") == "user":
                content = msg.get("content")
                prompt = content if isinstance(content, str) else str(content)
                break
    return (prompt or "").strip(), environment


_ACTION_SLUG = re.compile(r"^[a-z_]{1,32}$")


def _str_field(payload: dict, key: str) -> str:
    value = (payload or {}).get(key, "")
    return value if isinstance(value, str) else ""


def handle_invocation(payload: dict, session_id: str = "") -> dict:
    """Core, SDK-independent handler — unit-testable without the AgentCore SDK.
    Returns the GATED engine response in a small envelope. Never returns un-gated text.

    `action` missing or "ask": today's behaviour, byte-identical. `action: "diagram"`: the
    diagram specialist (forced), with `title`/`name` validated by its label policy and
    `include_artifacts` (a JSON boolean; anything else is false), and `planner` ("" or
    "deterministic"; anything else is ""; it can only downgrade the planner). The envelope then
    carries `diagram` (view 1's artefact bundle) plus `diagram_views` (views 2..n, when there
    are several) when include_artifacts is true, else `diagram_manifest` (names, sizes and
    short hashes only; `views` per view for a multi-view answer); neither when the gate
    blocked or nothing rendered.
    """
    from agents.orchestrator import Orchestrator  # imported lazily (engine on sys.path)
    prompt, environment = _extract_request(payload)
    if not prompt:
        return {"response": "No prompt provided.", "gated": True}
    action = (payload or {}).get("action")
    if action not in (None, "ask"):
        if action == "diagram":
            include = (payload or {}).get("include_artifacts") is True
            planner = "deterministic" if _str_field(payload, "planner") == "deterministic" else ""
            orch = Orchestrator()
            text, bundles = orch.handle_with_views(
                prompt, environment, force="diagram", deliver=include,
                title=_str_field(payload, "title"), name=_str_field(payload, "name"),
                planner=planner)
            out = {"response": text, "gated": True}
            if include and bundles:
                out["diagram"] = bundles[0]
                if len(bundles) > 1:
                    out["diagram_views"] = bundles[1:]
            elif not include and orch.last_manifest is not None:
                out["diagram_manifest"] = orch.last_manifest
            logger.info("[gac] handled diagram invocation (session=%s, env=%s)",
                        session_id or "-", environment)
            return out
        from agents.governance_gate import gate_output
        slug = action if isinstance(action, str) and _ACTION_SLUG.match(action) else ""
        text = (f"Unknown action '{slug}'; supported: ask, diagram." if slug
                else "Unknown action; supported: ask, diagram.")
        return {"response": gate_output(text, agent="runtime", request=prompt,
                                        environment=environment), "gated": True}
    response = Orchestrator().handle(prompt, environment=environment)
    logger.info("[gac] handled invocation (session=%s, env=%s)", session_id or "-", environment)
    return {"response": response, "gated": True}


if app is not None:  # pragma: no cover - requires the runtime SDK
    @app.entrypoint
    def invoke(payload, context):
        session_id = getattr(context, "session_id", "") or ""
        return handle_invocation(payload or {}, session_id=session_id)


if __name__ == "__main__":  # pragma: no cover
    if app is None:
        raise SystemExit("bedrock_agentcore runtime SDK not installed; this is a deploy-time dep.")
    app.run()
