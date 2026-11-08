# AGENTS.md

Guidance for AI coding agents and contributors working in this repository. Follows the open
[AGENTS.md](https://agents.md/) format.

## Project overview

Governed Agentic Companion is a **product- and customer-agnostic starter kit** for a governed
domain-knowledge companion for builders on Amazon Bedrock AgentCore, reachable from Kiro, Claude
Code, or any MCP client. A single orchestrator routes each request to one domain specialist.
The Governance Gate blocks responses that claim a deployment or a system-of-record write,
contain secret material, or present an exploration-zone answer below the configured grounding
threshold. It has no off switch. The text gate is an observability signal, not proof of what an
agent did (Tenet 13). Governance is defined in `PRINCIPLES.md` (13 tenets, SHA-256 integrity-verified by
`./run.sh status` and by the unit test suite, which CI runs on every push).

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
./run.sh verify --parity        # every gate front door reaches the same verdict
# Terraform (never plan or apply from an agent; init without a backend is safe):
cd agentcore/terraform && terraform fmt -check -recursive \
  && terraform init -backend=false -input=false && terraform validate
```

`terraform test` (plan-only, mocked providers) auto-loads a local `terraform.tfvars`, which breaks
a mocked run. Run it in a copy of the repository without `terraform.tfvars`, `backend.tf` and
`.terraform/` (see `agentcore/terraform/README.md`), for the companion and for `platform/`.

Always run the tests and `status` before committing. Do not commit if tests fail or the
governance integrity check reports tampering.

## Agent inventory

| Agent | Role |
|-------|------|
| Orchestrator | Classifies each request and routes to one specialist; every response passes the always-on Governance Gate. Non-interactive: `./run.sh ask "<request>"` |
| platform (example) | Example platform/infrastructure specialist — replace with your own |
| security (example) | Example security specialist — replace with your own |
| external_tool (example) | Guidance-only external CLI specialist (plans the command; never runs it) |
| diagram (preview) | **Preview, held out of any public release (for example a public samples port) until the owner accepts its redesign.** Current limits: no real peer collaboration (reads only each specialist's hand-authored `diagram_facts`, never asks peers or the orchestrator, never uses their wider knowledge); intent is largely ignored (the deterministic keyword planner is the default, the Bedrock planner is off); the view splitter overrides an explicit single view; each view is capped (24 nodes, 6 for a generic view), so a dense grouped single overview is impossible, and the quality check scores legibility, not fit to the request. Today it draws AWS architecture diagrams from natural-language requests over a catalogue of every specialist's `diagram_facts` (its own KB slice plus each peer's read-only `diagram_facts()`). Deterministic by default; an opt-in Bedrock planner (`diagram.planner.mode: bedrock`) only selects fact ids and falls back to the deterministic planner on any error. A large selection becomes up to 4 views (presets, then flow views), each its own bundle; every element cites a source (listed per element in the report); measured quality check per view; returns `.drawio`, `.svg`, `.png` and a report per view. Declines honestly, with prompts that work, when the KB cannot support a request (exit 4) or when the AWS Architecture Icons were not staged at image build. `./run.sh diagram "<request>"` |

Specialists are small classes over `BaseAgent` declared in `agents/specialists/`
(`build_specialists()`); routing keywords and gating come for free. Replace the examples with
your domain specialists per engagement.

Three-tier execution: Tier 1 ships. Tiers 2 and 3 are fork extension points
(`llm_answer_or_decline`), and the `llm.*` settings are reserved. **Trust zones:** Tier 1 =
verified (deterministic, grounded by construction); Tier 2/3 = exploration (LLM,
non-deterministic, a claim to verify). The gate blocks exploration-zone answers below the
configured threshold (`grounding.fact_threshold`, default 0.95, tighten-only); verified-zone output
is grounded by construction. A specialist that cannot ground an answer declines and offers next
steps rather than guess.

Success measures: the configured threshold; `./run.sh audit` for gate decisions and sources;
`./run.sh verify --maturity` for grounding and open-gap rates (an advisory proxy); and
`tests/golden` as a regression check (not an accuracy measure).

## Front doors

- **CLI**: `./run.sh status | ask | gate | bible | audit | feedback | plan | verify | diagram |
  diagram-fetch | diagram-assets` (`diagram --check-facts` validates diagram facts, exit 0/1).
- **Claude Code plugin** — `plugin/` (orchestrator + example specialists as sub-agents, a Skill,
  MCP wiring, and a `Stop`-hook governance backstop). MIT-0.
- **Cloud (MCP)** — reach a deployed AgentCore runtime via the local stdio bridge
  (`frontdoor/mcp_bridge.py`) or the gateway HTTP path. See `docs/FRONT-DOORS.md`. The bridge's
  `draw_diagram` tool is the front door that lands the verified diagram files in the developer's
  workspace (runtime upstream by default, or `GAC_BRIDGE_UPSTREAM=gateway`); a direct-gateway
  client gets the gated text and a compact manifest only.
- **Local gate MCP server** — `frontdoor/gate_mcp_server.py` exposes the gate as a local,
  deterministic MCP tool (`govern_artifact`) — no AWS/runtime needed.
- **Agentic-SDLC governance hook** — `integrations/aidlc/` wires the gate as a `Stop`-style
  command hook so an agentic SDLC (AI-DLC or any harness) gains GAC's unbypassable floor.
- **Onboarding** — `onboarding/install-companion-frontdoor.sh` (+ `.ps1`) wires Kiro + Claude
  Code routing pointers without touching a team's own files.

## Shared-identity consume path (multi-flavor deployments)

`agentcore/terraform/` can either OWN its Cognito pool (default, `manage_identity_pool = true` —
unchanged standalone behavior) or CONSUME a shared pool that a separate platform stack owns
(`manage_identity_pool = false` + `existing_user_pool_id` / `existing_oidc_issuer`). On the
consume path GAC does NOT create the pool/domain, but it STILL creates its OWN
`<engine_resource_server_identifier>/invoke` resource-server scope (default `gac-engine`) and its
front-door and OAuth clients IN the shared pool, plus an M2M client only when the gateway's
outbound hop uses OAuth (public mode) (flavor isolation). The gating split is in `cognito.tf` (`pool_count` owned-only, `client_count`
always-when-cognito, `effective_pool_id` = created-or-shared). `backend.tf` (remote S3 state) is
environment-specific and gitignored; only `backend.tf.example` is tracked. This lets one shared
pool and one state backend serve several companions in one account (the `platform/` stack:
`modules/backend-bootstrap` and `modules/shared-identity`, plus an optional shared network).
Flipping a LIVE owning deployment to consume is a human-run state import, never a recreate.

## Private networking (default)

`network_access = "private"` is the default; public mode needs `acknowledge_public_endpoint = true`.
`modules/private-network` builds the VPC (with `create_vpc = true`; your own VPC otherwise),
private subnets, interface endpoints, the S3 gateway endpoint, the endpoint security groups and,
for a VPC it creates, VPC Flow Logs. `resource_policies.tf`, `ecr.tf`, `s3_kb.tf` and `iam.tf`
hold the private-mode denies (gateway and orchestrator via `aws:SourceVpce`, MCP runtime gateway
role only, Memory, ECR, KB bucket, the runtime role's 17-action explicit deny). Opt-ins: the
private access probe (`enable_private_access_probe`, a test stand-in to disable after testing) and
a Route 53 Resolver inbound endpoint (`enable_resolver_inbound_endpoint`). Since 3.2.1 the
private-mode gateway target waits 30 s for the gateway role's grants to propagate. The 2026-10-31
sandbox validation and the post-deploy tests are in `docs/DEPLOY.md`. Do not change these policies
as part of a cleanup.

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
- **Entitlement (3.4.0):** with a JWT authorizer, the gateway and the orchestrator runtime
  require the token's `cognito:groups` (`entitlement_claim_name`) to contain the companion's
  group (`<name_prefix>-users` by default; `require_entitlement_group`, refused as `false` in
  public mode); the identity interceptor re-checks it and fails closed with "GAC: not entitled".
  On a shared pool the companion never creates the group: the pool's owner does, and the plan
  fails clearly if it is missing. Adding a user to the group is a human-run Cognito step
  (`docs/DEPLOY.md`).
- **Governance integrity (Tenet 9):** `PRINCIPLES.md` is SHA-256 baselined and verified by
  `./run.sh status` and by the unit test suite (run in CI on every push); changes require a re-baseline via
  `scripts/rebaseline_integrity.py`.

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
- Never commit the AWS Architecture Icons: no zip, no extracted icon, no catalog. Stage the zip in
  the gitignored `agentcore/diagram-icons/` (or build `.diagram-assets/` locally); generated
  diagrams go to the gitignored `outputs/` tree or the developer's workspace, never into git.
