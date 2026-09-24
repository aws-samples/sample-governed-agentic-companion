# Governed Agentic Companion — a starter kit

A **fork-and-build starter kit** for a governed, orchestrator-driven multi-agent builder
companion on **Amazon Bedrock AgentCore**, reachable from **Kiro, Claude Code, or any MCP
client**. It is **safe by construction**: it never deploys or mutates an environment, never leaks
a secret, and never presents an ungrounded guess as fact — and the boundary that keeps it safe has
**no off switch**.

> **⚠️ Sample code — not for production as-is.** This project is provided for demonstration and
> educational purposes only. It ships with example specialists and a generic knowledge base to
> illustrate the governance model. It is **not intended for production use without additional
> security review, testing, and hardening** — including your own review of the authentication,
> authorization, IAM, and credential-provisioning guidance in the deployment docs before you
> deploy anything to an AWS account.

This kit is the reference implementation of the AWS Prescriptive Guidance pattern
[**"The Governed Companion"**](https://apg-library.amazonaws.com/content/b4106bc4-ec93-473e-b904-303d4be0d1b2/1).
It is **product- and customer-agnostic**: it ships with two example specialists and a tiny
generic knowledge base so you can see the governance work in seconds, then swap in your own
specialists and knowledge.

## What it helps with

**The problem.** On a delivery engagement it's easy to stand up an AI assistant; the hard part is
making it *safe to trust* — and making the hard-won project knowledge outlast the people who
learned it. That knowledge (the constraints, the decisions, the gotchas) lives in a few experts'
heads and scattered docs, so every team rotation and every new customer builder pays to rediscover
it. And an assistant that confidently invents a config value, or — worse — claims it "deployed to
prod," is more dangerous than no assistant at all. Most "governed AI" is a hopeful instruction in
a prompt that the model can ignore.

**The core idea.** Capture the project's knowledge once and put it to work every day — for the
delivery team *and* the customer's own builders — as if each had a dedicated, project-trained
expert on tap. The knowledge base is the durable asset: it's versioned and cited, it **self-evolves**
as experts correct it and confirm new facts (human-reviewed, never auto-promoted), and it grows
more valuable over the life of the engagement instead of walking out the door with the team. You're
not renting answers from a generic model — you're building the customer a companion that *knows
their system* and stays with them.

**What this does.** It gives you a governed *team of agents* whose guardrails are **code, not
prompts**. Every response passes an always-on governance gate — with **no off switch** — that
hard-blocks any answer that:

- claims a deployment, apply, promote, restart, or environment mutation happened (a human deploys — always);
- claims a write to a read-only system of record;
- leaks a secret or credential; or
- is an ungrounded LLM guess below the grounding bar.

When it can't ground an answer in the knowledge base, it **says so and offers a path forward**
instead of guessing.

**Who it's for.** Any ProServe / builder team standing up an agentic assistant for an engagement
that must be safe-by-construction and auditable — especially in regulated or production-adjacent
work. It's meant for **everyday use by both audiences**: the delivery team leans on it while
building, and the customer's own engineers keep using it to operate and extend the system after
handover. **Fork it, drop in your engagement's specialists and knowledge, and hand the customer a
governed companion they keep** — the knowledge you captured keeps paying off. MIT-0, so there are
no strings on reuse.

**Why it's different from a chatbot wrapper.** The core answers *deterministically* with no LLM in
the path (so factual recall can't hallucinate); the LLM tiers are optional and still gated; and
every response carries a governance-outcome footer showing which tenets were checked, the measured
grounding confidence, and the knowledge sources it used. Trust is demonstrated, not asserted.

> **Run it now, no cloud, no LLM:**
> ```bash
> python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
> ./run.sh status                                   # governance integrity + knowledge load
> ./run.sh ask "what is the deployment policy?"     # grounded, deterministic, with a footer
> echo "I have deployed to production." | ./run.sh gate   # watch the gate BLOCK it
> ```

## What you get

- **A governed Orchestrator** that classifies each request and routes it to one specialist.
- **An always-on Governance Gate** (`agents/governance_gate.py`) that enforces the tenets as code
  — with no mode flag to turn it off.
- **Example specialists** over a common `BaseAgent` (measured confidence + grounding sources).
- **A three-tier execution model** — deterministic → local LLM (Ollama) → cloud LLM (Bedrock) —
  so the core works with no LLM at all.
- **A hybrid knowledge base** — an in-repo Tier-A baseline (locked) + an optional S3 Tier-B
  overlay (human-promoted) that fails open to the baseline.
- **An optional evidence gateway** (`evidence.provider`, default `none` — a no-op until you
  turn it on) — a provider-neutral provenance contract (`agents/evidence_gateway.py`) that,
  when enabled, builds a packet before dispatch and lets the gate BLOCK on a missing/tampered/
  not-READY packet. It only ever TIGHTENS the gate (never feeds confidence scoring). `./run.sh
  bible` derives the evidence corpus from `knowledge/` — a pointer into the KB, not a rival
  ledger (Tenet 8); `.evidence/` is gitignored.
- **Multi-IDE front doors** — reach the companion from **Kiro, Claude Code, or any MCP client**:
  a local stdio MCP bridge (`frontdoor/mcp_bridge.py`), a direct AgentCore Gateway HTTP path, a
  token-refresh helper (`frontdoor/gateway_token.py`), an **installable Claude Code plugin**
  (`plugin/`) with a Stop-hook governance backstop, and a one-command onboarding installer
  (`onboarding/`) that wires routing pointers for both IDEs.
- **A governance re-baseline script** (`scripts/rebaseline_integrity.py`) — human-run, for when
  you adapt the constitution. The AWS provisioning steps (identity, S3, gateway, the KB-read IAM
  grant) are documented as human-run commands in [`docs/DEPLOY.md`](docs/DEPLOY.md) (Tenet 1 — the
  kit prepares configs and commands; a human runs every cloud mutation).
- **Tests** that run with no AWS and no LLM.

## The governance model (13 tenets)

Codified in [`PRINCIPLES.md`](PRINCIPLES.md), SHA-256 integrity-verified at startup. Two are
**absolute** — they cannot be relaxed by configuration, prompt, or agent reasoning:

| # | Tenet | # | Tenet |
|---|---|---|---|
| 1 | **Human-Owned Deployment** | 8 | Knowledge Compounds Safely |
| 2 | Autonomous, Grounded Review | 9 | Governance Integrity |
| 3 | **Bounded Agency** | 10 | Relentless Quality |
| 4 | Grounded Reasoning / No Hallucination | 11 | Bounded Egress |
| 5 | Transparency & Audit | 12 | Bounded Resource |
| 6 | Security & Data Governance | 13 | Layered Action-Level Enforcement |
| 7 | Sustainability & Cost | | |

## Make it yours (adoption path)

1. **Run it locally** — `./run.sh status` shows the integrity check passing; ask a question and
   watch it answer on the deterministic tier with a governance-outcome footer. No AWS/LLM yet.
2. **Keep the constitution; adjust the wording** of the non-absolute tenets to your org, then
   re-baseline the integrity hash (`scripts/rebaseline_integrity.py`). **Do not add an off switch.**
3. **Replace the example specialists** (`agents/specialists/`) with your domain specialists — each
   is a small class over `BaseAgent`; routing and gating come for free.
4. **Seed your knowledge base** — locked constraints in `knowledge/tier_a/`, medium-velocity
   references in `knowledge/tier_b/` (S3-overlay-eligible via the explicit allowlist).
5. **Turn on a model tier** when you want depth (`config.yaml`).
6. **Provision AgentCore + identity** (human-run) — see [`docs/DEPLOY.md`](docs/DEPLOY.md).
7. **Point your IDE at it** — run `onboarding/install-companion-frontdoor.sh`, install the Claude
   Code plugin from `plugin/`, or wire the MCP server directly. See
   [`docs/FRONT-DOORS.md`](docs/FRONT-DOORS.md).

## Layout

```
governed-agentic-companion/
├── PRINCIPLES.md              # the constitution (the 13 tenets)
├── governance/
│   ├── integrity.yaml         # SHA-256 baseline of PRINCIPLES.md
│   └── integrity_check.py     # startup tamper verification
├── agents/
│   ├── base_agent.py          # measured confidence + grounding sources
│   ├── orchestrator.py        # classify + route to one specialist
│   ├── governance_gate.py     # the always-on gate (no off switch)
│   ├── kb_overlay.py          # hybrid KB: Tier-A baseline + Tier-B S3 overlay
│   └── specialists/           # platform_specialist.py, security_specialist.py (examples)
├── knowledge/
│   ├── PRINCIPLES.yaml         # machine-readable threat model + enforcement roadmap
│   ├── tier_a/                # locked, in-repo, code-reviewed only
│   └── tier_b/                # medium-velocity, S3-overlay-eligible (explicit allowlist)
├── frontdoor/                 # IDE front doors (see frontdoor/README.md)
│   ├── mcp_bridge.py          # local stdio MCP bridge (wired to the deployed runtime)
│   ├── runtime_client.py      # invokes the deployed AgentCore runtime (bearer + retry-on-401)
│   ├── cognito_token.py       # mints + auto-refreshes the runtime token
│   ├── gateway_token.py       # gateway JWT refresh helper (print/write-env/daemon)
│   └── mcp.example.json       # copy into your IDE's MCP config
├── plugin/                    # installable Claude Code plugin (see plugin/README.md)
│   ├── .claude-plugin/        # plugin.json (MIT-0) + marketplace.json
│   ├── .mcp.json              # MCP servers (systems of record read-only)
│   ├── agents/                # orchestrator + example specialists (disallowedTools: Bash)
│   ├── hooks/                 # Stop-hook governance backstop (runs main.py gate)
│   ├── skills/companion/      # SKILL.md
│   └── permissions.example.json
├── onboarding/                # one-command multi-IDE front-door installer
│   ├── install-companion-frontdoor.sh / Install-CompanionFrontDoor.ps1
│   ├── companion-pointer.md          # Kiro inclusion:auto steering pointer
│   ├── companion-claude-pointer.md   # Claude Code CLAUDE.md block
│   └── companion-kiro-mcp.example.json / companion-kiro-gateway.example.json
├── agentcore/                 # AgentCore Runtime packaging
│   ├── agentcore.json.example # runtime/gateway config template
│   └── app/                   # container entrypoint(s) — /invocations + /mcp faces
├── scripts/
│   └── rebaseline_integrity.py # re-baseline the PRINCIPLES.md hash after a governance change
├── docs/                      # DEPLOY.md, FRONT-DOORS.md, GOVERNANCE.md
├── tests/                     # run with no AWS / no LLM
├── config.yaml.example        # copy to config.yaml (gitignored)
├── run.sh / main.py           # the governed CLI (status / ask / gate)
└── requirements.txt
```

## Governance, honestly

The absolute boundaries (no deploy, no system-of-record write, no secret leak, no
fetch-and-execute directive, grounding-block) are enforced **today**. Some *mechanical* controls —
a network-layer egress allowlist, hard resource ceilings, a single action-layer seam across all
front doors — ship as first-increment **text detectors** now, with mechanical enforcement on a
tracked roadmap (`knowledge/PRINCIPLES.yaml`). We state which controls are deterministic vs
advisory rather than overclaiming — that honesty is itself a tenet.

## License

MIT No Attribution (MIT-0) — see [`LICENSE`](LICENSE).
