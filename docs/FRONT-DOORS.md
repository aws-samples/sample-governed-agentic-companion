# Front doors

The companion is reached from **any MCP-capable agentic IDE** — Kiro, Claude Code, or another
MCP client — over the same governed engine. There are three ways to connect, depending on whether
you run the engine locally or reach a deployed AgentCore runtime, and whether you want packaged
agents.

Every path preserves governance identically: the response is gated (in-process locally, or in the
cloud for a deployed runtime), a human deploys (Tenet 1), and systems of record stay read-only
(Tenet 3).

## One-command onboarding

From inside your clone:

```bash
bash onboarding/install-companion-frontdoor.sh          # macOS / Linux / WSL
# or, on Windows:
powershell -ExecutionPolicy Bypass -File onboarding\Install-CompanionFrontDoor.ps1
```

It creates/refreshes the engine venv, runs `main.py status`, and (optionally) installs routing
pointers into the parent workspace for BOTH front doors — a Kiro `inclusion: auto` steering
pointer (`onboarding/companion-pointer.md`) and a clearly-marked block appended to a Claude Code
`CLAUDE.md` (`onboarding/companion-claude-pointer.md`). It never overwrites your own
steering/CLAUDE.md and never deploys anything.

## 1. Local engine (deterministic, no cloud)

Run the CLI directly — `./run.sh status | ask "..." | gate`. In an IDE, the onboarding pointer
routes matching requests to the specialists (`companion-orchestrator`, `companion-platform`,
`companion-security`). No AWS or LLM required for the deterministic tier.

## 2. Claude Code plugin (packaged agents)

Install the plugin under [`../plugin/`](../plugin/README.md) to get the orchestrator + specialist
sub-agents, a domain Skill, MCP wiring, and a **Stop-hook governance backstop** that runs the
engine's gate over each sub-agent's completed response (blocks on absolute Tenet 1/3/6
violations; surfaces the governance footer otherwise):

```
/plugin marketplace add <git-url-of-this-repo>
/plugin install governed-agentic-companion
```

Then merge [`../plugin/permissions.example.json`](../plugin/permissions.example.json) into your
project `.claude/settings.json` and set the env vars referenced in
[`../plugin/.mcp.json`](../plugin/.mcp.json).

## 3. Cloud runtime as an MCP tool (Kiro / Claude Code / any client)

When the engine is deployed on AgentCore, reach it as an MCP tool instead of running it locally.
Two transports — use one; both expose the identical tools `ask_companion` and `companion_kb`:

- **Option A — local stdio MCP bridge** (`frontdoor/mcp_bridge.py`): the IDE spawns the bridge,
  which mints + auto-refreshes a Cognito token and relays each call to the deployed runtime.
  Turnkey and self-refreshing; works as a gateway-down fallback. Kiro config:
  [`../onboarding/companion-kiro-mcp.example.json`](../onboarding/companion-kiro-mcp.example.json).
- **Option B — direct gateway HTTP path**: point the IDE's `http` MCP server at the AgentCore
  Gateway invocations URL with a Bearer JWT; keep it fresh with `frontdoor/gateway_token.py`
  (`print` / `write-env` / `daemon`). Kiro config:
  [`../onboarding/companion-kiro-gateway.example.json`](../onboarding/companion-kiro-gateway.example.json).

Copy the chosen server entry from [`../frontdoor/mcp.example.json`](../frontdoor/mcp.example.json)
(or the Kiro example above) into your IDE's MCP config and merge with any existing `mcpServers`.
See [`../frontdoor/README.md`](../frontdoor/README.md) for the env-file setup and prerequisites.

> **Never commit real paths, ARNs, tokens, or account ids.** The kit gitignores `.kiro/settings/`,
> `config.yaml`, and `.env*`; keep your identity/runtime config in a gitignored
> `~/.companion-frontdoor.env`.
