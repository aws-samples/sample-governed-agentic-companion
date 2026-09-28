"""Example: a GUIDANCE-ONLY external-tool specialist. REPLACE with your own if you need one.

THE PATTERN (why this shape matters for a governed companion):
  Many builder companions want to help with an external CLI or SaaS tool — a scanner, a
  migration tool, a deployment CLI. The governed way to do that is GUIDANCE-ONLY: the agent
  PLANS the run and EMITS the exact command for a human to execute with their own credentials.
  It never shells out, never invokes the tool, never mutates anything.

  This is the generic form of a domain "external-tool agent". It deliberately has NO subprocess
  call — removing that capability removes the dynamic-subprocess attack surface entirely, and
  keeps the boundary crisp: a human runs the tool (Tenet 1), the companion only advises.

  If a fork genuinely needs to invoke a tool, it should still default to PREPARE-ONLY and put
  any execute/upload/submit behind an explicit, double-gated interlock (an env flag AND an
  explicit call argument) — never on by default. This example does not execute at all.

GOVERNANCE: read/advise only. No deploy or mutation (Tenet 1), no write to a system of record
(Tenet 3), grounded in the knowledge base (Tenet 4), and every response leaves through the
always-on gate via the Orchestrator.
"""

from __future__ import annotations

from ..base_agent import BaseAgent


class ExternalToolSpecialist(BaseAgent):
    """Plans an external tool/CLI run and emits the command for a human to execute.

    Grounds naming/commands in the KB where available; otherwise emits a clearly-templated
    command with placeholders the builder fills in. It NEVER runs the tool."""

    knowledge_dirs = ["tier_a/tooling", "tier_b/tooling"]

    #: the CLI this example advises on — a fork sets this to its real tool.
    tool_name = "example-cli"

    def __init__(self):
        super().__init__("external_tool")

    def get_system_prompt(self) -> str:
        return (
            "You are the External-Tool specialist for a governed builder companion. You are "
            "GUIDANCE-ONLY: you plan a tool/CLI run and produce the exact command for a human to "
            "execute with their own credentials. You NEVER run the tool, deploy, or mutate any "
            "environment (Tenet 1). Ground commands in the knowledge base; if a specific detail "
            "is missing, emit a clearly-marked placeholder rather than guessing (Tenet 4)."
        )

    def handle_request(self, request: str, environment: str = "dev") -> str:
        """Emit a guidance plan: the command(s) to run, and the safety notes — never execution."""
        # A real fork would branch on intent and pull command templates from the KB. The kit
        # keeps it simple and honest: produce a review-ready plan the human runs.
        grounded = self._answer_from_knowledge(request)
        plan = grounded or (
            f"# {self.tool_name}: guidance for '{request.strip()}'\n"
            f"# (No KB entry matched — this is a template; fill in the placeholders.)\n"
            f"{self.tool_name} <subcommand> --input <path-or-resource> [--dry-run]\n"
        )
        return (
            f"[{self.name}] GUIDANCE ONLY — run this yourself with your own credentials; "
            "this companion does not execute external tools (Tenet 1).\n\n"
            f"{plan}\n\n"
            "Safety notes:\n"
            "  - Prefer a --dry-run / preview first where the tool supports it.\n"
            "  - Keep TLS verification enabled; trust a corporate CA via REQUESTS_CA_BUNDLE / "
            "NODE_EXTRA_CA_CERTS rather than disabling it.\n"
            "  - A human reviews the output and decides the next step; nothing here is executed "
            "for you."
        )
