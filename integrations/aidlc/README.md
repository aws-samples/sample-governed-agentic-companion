# GAC as the governance substrate for an agentic SDLC

This directory wires the **Governed Agentic Companion (GAC)** governance gate into an
**agentic software-delivery lifecycle** — AI-DLC, or any harness with a command-hook / MCP
convention — so that lifecycle gains an **always-on, unbypassable governance floor** it does
not otherwise have.

> **What this is, and is not.** This is a *technical integration* a user can wire up. It is
> **not** an official partnership or endorsement, and GAC is **not** a replacement for an
> agentic SDLC. An agentic SDLC (AI-DLC) governs *building software*; GAC governs *whether an
> AI-produced artifact crosses an absolute safety boundary*. They are complementary layers.

## Why layer GAC under an agentic SDLC

An agentic SDLC applies rigor through workflow stages and approval gates, and verification
through advisory sensors. That is strong, but the rigor is **per-profile** and the sensors are
**advisory** — there is no single floor that blocks, regardless of stage, when an artifact:

- claims a **deployment or mutation was performed** (GAC Tenet 1 — a human deploys),
- claims a **write to a read-only system of record** (Tenet 3),
- **leaks secret material** (Tenet 6), or
- is an **ungrounded LLM answer** presented as fact (Tenet 4).

GAC's gate is that floor. It is deterministic, local, and **has no off switch** — which is
exactly the property an agentic SDLC's own docs are honest about not providing
("generative AI can make mistakes; review the output").

## Two ways to wire it

### 1. Stop-style command hook (`companion-gate-hook.sh` + `hooks.json`)

Mirrors GAC's own Claude-plugin backstop. The harness pipes each completed response/artifact to
the hook on stdin; the hook runs the gate and returns:

- **exit 2** — BLOCK: an absolute Tenet 1/3/6 violation (or an ungrounded LLM artifact). The
  block notice is on stderr and the harness should refuse the artifact.
- **exit 0** — allow (clean, or GAC not installed → **fail-open**, never breaks the workflow).
  On a clean pass the governance-outcome footer is written to stderr for transparency.

Setup:

```bash
export GAC_ENGINE_REPO=/path/to/governed-agentic-companion   # your GAC checkout
# then register integrations/aidlc/hooks.json with your harness's hook mechanism,
# or call the hook directly from the harness's Stop/post-response event.
```

Try it against a known-bad artifact:

```bash
echo 'I have deployed the stack to production.' \
  | bash "$GAC_ENGINE_REPO/integrations/aidlc/companion-gate-hook.sh"; echo "exit=$?"
# -> exit=2, block notice on stderr (Tenet 1: deployment claimed as performed)
```

### 2. Local MCP `gate` tool (`frontdoor/gate_mcp_server.py`)

For harnesses that consume MCP tools, run GAC's gate as a **local, deterministic** MCP server
(no AWS, no runtime, no token — unlike the cloud bridge). It exposes one tool:

```text
govern_artifact(text, kind="artifact", used_llm=false) -> { verdict: "PASS"|"BLOCK", findings: [...] }
```

Register it as an MCP server the harness can call before accepting an AI-produced artifact
(answer, diff, plan, or generated code):

```jsonc
// example MCP server entry (adapt to your harness's mcp config)
{
  "mcpServers": {
    "companion-gate": {
      "command": "python3",
      "args": ["/path/to/governed-agentic-companion/frontdoor/gate_mcp_server.py"]
    }
  }
}
```

## Guarantees preserved

- **No off switch.** Neither surface adds a bypass; the gate enforces the same whether called by
  the engine, the plugin, this hook, or the MCP tool.
- **Human-owned deployment (Tenet 1).** These surfaces only *inspect* text. They never deploy,
  mutate, or write anything.
- **Fail-open on absence, fail-closed on violation.** If GAC is not installed the hook allows the
  workflow to proceed (it is a floor, not a tripwire); when GAC *is* present, an absolute
  violation is blocked and withheld.
- **Audited.** Every gate decision from these front doors is recorded in GAC's action-level audit
  trail (`.audit/trail.jsonl`), tagged with the front door (`aidlc-hook` / `mcp-gate`).
