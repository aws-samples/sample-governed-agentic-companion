"""Tests for the AgentCore runtime entrypoints (SDK-independent cores). No AWS, no SDK.

The entrypoints guard their `bedrock_agentcore` / `mcp` imports, so the reasoning cores
(`handle_invocation`, the MCP tool functions) are importable and testable without either SDK.
Every path must return a GATED response (routed through the governed engine).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "orchestrator"))
sys.path.insert(0, str(ROOT / "agentcore" / "app" / "mcp_runtime"))

import runtime_entrypoint as orch_rt          # noqa: E402
import runtime_mcp_entrypoint as mcp_rt        # noqa: E402


class TestOrchestratorRuntime:
    def test_handle_invocation_prompt_is_gated(self):
        out = orch_rt.handle_invocation({"prompt": "what is the deployment policy?",
                                         "environment": "dev"})
        assert out["gated"] is True
        assert "Governance Outcome" in out["response"]

    def test_handle_invocation_messages_shape(self):
        out = orch_rt.handle_invocation(
            {"messages": [{"role": "user", "content": "how are secrets handled?"}]})
        assert out["gated"] is True
        assert "Governance Outcome" in out["response"]

    def test_empty_prompt_is_handled(self):
        out = orch_rt.handle_invocation({})
        assert out["gated"] is True
        assert "No prompt" in out["response"]

    def test_extract_request_defaults_environment(self):
        prompt, env = orch_rt._extract_request({"prompt": "hi"})
        assert prompt == "hi" and env == "dev"


class TestMcpRuntimeTools:
    def test_ask_companion_is_gated(self):
        out = mcp_rt.ask_companion("what is the deployment policy?")
        assert "Governance Outcome" in out

    def test_companion_kb_is_gated(self):
        out = mcp_rt.companion_kb("how are secrets handled?")
        assert "Governance Outcome" in out

    def test_tool_registry_exposes_both(self):
        assert set(mcp_rt.TOOLS) == {"ask_companion", "companion_kb"}

    def test_build_app_requires_mcp_sdk_clearly(self):
        # Without the mcp SDK installed, build_app raises a clear, actionable error
        # (not an obscure ImportError). If the SDK IS present, it returns an app object.
        try:
            import mcp.server.fastmcp  # noqa: F401
            sdk_present = True
        except ImportError:
            sdk_present = False
        if sdk_present:
            assert mcp_rt.build_app() is not None
        else:
            import pytest
            with pytest.raises(RuntimeError, match="mcp"):
                mcp_rt.build_app()
