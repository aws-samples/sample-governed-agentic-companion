# Governed Agentic Companion (Claude Code Plugin)

A product-agnostic, installable starter kit that packages the Governed Agentic Companion
multi-agent system (orchestrator + example specialists), a domain-knowledge Skill, and MCP
wiring into a Claude Code plugin.

This plugin produces **review-ready artifacts only**. It never deploys.

## Governance (see `../PRINCIPLES.md`)

- **Human-owned deployment (Tenet 1):** no agent deploys, applies, promotes, restarts, or
  mutates any environment — including non-production.
- **Autonomous, grounded review (Tenet 4):** analysis and feedback may be autonomous when
  grounded (traceable to knowledge or a read-only source). Otherwise it defers to a human.
- **Bounded agency (Tenet 3):** writes go only to Jira, Confluence, GitHub. All systems of
  record (AWS and any domain system) are read-only.

## Layout

```
plugin/
├── .claude-plugin/
│   ├── plugin.json          # plugin manifest (MIT-0)
│   └── marketplace.json     # marketplace entry (source: "./")
├── .mcp.json                # MCP servers (systems of record are read-only)
├── permissions.example.json # permission block to merge into your .claude/settings.json
├── agents/                  # sub-agents (each ships disallowedTools: Bash)
│   ├── companion-orchestrator.md
│   ├── companion-platform.md
│   └── companion-security.md
├── hooks/                   # Stop-hook governance backstop (code-level enforcement)
│   ├── hooks.json
│   └── scripts/companion-governance-gate.sh
└── skills/companion/SKILL.md
```

## Governance backstop (Stop hook)

The sub-agents self-gate at the prompt level and are bounded by the read-only MCP boundary, but
a prompt is not code-level enforcement. The bundled `Stop` hook (`hooks/hooks.json`) runs the
companion engine's always-on GovernanceGate over each sub-agent's completed response — the same
deterministic seam the CLI applies (`main.py gate`). It blocks (exit 2) on absolute Tenet 1/3/6
violations and, on every turn, surfaces the governance-outcome footer.

The engine (`main.py` + `agents/`) is not bundled in the plugin; by default the hook resolves it
as the parent of the plugin directory — override with `GAC_ENGINE_REPO`. If the engine or its
venv is not found, the hook fails open (never blocks on infrastructure absence).

## MCP access boundary

| Server | Access | Role |
|--------|--------|------|
| confluence, jira, github | Read + **Write** | Read context; write docs, issues, PRs for human review |
| aws | **Read-only** | Target infrastructure state |
| companion-engine | JWT-authorized | The governed engine (`ask_companion`/`companion_kb`); responses are gated in the cloud |

The `companion-engine` server is reached either through the **AgentCore Gateway** HTTP endpoint
(shown in `.mcp.json`) or the **local stdio bridge** (`../frontdoor/mcp.example.json`, option A) —
use one. Both expose the identical two tools; the gateway does inbound JWT auth and the runtime's
response is already gated in the cloud.

Enforcement is layered (defense in depth):
1. **Per-agent (shipped):** every agent declares `disallowedTools: Bash`, so no agent can run a
   deployment/mutation shell command (terraform, cdk, cloudformation, ansible, kubectl).
2. **Engine + gateway:** the governed engine gates every response and never mutates an
   environment (Tenet 1); systems of record are read-only (Tenet 3).
3. **Project permissions (you apply):** Claude Code enforces permissions from your
   `.claude/settings.json`, not from a plugin's `settings.json`. Merge `permissions.example.json`
   into your project `.claude/settings.json` to auto-allow the collaboration servers, set AWS and
   the engine to `ask`, and explicitly deny deployment/mutation shell commands.

> GitHub is read+write so agents can open pull requests. A human always reviews and merges;
> opening a PR is not a deployment. Swap `github` for a `gitlab` server if that is the
> engagement's source host.

## Install (your Claude Code)

```
/plugin marketplace add <git-url-of-this-repo>
/plugin install governed-agentic-companion
```

Then:
1. Merge `permissions.example.json` into your project `.claude/settings.json`.
2. Set the environment variables referenced in `.mcp.json` (collaboration server URLs/tokens;
   `${GAC_GATEWAY_URL}` + `${GAC_GATEWAY_TOKEN}` for the engine — or switch the `companion-engine`
   entry to the local bridge from `../frontdoor/mcp.example.json`).

## Usage

Invoke `companion-orchestrator` and describe the task (routing is automatic), or call a specialist
directly. Every artifact returns with assumptions, risks, cited sources, and a "human action
required to deploy" note.

## Product-agnostic guarantee

Fork per engagement and replace `agents/specialists/`, `knowledge/`, and the example plugin agents
with your own. Never commit customer identifiers, credentials, hostnames, IP ranges, or schemas
(`PRINCIPLES.md`, Tenet 6).
