# Governed Agentic Companion: a starter kit

![version 3.4.1](https://img.shields.io/badge/version-3.4.1-blue)


A **fork-and-build starter kit** for a governed domain-knowledge companion for builders on
**Amazon Bedrock AgentCore**, reachable from **Kiro, Claude Code, or any MCP client**. A single
orchestrator routes each request to one domain specialist, which answers from the project's
knowledge base. The Governance Gate blocks responses that claim a deployment or a system-of-record write,
contain secret material, or present an exploration-zone answer below the configured grounding
threshold. It has no off switch. The text gate is an observability signal, not proof of what an
agent did (Tenet 13).

> **⚠️ Sample code — not for production as-is.** This project is provided for demonstration and
> educational purposes only. It ships with example specialists and a generic knowledge base to
> illustrate the governance model. It is **not intended for production use without additional
> security review, testing, and hardening** — including your own review of the authentication,
> authorization, IAM, and credential-provisioning guidance in the deployment docs before you
> deploy anything to an AWS account.

This kit is the reference implementation of the AWS Prescriptive Guidance pattern
[**"The Governed Companion"**](https://apg-library.amazonaws.com/content/b4106bc4-ec93-473e-b904-303d4be0d1b2/1).
It is **product- and customer-agnostic**: it ships with three example specialists (platform, security,
external_tool), a diagram specialist, and a tiny generic knowledge base so you can see the governance work in seconds, then swap in your own
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
expert on tap. The knowledge base is the durable asset: it's versioned and cited, it **grows** as
experts correct it and confirm new facts (staged, human-reviewed, and promoted by a human), and it grows
more valuable over the life of the engagement instead of walking out the door with the team. You're
not renting answers from a generic model — you're building the customer a companion that *knows
their system* and stays with them.

**What this does.** It gives you a governed companion (one orchestrator, one specialist per
request) whose guardrails are **code, not prompts**. Every response passes an always-on governance
gate, with **no off switch**, that blocks an answer that:

- claims a deployment, apply, promote, restart, or environment mutation happened (a human deploys);
- claims a write to a read-only system of record;
- contains secret or credential material; or
- is an exploration-zone (LLM) answer below the configured grounding threshold (default 0.95,
  tighten-only).

When it can't ground an answer in the knowledge base, it **says so and offers a path forward**
instead of guessing.

**Who it's for.** Any ProServe / builder team standing up an agentic assistant for an engagement
that must be governed and auditable, especially in regulated or production-adjacent
work. It's meant for **everyday use by both audiences**: the delivery team leans on it while
building, and the customer's own engineers keep using it to operate and extend the system after
handover. **Fork it, drop in your engagement's specialists and knowledge, and hand the customer a
governed companion they keep** — the knowledge you captured keeps paying off. MIT-0, so there are
no strings on reuse.

**Why it's different from a chatbot wrapper.** The shipped core answers *deterministically* with
no LLM in the path, so factual recall comes from the knowledge base rather than a model. Tiers 2
and 3 are fork extension points and pass the same gate when a fork adds them. Every passing
response carries a governance-outcome footer showing which tenets were checked, the measured
grounding confidence, and the knowledge sources it used; a blocked response returns a block
notice instead.

> **Run it now, no cloud, no LLM:**
> ```bash
> python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
> ./run.sh status                                   # governance integrity + knowledge load
> ./run.sh ask "what is the deployment policy?"     # grounded, deterministic, with a footer
> echo "I have deployed to production." | ./run.sh gate   # watch the gate BLOCK it
> .venv/bin/python -m pytest -q                     # the full test suite (no AWS, no LLM)
> ```
>
> **Ask for a diagram (preview).** The diagram specialist is a preview with known limits (see
> "What you get"), is not yet production-ready, and a redesign is planned (agent-to-agent knowledge gathering and an intent-aware single-view layout). Download the AWS Architecture Icons Asset Package yourself, then:
> ```bash
> ./run.sh diagram-assets --zip /path/to/the-asset-package.zip   # local, gitignored catalog
> ./run.sh diagram "draw our private network path"               # writes outputs/diagrams/private-request-path/
> ./run.sh diagram "draw the architecture end to end"            # natural language: up to 4 views
> ./run.sh diagram "what talks to Bedrock"                       # a question works too
> ./run.sh diagram "how does memory get written"                 # honest decline (exit 4) with prompts that work
> ```
> Locally this writes the `.drawio`, `.svg` and report; the `.png` also needs the optional
> rasterizer on Python 3.10 or later (`.venv/bin/python -m pip install resvg-py==0.5.0`). The
> runtime images always include it, so the deployed path returns all three files.
> From an IDE, call `draw_diagram` through the local stdio bridge (`frontdoor/mcp_bridge.py`):
> it lands `<name>.drawio`, `.svg`, `.png` and `.report.json` in your workspace after checking
> each SHA-256 ([`docs/FRONT-DOORS.md`](docs/FRONT-DOORS.md)). Diagrams are drawn from
> knowledge-base diagram facts, not from live deployment state.
> Natural-language diagram questions go through `draw_diagram`, `./run.sh diagram` or
> `./run.sh diagram-fetch`; `./run.sh ask` keeps its routing, so a question such as "what talks
> to Bedrock" sent to `ask` is routed as before and is not drawn.

## How to think about this tool — the human decides, always

Read this before you use the companion. It accelerates your work and puts a project-trained
expert and a reasoning engine at your fingertips — but **at its core, the human makes every
decision and the human uses the tool.** The companion never acts on the world on its own.

- **ReAct (Reasoning + Acting), operated by you.** The engine *reasons* over the knowledge base
  and *acts* only by producing review-ready artifacts — an answer, a plan, a config, a command to
  run, a diff to apply. It does not deploy, apply, promote, restart, or mutate anything. You read
  the reasoning, judge it, and take the action if you agree.
- **Human-in-the-loop (HITL) at every decision.** Nothing the companion produces is
  self-executing. A deployment is run by a human (Tenet 1); a knowledge correction is promoted by
  a human (Tenet 8); a system of record is never written by the agent (Tenet 3). The governance
  gate exists to keep those boundaries even if a model tries to cross them.
- **A tool, not an authority.** Treat every answer as *input to your judgment*, not a verdict.
  The governance footer (trust zone, grounding confidence, cited sources) is there so you can
  weigh it — a verified/deterministic answer is grounded by construction; an exploration-zone
  (LLM) answer is a claim to verify. When the companion can't ground an answer, it says so rather
  than guessing — that is your cue to verify, not to trust.
- **You own the outcome.** The companion makes you faster and better-informed; it does not
  transfer accountability. The right decision — and the responsibility for it — stays with you.

Everything below (the gate, the tenets, the trust zones) is the machinery that *enforces* this
human-decides model so you can rely on it.

## What you get

- **A governed Orchestrator** that classifies each request and routes it to one specialist.
- **An always-on Governance Gate** (`agents/governance_gate.py`) that enforces the tenets as code
  — with no mode flag to turn it off.
- **Example specialists** over a common `BaseAgent` (measured confidence + grounding sources).
- **A Diagram specialist, preview** (`agents/specialists/diagram_specialist.py`, registered by
  default). Preview status: it is not yet production-ready, and
  a redesign is planned (agent-to-agent knowledge gathering and an intent-aware single-view layout). Current limits: it does not collaborate with peer
  specialists or the orchestrator (it reads only their hand-authored `diagram_facts`, not their
  wider knowledge); the deterministic keyword planner is the default and largely ignores intent
  ("single view", "both", "public"); the view splitter overrides an explicit single view; and
  each view is capped at 24 nodes, so a dense grouped overview cannot be drawn. Today it draws AWS architecture diagrams from natural-language requests over the knowledge base
  ("draw the architecture end to end", "what talks to Bedrock", "show how a developer request
  reaches the knowledge base", "diagram our security boundaries", or a preset such as "draw our
  private network path"). A request selects facts from the catalogue every specialist shares;
  a large selection is split into up to 4 views, each its own set of files. Every node and
  arrow cites a KB diagram fact or a peer specialist's read-only facts (listed per element in the
  report). When the knowledge base cannot support a request, the answer is an honest decline
  that names what is missing and suggests prompts that work. It is deterministic by default; an
  opt-in Amazon Bedrock planner only selects fact ids from the catalogue, never invents a node
  or an edge, and falls back to the deterministic planner on any error. It measures its own layout against the kit's
  [diagram quality rules](docs/ARCHITECTURE.md#diagram-quality-rules) (score and pass mark), and
  returns `.drawio`, `.svg`, `.png` and a report. The official AWS Architecture Icons are never in this repository:
  you stage the icons zip at image build ([`docs/DEPLOY.md`](docs/DEPLOY.md)); without it the
  specialist declines honestly. Through the local stdio bridge the verified files land in your
  workspace (`outputs/diagrams/<name>/`), with no renderer installed on your machine.
- **A three-tier execution model.** Tier 1 (deterministic) ships and works with no LLM. Tiers 2
  (Ollama) and 3 (Amazon Bedrock) are fork extension points (`llm_answer_or_decline`), and the
  `llm.*` settings are reserved. The gate blocks exploration-zone answers below the configured
  threshold (default 0.95, tighten-only).
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

### Network access: private by default

The domain knowledge is the customer's own, so the AgentCore deployment is **private by default**
(`network_access = "private"`). Both runtimes run in VPC mode in your private subnets (the kit
adds no internet route). Each resource policy is its own control:

- The gateway and the orchestrator runtime accept requests only through their VPC interface
  endpoints (AWS PrivateLink, `aws:SourceVpce`). They have no admin exemption.
- The MCP runtime accepts only the gateway's execution role (SigV4), whatever the network path.
- The KB bucket accepts data access only through the S3 gateway endpoint, except the knowledge
  admins (`knowledge_admin_principal_arns` plus the Terraform runner) and AWS service principals.
- The ECR repositories accept image pulls only through the ECR endpoints, except the runtime role
  and the knowledge admins.
- AgentCore Memory (`enable_memory`) accepts data-plane calls only through the data-plane
  endpoint, except calls an AWS service makes on your behalf. It has no admin exemption.

A valid token used from the internet is refused: outside the VPC the gateway hostname still resolves
to public AWS addresses, and the resource policy denies the request (HTTP 403). IDEs connect over
your VPN / Direct Connect path (with an optional Route 53 Resolver inbound endpoint for DNS) or
from an in-VPC desktop ([`docs/FRONT-DOORS.md`](docs/FRONT-DOORS.md)). The gateway front door
needs `deploy_gateway = true` (default `false`). The private network comes from
`agentcore/terraform/modules/private-network` (your VPC, or a sandbox VPC with `create_vpc = true`),
or from a shared `platform/` stack that several companions consume
([`docs/DEPLOY.md`](docs/DEPLOY.md#adoption-paths)). The architecture is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#architecture-diagram).

![Private request path in private mode with deploy_gateway = true: a builder on a corporate network signs in to Amazon Cognito on its public endpoint, then the IDE reaches the AgentCore Gateway endpoint over VPN or Direct Connect (HTTPS 443, JWT); the gateway verifies the token with keys from Cognito, calls the identity interceptor and calls the MCP runtime with SigV4 as the gateway role, and the MCP runtime runs the orchestrator and gate in-process; the stdio bridge through the data-plane endpoint to the orchestrator runtime is the path when the gateway is not deployed (the default); an outside caller with a valid JWT is denied with HTTP 403 at the gateway, the MCP runtime and the orchestrator runtime](docs/architecture/diagrams/private-request-path.svg)

Private request path in private mode with `deploy_gateway = true` (without the gateway, the
stdio bridge through the data-plane endpoint is the path). Source, the runtime-egress figure and the
adoption-paths figure: [`docs/architecture/diagrams/`](docs/architecture/diagrams/); details in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#architecture-diagram).
`network_access = "public"` is a **demo opt-in** behind `acknowledge_public_endpoint = true`.
**Warning:** in public mode anyone holding a valid token can query the knowledge from anywhere.
Private mode does not control what an IDE assistant does with a tool result; read
[What private mode does not protect](docs/SECURITY.md#what-private-mode-does-not-protect). Deploy
steps and the post-deploy tests are in [`docs/DEPLOY.md`](docs/DEPLOY.md).

## The governance model (13 tenets)

Codified in [`PRINCIPLES.md`](PRINCIPLES.md), SHA-256 integrity-verified by `./run.sh status` and
by the unit test suite (run in CI on every push). Two are
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
   references in `knowledge/tier_b/` (S3-overlay-eligible via the explicit allowlist). With your
   domain expert, extend the golden regression set (`tests/golden/`) when you add knowledge.
5. **Turn on a model tier** when you want depth (`config.yaml`).
6. **Provision AgentCore + identity** (human-run) — see [`docs/DEPLOY.md`](docs/DEPLOY.md). Choose
   an adoption path first: a standalone companion that owns its Cognito pool and network, or a
   companion that consumes a shared `platform/` stack (`manage_identity_pool = false`).

   ![Adoption paths: Path A, a consuming companion, creates its own scope and clients in the shared Cognito pool, may keep its state in the platform Amazon S3 state bucket and adds endpoint security group ingress on the optional shared private network; the platform stack holds the state and access-log buckets, the shared pool and the optional shared network; Path B, a standalone companion, owns its Cognito pool and network and may keep its state in the platform state bucket](docs/architecture/diagrams/platform-adoption-paths.svg)

   Source: [`docs/architecture/diagrams/platform-adoption-paths.drawio`](docs/architecture/diagrams/platform-adoption-paths.drawio);
   the paths are explained in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#adoption-paths) and
   [`docs/DEPLOY.md`](docs/DEPLOY.md#adoption-paths).
7. **Deploy, test and tear down a private sandbox** (human-run):
   1. In `agentcore/terraform/terraform.tfvars` set `create_vpc = true`, `deploy_gateway = true` and
      `knowledge_admin_principal_arns` (every principal that runs Terraform or pushes images).
   2. `terraform apply`, then run post-deploy test 12 (policies attached), then tests 2 and 4a from
      outside the VPC. Both must return the resource-policy denial before you hand anything out.
   3. Optionally set `enable_private_access_probe = true` to run the in-VPC tests (test 16), then
      set it back to `false`.
   4. `terraform destroy`. AgentCore ENIs can linger for hours; re-run the destroy later if it
      stops on `DependencyViolation`.
8. **Point your IDE at it** — run `onboarding/install-companion-frontdoor.sh`, install the Claude
   Code plugin from `plugin/`, or wire the MCP server directly. See
   [`docs/FRONT-DOORS.md`](docs/FRONT-DOORS.md).

## Success measures

Each measure is a configured or measured value in the kit. Set the targets for your domain
before rollout.

- **Grounding threshold:** `grounding.fact_threshold` in `config.yaml` (default 0.95,
  configurable upward only).
- **Gate decisions and sources:** `./run.sh audit` (the action-level audit trail).
- **Grounding rate and open-gap rate:** `./run.sh verify --maturity`, an advisory proxy. A grounded
  answer is a passing response with at least one knowledge source; an open gap is a staged
  feedback candidate. Author defaults are `grounding_rate_bar` 0.70 and `decline_rate_ceiling`
  0.30, overridable under `maturity:` in `config.yaml`.
- **Golden regression set:** `tests/golden` checks that routing, sources, and declines do not
  change unexpectedly. It is a regression check, not a measure of answer accuracy.

## Roadmap (not built)

These are planned, not in the kit today. No code for them ships.

- **Scheduled repository ingestion into a source-derived tier.** Tenet 8 allows knowledge derived
  from a source repository, kept apart from Tier A and Tier B and refreshed on a schedule. The kit
  has no ingestion job, no source-derived tier and no scheduler.
- **Mechanical egress allowlist and hard resource ceilings** (Tenets 11 and 12). Today these are
  text detectors in the gate; the mechanical controls are tracked in `knowledge/PRINCIPLES.yaml`.

## Golden regression set

`tests/golden/` holds 15 questions over the shipped example knowledge base
(`golden_questions.yaml`) and one pytest file. Each entry states the expected route, the expected
knowledge sources, and the expected outcome. The set covers grounded answers from each Tier A and
Tier B file (8), in-scope phrasings the knowledge base does not ground today (2, recorded as known
gaps), out-of-scope questions that must decline (3), a deploy request that is answered from the
policy without a deploy claim (1), and an external-tool plan (1). For every entry the test checks
the route, the exact source set, the decline or grounded outcome, that the gate passed the response
with a governance footer, and that the response makes no executed-deploy claim.

```bash
.venv/bin/python -m pytest -q tests/golden
```

This is a regression check that routing, sources, and declines do not change unexpectedly. It is
not a measure of answer accuracy. When you replace the example knowledge, replace or extend the set
with your domain expert, and never edit `knowledge/` only to make an entry pass.

## Layout

```
governed-agentic-companion/
├── PRINCIPLES.md              # the constitution (the 13 tenets)
├── governance/
│   ├── integrity.yaml         # SHA-256 baseline of PRINCIPLES.md
│   └── integrity_check.py     # tamper verification
├── agents/
│   ├── base_agent.py          # measured confidence + grounding sources
│   ├── orchestrator.py        # classify + route to one specialist
│   ├── governance_gate.py     # the always-on gate (no off switch)
│   ├── kb_overlay.py          # hybrid KB: Tier-A baseline + Tier-B S3 overlay
│   ├── diagram/               # diagram engine: facts, labels, layout, quality, emitters, raster
│   └── specialists/           # platform, security, external_tool (examples) + diagram_specialist.py
├── knowledge/
│   ├── PRINCIPLES.yaml         # machine-readable threat model + enforcement roadmap
│   ├── tier_a/                # locked, in-repo, code-reviewed only
│   └── tier_b/                # medium-velocity, S3-overlay-eligible (explicit allowlist)
├── frontdoor/                 # IDE front doors (see frontdoor/README.md)
│   ├── mcp_bridge.py          # local stdio MCP bridge (runtime or gateway upstream; draw_diagram)
│   ├── diagram_writer.py      # verified, bounded local writer for diagram artefacts
│   ├── diagram_relay.py       # gateway relay (allowlisted /mcp URL) for draw_diagram
│   ├── runtime_client.py      # invokes the deployed AgentCore runtime (bearer + retry-on-401)
│   ├── pkce_token.py          # browser sign-in (auth code + PKCE; the developer default)
│   ├── cognito_token.py       # password flow (test harness) + GAC_AUTH_FLOW selection
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
├── agentcore/                 # AgentCore packaging and infrastructure
│   ├── agentcore.json.example # runtime/gateway config template
│   ├── app/                   # container entrypoints: /invocations + /mcp faces, diagram-assets build pins
│   ├── diagram-icons/         # YOU stage the AWS Architecture Icons zip here (gitignored; README only)
│   ├── interceptor/           # gateway REQUEST interceptor Lambda source
│   ├── probe/                 # private access probe Lambda source (opt-in test stand-in)
│   └── terraform/             # the companion stack (human-applied)
│       ├── modules/           # private-network, backend-bootstrap, shared-identity
│       ├── platform/          # optional shared layer: state backend, shared Cognito pool, network
│       └── tests/             # plan-only `terraform test` cases (mocked providers)
├── integrations/aidlc/        # gate as a Stop-style hook for an agentic SDLC
├── scripts/
│   ├── build_icon_catalog.py  # sanitized, checksummed icon catalog (image build and ./run.sh diagram-assets)
│   └── rebaseline_integrity.py # re-baseline the PRINCIPLES.md hash after a governance change
├── docs/                      # ARCHITECTURE, SECURITY, COST, DEPLOY, FRONT-DOORS, GOVERNANCE,
│                              # CORE-INTENT, MATURATION-PATH (+ setup/, security/)
│   └── architecture/diagrams/ # AWS-icon figures: .svg, .png, editable .drawio, .tsx scene source
├── tests/                     # run with no AWS / no LLM (golden/ = the regression set)
├── config.yaml.example        # copy to config.yaml (gitignored)
├── run.sh / main.py           # the governed CLI: status, ask, gate, bible, audit, feedback, plan, verify,
│                              # diagram, diagram-fetch, diagram-assets
├── CHANGELOG.md               # release history (version in config.yaml.example)
├── PROD-READINESS.md          # gates before production use
└── requirements.txt
```

## Documentation

Focused documents, each reviewable on its own (and written to survive public security scanning
— no real account ids, ARNs, endpoints, or secrets):

1. **[System Overview & Architecture](docs/ARCHITECTURE.md)** — component topology (with diagram),
   the three-tier execution model, trust boundaries, and the request/governance data flow.
2. **[Security](docs/SECURITY.md)** — identity & authorization, least-privilege IAM, data
   protection, the always-on boundary, security scanning, and an accepted-security-debt table.
3. **[Cost analysis](docs/COST.md)** — the pay-per-use cost model, a sample monthly estimate, the
   cost drivers/levers, right-sizing guidance, and teardown.
4. **[Deploy & implementation](docs/DEPLOY.md)** — the human-run Terraform sequence to provision
   the AgentCore stack, verify it end-to-end, and tear it down.
5. **[Integration (Kiro & Claude Code)](docs/FRONT-DOORS.md)** — connect either IDE to the
   companion as an MCP server over the AgentCore Gateway (native OAuth/PKCE + fallbacks).
6. **[Governance & operations](docs/GOVERNANCE.md)** — how the boundary is enforced as code, the
   operations runbook and change-management handshake, and the security standards this kit implies.
7. **[Core intent](docs/CORE-INTENT.md)** and **[Maturation path](docs/MATURATION-PATH.md)** — why
   the kit exists and how a fork grows it.
8. **[Source knowledge setup](docs/setup/source-knowledge.md)** — preparing the knowledge a fork
   loads.
9. **[Security scan results](docs/security/scan-results.md)** — scanner results and every inline
   suppression with its justification.
10. **[Production readiness](PROD-READINESS.md)** and **[Changelog](CHANGELOG.md)** — the gates
    before production use, and what changed in each release.

## Governance, honestly

The absolute boundaries (no deploy, no system-of-record write, no secret leak, no
fetch-and-execute directive, grounding-block) are enforced **today**. Some *mechanical* controls —
a network-layer egress allowlist, hard resource ceilings, a single action-layer seam across all
front doors — ship as first-increment **text detectors** now, with mechanical enforcement on a
tracked roadmap (`knowledge/PRINCIPLES.yaml`). We state which controls are deterministic vs
advisory rather than overclaiming — that honesty is itself a tenet.

## License

MIT No Attribution (MIT-0) — see [`LICENSE`](LICENSE).
