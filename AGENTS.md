# AGENTS.md

Guidance for AI coding agents and contributors working in this repository. Follows the open
[AGENTS.md](https://agents.md/) format.

## Project overview

Governed Agentic Companion is a **product- and customer-agnostic starter kit** for a governed,
orchestrator-driven multi-agent builder companion on Amazon Bedrock AgentCore, reachable from
Kiro, Claude Code, or any MCP client. It is **safe by construction**: it never deploys or
mutates an environment, never leaks a secret, and never presents an ungrounded guess as fact —
and the boundary that keeps it safe has **no off switch**. Governance is defined in
`PRINCIPLES.md` (13 tenets, SHA-256 integrity-verified at startup).

**Fork-and-build.** This is a starter kit: fork it per engagement, replace the example
specialists and knowledge base with your own, and keep the governance. License is **MIT-0**.

## Setup

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
./run.sh status                 # governance integrity + knowledge load (no AWS/LLM needed)
./run.sh ask "what is the deployment policy?"   # routed + gated, with a governance footer
```

## Build / test / verify

```bash
.venv/bin/python -m pytest -q   # full suite (runs with no AWS and no LLM) — must pass before commit
./run.sh status                 # integrity + knowledge load
echo "I have deployed to prod" | ./run.sh gate   # watch the always-on gate BLOCK an action claim
```

Always run the tests and `status` before committing. Do not commit if tests fail or the
governance integrity check reports tampering.

## Agent inventory

| Agent | Role |
|-------|------|
| Orchestrator | Classifies each request and routes to one specialist; every response passes the always-on Governance Gate. Non-interactive: `./run.sh ask "<request>"` |
| platform (example) | Example platform/infrastructure specialist — replace with your own |
| security (example) | Example security specialist — replace with your own |

Specialists are small classes over `BaseAgent` declared in `agents/specialists/`
(`build_specialists()`); routing keywords and gating come for free. Replace the examples with
your domain specialists per engagement.

Three-tier execution: Tier 1 deterministic (always on) → Tier 2 local LLM (Ollama) → Tier 3
cloud LLM (Bedrock). **Trust zones:** Tier 1 = verified (deterministic, grounded by
construction); Tier 2/3 = exploration (LLM, non-deterministic — a claim to verify). The gate
blocks exploration-zone answers below the 0.95 grounding bar; verified-zone output is grounded
by construction. A specialist that cannot ground an answer declines honestly rather than guess.

## Front doors

- **CLI** — `./run.sh status | ask | gate | bible | audit | feedback | plan | verify`.
- **Claude Code plugin** — `plugin/` (orchestrator + example specialists as sub-agents, a Skill,
  MCP wiring, and a `Stop`-hook governance backstop). MIT-0.
- **Cloud (MCP)** — reach a deployed AgentCore runtime via the local stdio bridge
  (`frontdoor/mcp_bridge.py`) or the gateway HTTP path. See `docs/FRONT-DOORS.md`.
- **Local gate MCP server** — `frontdoor/gate_mcp_server.py` exposes the gate as a local,
  deterministic MCP tool (`govern_artifact`) — no AWS/runtime needed.
- **Agentic-SDLC governance hook** — `integrations/aidlc/` wires the gate as a `Stop`-style
  command hook so an agentic SDLC (AI-DLC or any harness) gains GAC's unbypassable floor.
- **Onboarding** — `onboarding/install-companion-frontdoor.sh` (+ `.ps1`) wires Kiro + Claude
  Code routing pointers without touching a team's own files.

## v2.0.0 capabilities (the uplift)

GAC v2 keeps the governance core unchanged (same 13 tenets; the gate still has no off switch) and
adds a self-evolution loop plus the surfaces that let it serve as a governance substrate:

- **Action-level audit trail** (`agents/audit.py`, `./run.sh audit`) — every gated response, from
  any front door, appends a metadata-only event (front door, agent, trust zone, confidence,
  sources, decision, findings) to `.audit/trail.jsonl` (gitignored). Observability, not a second
  enforcement path — the gate still blocks.
- **Feedback / evolution loop** (`agents/feedback.py`, `./run.sh feedback`) — every work item
  emits a signal; a grounding gap stages a *candidate* learning that a human reviews and promotes
  to a Tier-B overlay entry. Staged → reviewed → promoted (Tenet 8); never self-written; Tier-A
  stays locked.
- **Layered memory** (`agents/memory_layers.py`) — org/team/project precedence over the Tier-A/
  Tier-B KB; `project > team > org`; a layer never escalates trust. No-op by default.
- **Staged mode** (`agents/staged.py`, `./run.sh plan`) — opt-in intent → plan (checkboxed) →
  **human approval** → execute one *gated* step at a time. "Executing" a step produces a
  review-ready artifact; it never deploys or mutates anything (Tenet 1).
- **Sensors + parity guard** (`agents/sensors.py`, `scripts/parity_check.py`, `./run.sh verify`)
  — advisory inspectable verification manifests (report, never block) + a determinism/parity
  check proving every gate front door reaches the same verdict for the same input.
- **Governance substrate for an agentic SDLC** — the gate as a `Stop`-hook + a local MCP tool so
  an agentic SDLC can run an AI-produced artifact through the unbypassable floor before accepting
  it. This is a *technical integration*, **not** a partnership or endorsement, and GAC does not
  replace an agentic SDLC — it is the complementary safety layer (see `integrations/aidlc/README.md`).

## Security considerations

- **Human-owned deployment (Tenet 1):** agents never deploy, apply, promote, restart, or mutate
  any environment, including non-production.
- **Always-on Governance Gate (`agents/governance_gate.py`):** every response passes one
  enforcement seam before the user sees it; no mode/flag disables it. It hard-blocks a response
  that claims a deployment/mutation, claims a write to a read-only system of record, or leaks
  secret material, and blocks an ungrounded exploration-zone answer below the grounding bar.
- **Optional evidence gateway (`evidence.provider`, default `none`):** when enabled, a
  provenance packet can only TIGHTEN the gate — it is never fed into confidence scoring. The
  corpus is derived from `knowledge/` (a pointer, not a rival ledger — Tenet 8) and gitignored.
- **No secrets in the repo:** credentials live in a gitignored env file; the knowledge base
  holds no credentials or PII. Approved LLM providers only (Bedrock, Ollama).
- **Governance integrity (Tenet 9):** `PRINCIPLES.md` is SHA-256 baselined and verified at
  startup; changes require a re-baseline via `scripts/rebaseline_integrity.py`.

## Code style and conventions

- Python 3.9+; keep the deterministic (Tier 1) path free of any LLM dependency.
- New knowledge goes in `knowledge/tier_a` (locked) or `knowledge/tier_b` (overlay-eligible) as
  YAML and must be product- and customer-agnostic (no names, hostnames, IPs, account ids,
  schemas). Customer-specific artifacts belong in `outputs/` (gitignored).
- Commit messages follow Conventional Commits. Run tests before committing; never commit secrets.

## What NOT to do

- Do not add code paths that deploy or mutate an environment (Tenet 1).
- Do not weaken Tenet 1 or Tenet 3 — they are absolute; do not add an off switch to the gate.
- Do not put customer-identifying data anywhere in the repo (Tenet 6).
