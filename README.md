# Governed Agentic Companion — a starter kit

A **fork-and-build starter kit** for a governed, orchestrator-driven multi-agent builder
companion on **Amazon Bedrock AgentCore**, reachable from **Kiro, Claude Code, or any MCP
client**. It is **safe by construction**: it never deploys or mutates an environment, never leaks
a secret, and never presents an ungrounded guess as fact — and the boundary that keeps it safe has
**no off switch**.

This kit is the reference implementation of the AWS Prescriptive Guidance pattern *"The Governed
Companion."* It is **product- and customer-agnostic**: it ships with two example specialists and a
tiny generic knowledge base so you can see the governance work in seconds, then swap in your own
specialists and knowledge.

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
- **Two IDE front doors** — a local stdio MCP bridge (`frontdoor/mcp_bridge.py`) and a direct
  AgentCore Gateway HTTP path — plus a token-refresh helper (`frontdoor/gateway_token.py`).
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
7. **Point your IDE at it** — see [`docs/FRONT-DOORS.md`](docs/FRONT-DOORS.md).

## Layout

```
sample-governed-agentic-companion/
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
│   ├── mcp_bridge.py          # local stdio MCP bridge
│   ├── cognito_token.py       # mints + auto-refreshes the runtime token
│   ├── gateway_token.py       # gateway JWT refresh helper (print/write-env/daemon)
│   └── mcp.example.json       # copy into your IDE's MCP config
├── agentcore/                 # AgentCore Runtime packaging
│   ├── agentcore.json.example # runtime/gateway config template
│   └── app/                   # container entrypoint(s)
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

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
