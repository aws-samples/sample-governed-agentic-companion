---
inclusion: auto
name: companion-pointer
description: Routes governed multi-agent requests (platform/infrastructure, security, and any specialist you add) to the Governed Agentic Companion front door installed in this workspace.
---

# Governed Agentic Companion front door pointer

This workspace contains the **Governed Agentic Companion** front door in the
`__COMPANION_REPO__/` subfolder (adjust the name below if you cloned it elsewhere).

For any request the companion's specialists cover (platform/infrastructure, security, and any
specialist you have added), use that front door rather than answering ad hoc:

- Prefer the specialist agents: `@companion-orchestrator` (entry point + routing) and the
  specialists (`@companion-platform`, `@companion-security`, plus your own).
- The deterministic engine lives at `__COMPANION_REPO__/`. Run engine commands from that
  folder: `cd __COMPANION_REPO__ && .venv/bin/python main.py <command>` (or `./run.sh <command>`).
- Governance is always on (PRINCIPLES.md + the always-on GovernanceGate). Produce review-ready
  artifacts only — a human deploys. Systems of record are read-only; writes go only to
  Jira/Confluence/GitHub.

This pointer only exists to route from your workspace root; it never overrides your own
project's steering. The full orchestration rulebook loads automatically when you open the
`__COMPANION_REPO__` folder as part of your workspace.

**Cloud endpoint (optional).** When the engine is deployed on AgentCore, reach it as an MCP tool
instead of running it locally: copy `__COMPANION_REPO__/onboarding/companion-kiro-mcp.example.json`
(local bridge) or `companion-kiro-gateway.example.json` (gateway) into your `.kiro/settings/mcp.json`
and set the referenced env vars. Same engine, same governance (see `agentcore/README.md`).
