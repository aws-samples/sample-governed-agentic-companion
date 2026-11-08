<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# System Overview & Architecture — Governed Agentic Companion

This document describes the architecture of the Governed Agentic Companion starter kit: the
component topology on Amazon Bedrock AgentCore, the three-tier execution model, the request/
governance data flow, and the design principles behind its governance. It is the
companion reference to [`GOVERNANCE.md`](GOVERNANCE.md) (how the boundary is enforced),
[`SECURITY.md`](SECURITY.md) (the security model), and [`COST.md`](COST.md).

## Intended audience

- **Builder / ProServe teams** forking the kit to stand up a governed assistant for an engagement.
- **Security and compliance reviewers** who must approve an autonomous AI component before it is
  used in production-adjacent work.
- **Platform engineers** wiring the companion into Amazon Bedrock AgentCore and their IdP.

Readers should be comfortable with AWS IAM, Amazon Cognito (or an OIDC IdP), containers, and the
Model Context Protocol (MCP). Familiarity with Amazon Bedrock AgentCore is helpful but not required.

## Objectives

After reading this document, you will be able to:

- Explain the component topology and where the trust boundaries sit.
- Trace a request from an IDE front door through the governance gate to a grounded answer.
- Identify which controls are deterministic (verified) versus advisory (exploration).
- Locate the code that owns each responsibility, so you can fork and replace it safely.

## Architecture overview

The companion is a governed **domain-knowledge companion** whose guardrails are code, not prompts.
A single **Orchestrator** classifies each request and routes it to exactly one **specialist**;
every response, from any path, passes through one always-on **Governance Gate** before a caller
sees it. The shipped engine answers **deterministically with no LLM in the path**; Tiers 2 and 3
are fork extension points that pass the same gate when a fork adds them.

The kit runs in three postures, all sharing the same engine and gate:

| Posture | What runs | AWS needed |
|---|---|---|
| **Local CLI** | `./run.sh status \| ask \| gate` | none (no AWS, no LLM) |
| **Local MCP bridge** | IDE → stdio bridge → deployed runtime | AgentCore runtime + identity |
| **Gateway (deployed stack)** | IDE → AgentCore Gateway (JWT) → MCP runtime | full `agentcore/terraform/` stack with `deploy_gateway = true` (default `false`) |

### Architecture diagram

The figures show **private mode, the default** (`network_access = "private"`), with the gateway
deployed (`deploy_gateway = true`; its default is `false`, and then the stdio bridge path in
figure 1 is the front door). In every figure arrows follow the data: a request or a write runs
from the caller to the callee, and a pull (an image, signing keys) runs from the source to the
reader. A red dashed line is a request that is denied. Each
caption says what its dashed lines mean.
Every node and arrow traces to a resource in [`../agentcore/terraform/`](../agentcore/terraform/);
the scene source names the file for each.

![Private request path in private mode with deploy_gateway = true: a builder on a corporate network signs in to Amazon Cognito on its public endpoint, then the IDE reaches the AgentCore Gateway endpoint over VPN or Direct Connect (HTTPS 443, JWT); the gateway verifies the token with keys from Cognito, calls the identity interceptor and calls the MCP runtime with SigV4 as the gateway role, and the MCP runtime runs the orchestrator and gate in-process; the stdio bridge through the data-plane endpoint to the orchestrator runtime is the path when the gateway is not deployed (the default); an outside caller with a valid JWT is denied with HTTP 403 at the gateway, the MCP runtime and the orchestrator runtime](architecture/diagrams/private-request-path.svg)

Source: [`architecture/diagrams/private-request-path.drawio`](architecture/diagrams/private-request-path.drawio)

*Figure 1. Private request path.* The builder IDE sits on an approved network and reaches the
AgentCore Gateway endpoint, an interface endpoint in your private subnets, over HTTPS 443. The path
from the corporate network into the VPC is yours (Client VPN, Site-to-Site VPN or Direct Connect;
this Terraform does not create it), and `private_client_cidrs` on the client endpoint security group
admits it. Your corporate DNS forwarder sends `bedrock-agentcore.<region>.amazonaws.com` to the
optional Route 53 Resolver inbound endpoint (`enable_resolver_inbound_endpoint`), or to your own
resolver. The builder signs in to Amazon Cognito on Cognito's public endpoint ("sign in", from the corporate
network): the
kit creates no Cognito VPC endpoint, and the user pool domain is public. The pool is
owned by this stack (`manage_identity_pool = true`, the default) or shared from `platform/`, and
either way this companion creates its own scope and clients; Microsoft Entra ID is the alternative
(`auth_mode = "entraid"`). The gateway always verifies the JWT with the pool's signing keys
(`CUSTOM_JWT` discovery URL; the dashed "verify token" line runs from Cognito to the gateway), and
its resource policy denies `InvokeGateway` unless `aws:SourceVpce` is the gateway endpoint. The
identity interceptor is a Lambda function in your account, outside the VPC, on by default whenever
the gateway is deployed (`enable_identity_interceptor = true`). The gateway reaches the MCP runtime
with SigV4 as its execution role, the only principal the MCP runtime's resource policy allows. The
MCP runtime runs the orchestrator and the Governance Gate in-process; it does not call the
orchestrator runtime. The "VPN / DX" arrow carries HTTPS 443 with the JWT, and the "SigV4"
arrow is signed as the gateway execution role. The stdio bridge runs through the data-plane endpoint
to the orchestrator runtime, whose resource policy denies any call that does not arrive through that
endpoint; with `deploy_gateway = false` (the default) it is the only front door. The "Regional services" group holds what runs in the Region but not in
your subnets: AWS-managed services (Cognito, AgentCore Gateway and Runtime) and the
account-owned interceptor Lambda. The outside caller is any client outside the VPC (the generic
Internet icon stands in for it): the hostnames still resolve to public AWS addresses, and the
requests are denied with HTTP 403: at the gateway and the orchestrator runtime by their resource
policies (tests 2 and 4a), and at the MCP runtime, which refuses a JWT call as an
authorization-method mismatch because it accepts SigV4 only (test 5a, run outside the VPC); a SigV4
call from any principal other than the gateway role hits its `DenyOtherPrincipals` statement (test
5b). See the [sandbox validation record](DEPLOY.md#sandbox-validation-record-2026-10-31). The access probe (`enable_private_access_probe`) is an opt-in test stand-in for
a workstation; disable it after testing. Dashed lines are opt-in (Resolver, probe), the public
sign-in step and the token check (sign in, verify token), and non-gateway paths (DNS forward, stdio
bridge).

![Runtime egress, arrows follow the data: both runtimes run in VPC mode; their ENIs in the private subnets reach the data-plane endpoint for AgentCore Memory, the ECR endpoints, where the image arrives from Amazon ECR, the runtime endpoints for CloudWatch Logs, X-Ray and Bedrock, and the S3 gateway endpoint for knowledge-base reads and writes and ECR image layers; VPC Flow Logs go to CloudWatch only when the kit creates the VPC (create_vpc = true)](architecture/diagrams/runtime-egress.svg)

Source: [`architecture/diagrams/runtime-egress.drawio`](architecture/diagrams/runtime-egress.drawio)

*Figure 2. Runtime egress.* One node stands for both runtimes: they share one execution role, one
runtime security group and the same private subnets, so their egress is identical. The "AgentCore
Runtime" group is the runtime part of figure 1's "Regional services" group; only
the runtimes' ENIs sit in your private subnets, behind the runtime security group (no ingress;
HTTPS egress only to the endpoint security groups and the S3 prefix list). The ECR endpoints are
`ecr.api` and `ecr.dkr`; the other runtime-only interface endpoints are `logs`, `xray` and
`bedrock-runtime`, plus `comprehend` with `pii_backend = "comprehend"`. Arrows follow the data:
logs, traces, events and model calls run out from the runtimes, the "image" line runs from ECR to
the ECR endpoints (the runtimes pull it), and the "read/write" line to the KB bucket has two heads.
The S3 gateway endpoint carries the knowledge-base reads
and writes (the runtime role holds `s3:GetObject` and `s3:PutObject` on the bucket) and the ECR
image layers. The KB bucket denies data access that
does not arrive through it, except listed knowledge admins, who make the human-promoted writes.
ECR denies pulls that do not arrive through the ECR endpoints, except the runtime role and listed
admins. AgentCore Memory (`enable_memory`) denies data-plane calls that do not arrive through the
data-plane endpoint; the example engine does not call it, so a fork wires it. The Bedrock line is
"LLM, opt-in": the `bedrock-runtime` endpoint (`enable_bedrock_runtime_endpoint = true`) and the
model grants are created by default, but the shipped engine calls no model (Tier 1); only the call
is opt-in, through a fork's Tier 2/3, and the Guardrail with `create_bedrock_guardrail` or
`pii_backend = "guardrails"`. VPC Flow
Logs go to CloudWatch Logs only when the kit creates the VPC (`create_vpc = true`). With `create_vpc
= true` the kit builds two private subnets with only the local and S3 routes, and no internet
gateway or NAT gateway; with your own VPC (`vpc_id`) the kit adds no internet route, and what else
the VPC holds is yours. The interceptor and the probe deliver their logs and traces through Lambda,
outside the VPC. Dashed lines are opt-in: Memory (`enable_memory`), the Bedrock "LLM, opt-in" line
and the flow logs (`create_vpc = true` only); the "image" line is dashed as a deploy-time input.

*In **public mode** (`network_access = "public"`, demo opt-in with `acknowledge_public_endpoint =
true`) there is no VPC, no endpoints and no resource policies: the runtimes use `PUBLIC` network
mode, the gateway reaches the MCP runtime with an OAuth2 M2M client-credentials token, and the
JWT authorizer is the only gate. The IDE assistant's own model sits outside this trust boundary
(see [`SECURITY.md`](SECURITY.md) "What private mode does not protect").*

### Adoption paths

![Adoption paths: Path A, a consuming companion, creates its own scope and clients in the shared Cognito pool, may keep its state in the platform Amazon S3 state bucket and adds endpoint security group ingress on the optional shared private network; the platform stack holds the state and access-log buckets, the shared pool and the optional shared network; Path B, a standalone companion, owns its Cognito pool and network and may keep its state in the platform state bucket](architecture/diagrams/platform-adoption-paths.svg)

Source: [`architecture/diagrams/platform-adoption-paths.drawio`](architecture/diagrams/platform-adoption-paths.drawio)

*Figure 3. Adoption paths.* A human applies the platform stack (`agentcore/terraform/platform/`)
first. It composes `modules/backend-bootstrap` (the Terraform state bucket and its access-log
bucket) and `modules/shared-identity` (one Cognito pool, its domain and one entitlement group per
companion), plus an optional shared private network (`enable_private_network`, default `false`).
**Path A, consumer:** the companion copies `terraform output -json companion_consumer_inputs` into
its tfvars (`manage_identity_pool = false`). It creates no pool, but it still creates its own
`<engine_resource_server_identifier>/invoke` scope and its clients in the shared pool. On the
shared network it adds HTTPS ingress from its runtime security group on the platform's endpoint
security groups (`manage_existing_endpoint_ingress = true`). **Path B, standalone (the default):**
the companion owns its pool (`manage_identity_pool = true`) and its network
(`modules/private-network`). Either companion can keep its state in the platform bucket under its
own key (`backend.tf.example`; `backend.tf` is gitignored), which is why those lines are dashed.
The DynamoDB lock table is a module-level opt-in (`enable_lock_table`) that the platform stack does
not pass, so it is not drawn. Switching a live owning deployment to consume the shared pool is a
human-run state import, never a recreate. Arrows follow the data: each companion stack writes into
the platform resource (its scope and clients, its state, its ingress rules); dashed lines are opt-in (`backend.tf` remote state and the shared network). On a shared network, `aws:SourceVpce` no longer
separates companions (SD-27 in [`SECURITY.md`](SECURITY.md)). The steps are in
[`DEPLOY.md`](DEPLOY.md#adoption-paths).

#### Regenerating the diagrams

**To edit a figure in a fork,** open its `.drawio` file (or the `.svg`) in
[diagrams.net](https://app.diagrams.net) or the draw.io desktop app, change it there, and export
the `.svg` and a PNG. No other tool is needed.

The `.tsx` files are the scene sources the figures were rendered from, kept as a record of each
figure's structure. The renderer they import is not distributed with the kit, so a fork edits the
`.drawio` file as above.

However you edit a figure, keep every node and arrow traceable to a Terraform resource, and keep
identifiers as placeholders (Tenet 6). The PNG must stay under the pre-commit 1024 KB limit. The repository holds
no AWS icon files: the icons appear only as artwork inside the rendered figures.

The committed figures stay hand-authored. The Diagram specialist (below) renders knowledge-base
topics on demand; it does not regenerate or replace these figures.

### Architecture diagram (described)

The deployed (gateway) topology, component by component:

1. **Front door.** A builder's IDE (Kiro, Claude Code, or any MCP client) presents a JWT minted
   by the identity provider (Amazon Cognito by default, or Microsoft Entra ID). The token is
   auto-refreshed by the front-door helpers (`frontdoor/gateway_token.py`).
2. **AgentCore Gateway.** Validates the inbound JWT with a `CUSTOM_JWT` authorizer scoped to the
   allowed client ids, then brokers to the upstream MCP server. In private mode (default) the
   request must also arrive through the gateway interface endpoint: the gateway's resource policy
   denies `InvokeGateway` unless `aws:SourceVpce` is that endpoint, so a valid JWT from the
   internet is refused
   ([resource-based policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html)).
   Its own outbound hop to the MCP runtime is SigV4 signed with the gateway execution role in
   private mode ([MCP server targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html)),
   and an OAuth2 M2M (client-credentials) credential from AgentCore Identity in public mode.
3. **Identity interceptor (on by default with the gateway, ADR-0001 Phase 2).** A REQUEST
   interceptor Lambda derives the verified builder subject from the already-validated JWT and
   injects it (`X-Gac-Actor-Sub`) into the forwarded request. A fork's runtime reads `X-Gac-Actor-Sub` to attribute per-builder memory
   to a verified actor; the example engine does not read it yet. It is invocable only by the
   gateway service principal.
4. **MCP runtime.** A stateless streamable-HTTP `/mcp` face on Amazon Bedrock AgentCore Runtime —
   the gateway's target. It runs the same governed engine in-process; it does not call the
   orchestrator runtime. In private
   mode it has no JWT authorizer, and its resource policy allows only the gateway execution role
   ([runtime-oauth.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).
5. **Orchestrator runtime.** An HTTP `/invocations` face on AgentCore Runtime for the direct
   (stdio-bridge) path. Both runtimes share one least-privilege execution role. In private mode
   both run in `VPC` network mode in your private subnets
   ([agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)),
   reach AWS services only through the interface endpoints and the S3 gateway endpoint, and the
   orchestrator accepts invocations only through the AgentCore data-plane endpoint.
6. **The engine + gate.** Inside each runtime: `Orchestrator.handle()` → one specialist →
   `gate_output()`. Governance is preserved by construction — there is no path that returns an
   un-gated response.
7. **Knowledge base.** A locked in-repo **Tier-A** baseline plus an optional **Tier-B** overlay
   read from Amazon S3 (`kb/curated/overlay.json`), which fails open to the baseline. The bucket
   is private, CMK-encrypted, versioned, HTTPS-only, and access-logged.
8. **Memory (opt-in).** Amazon Bedrock AgentCore Memory backs per-builder session Events and a
   summarization strategy for cross-builder recall. Ready infrastructure — a fork wires its engine
   to it (`GAC_MEMORY_ID`).
9. **Model tier (opt-in).** When a tier is enabled, the runtime invokes an Amazon Bedrock model
   (region-scoped IAM) and, optionally, applies an Amazon Bedrock Guardrail on each write.
10. **Observability.** Amazon CloudWatch Logs (scoped to `/aws/bedrock-agentcore/runtimes/*`),
    X-Ray active tracing on the interceptor, `bedrock-agentcore`-namespaced metrics, and VPC Flow
    Logs for a VPC the kit creates.

### Design principles

The architecture aligns with the AWS Well-Architected Framework:

- **Layered enforcement (Security).** The two absolute tenets, human-owned deployment (T1) and
  bounded agency (T3), are enforced at the action layer (read-only tools/credentials + permission
  boundaries), not by prompt. The gate is a second, observability layer. There is no off switch.
- **Grounded reasoning (Reliability).** Factual recall runs deterministically with no LLM in the
  path, so it cannot hallucinate. LLM tiers are optional and blocked below a measured grounding bar.
- **Least-privilege isolation (Security).** One shared runtime execution role with narrowly scoped,
  region-bound grants; per-component roles for the gateway and interceptor; CMK encryption for the
  knowledge base and the ECR images.
- **Operational excellence.** Every passing response carries a governance-outcome footer (tenets
  checked, measured confidence, sources, gate status); a blocked response returns a block notice.
  Integrity of the constitution is SHA-256 verified by `./run.sh status` and by the unit test suite
  (run in CI on every push).
- **Progressive complexity (Cost / Sustainability).** The kit runs with zero cloud footprint;
  memory, gateway, guardrail, and model tiers are each independent opt-ins, so you pay only for
  what an engagement needs. See [`COST.md`](COST.md).

## Component structure

| Component | Responsibility | Code / IaC |
|---|---|---|
| Orchestrator | Classify a request, route to one specialist | `agents/orchestrator.py` |
| Governance Gate | Always-on, deterministic output enforcement (no off switch) | `agents/governance_gate.py` |
| Specialists | Domain answers over a common contract (measured confidence + sources) | `agents/specialists/` |
| Knowledge base | Tier-A locked baseline + Tier-B S3 overlay (fails open) | `knowledge/`, `agents/kb_overlay.py` |
| Data-protection guard | Reject secrets / redact PII+proprietary before a write (T6); called by feedback staging, and by each write path a fork adds | `agents/data_protection.py` |
| Evidence gateway (opt-in) | Provenance packet that can only TIGHTEN the gate | `agents/evidence_gateway.py` |
| Runtimes | HTTP `/invocations` + stateless MCP `/mcp` faces | `agentcore/app/`, `runtimes.tf` |
| Gateway + interceptor | JWT front door + verified-identity propagation | `gateway.tf`, `interceptor.tf`, `agentcore/interceptor/` |
| Identity | Cognito pool, owned (`manage_identity_pool = true`, default) or shared from `platform/`, with this companion's own scope and clients; or Entra ID. M2M credential in public mode only | `cognito.tf`, `secrets.tf` |
| Private network | VPC (`create_vpc = true`) or yours, private subnets, interface endpoints, S3 gateway endpoint, endpoint SGs, VPC Flow Logs for a created VPC, optional Route 53 Resolver inbound endpoint (`enable_resolver_inbound_endpoint`) | `modules/private-network/`, `network.tf` |
| Private-mode policies | Gateway and orchestrator via `aws:SourceVpce`, MCP runtime gateway role only, Memory, ECR, KB bucket, runtime-role explicit deny | `resource_policies.tf`, `ecr.tf`, `s3_kb.tf`, `iam.tf` |
| Shared platform (optional) | Remote-state bucket, shared Cognito pool with per-app groups, optional shared network, consumed by several companions | `platform/`, `modules/backend-bootstrap/`, `modules/shared-identity/` |
| Private access probe (opt-in test stand-in) | In-VPC Lambda that exercises the gateway and orchestrator paths like a DX/VPN workstation; disable after testing | `probe.tf`, `agentcore/probe/` |
| Diagram specialist | Draws natural-language requests over the shared fact catalogue as up to 4 views of `.drawio`, `.svg`, `.png` and a report; every element cites a source (listed per element in the report); deterministic by default, opt-in Bedrock planner that only selects fact ids; measured quality check; honest declines with prompts that work; declines when icons are not staged | `agents/specialists/diagram_specialist.py`, `agents/diagram/` |
| Icon catalog (build time) | Sanitized, checksummed AWS Architecture Icons from a deployer-staged zip (never committed) | `scripts/build_icon_catalog.py`, Dockerfile `diagram-assets` stage |
| Front doors | Local stdio bridge (incl. `draw_diagram` and the verified local writer), gateway HTTP, Claude plugin, onboarding | `frontdoor/`, `plugin/`, `onboarding/` |

## The Diagram specialist

A builder asks for a diagram in plain language: a preset ("draw our private network path"), a
draw request ("draw the architecture end to end") or a question ("what talks to Bedrock", "show
how a developer request reaches the knowledge base"). By default the request runs
deterministically, with no model call:

1. **Route.** A narrow draw-intent pre-check in `Orchestrator.route()` (draw, diagram, sketch,
   visualise; an artefact noun with a creation verb; "show our ... path/flow/topology" only when
   no other specialist scores). Other questions route exactly as before, so `./run.sh ask` sends
   "what talks to Bedrock" to a text specialist. The diagram doors (`draw_diagram`,
   `./run.sh diagram`, `./run.sh diagram-fetch`) force the diagram specialist and accept
   questions as well as draw requests.
2. **Catalogue.** The specialist builds one fact catalogue per request
   (`agents/diagram/catalogue.py`): its own `diagram_facts` plus each peer's read-only
   `BaseAgent.diagram_facts()` (every fact of that peer, under its own `knowledge_dirs`, so an
   overlay key is cited by the specialist that owns it). No peer's `handle_request` runs; there is
   still one gated response per request. Each fact passes the schema, the label policy (data
   guard, ASCII charset, identifier denylist), the gate's content detectors and an injection
   detector on every string a planner could read. A failing fact is dropped and flagged, never
   invented (Tenet 4). The catalogue holds the facts by qualified id (`n:`, `g:`, `e:`), the
   adjacency, a term index over labels, aliases and tags (ids are never matched), the presets, the
   vocabulary and a SHA-256 of all of it.
3. **Plan.** A planner selects fact ids from the catalogue; it never creates one.
   - A **preset** trigger (`knowledge/tier_a/diagram/topics.yaml` plus Tier-B topics) is tried
     first in every mode: "draw our private network path" draws the `private-request-path` preset
     with its hand-tuned layout, under the same file names as before.
   - The **deterministic planner** (`planner_det.py`) removes sensitive spans (secret shapes,
     IPs, CIDRs, ARNs, account ids, emails, resource ids, hostnames), matches scope phrases ("end
     to end", "talks to", "depends on", "reaches", "security boundaries"), stems the remaining
     words, applies the vocabulary synonyms, scores anchors over the index and selects by scope
     (path, neighbourhood in or out, security, end to end, focus).
   - The **opt-in Bedrock planner** (`planner_bedrock.py`, `diagram.planner.mode: bedrock`) makes
     one Converse call with a forced `select_facts` tool, temperature 0, a guardrail and a bounded
     catalogue view (ids, labels, aliases, tags, descriptions; never prose, refs or sources). Its
     answer is validated: unknown ids are dropped and counted, an invalid plan falls back. Any
     error, missing prerequisite or open breaker falls back to the deterministic planner, and the
     footer names the fallback code. The planner wall time is about `timeout_seconds` (per-read
     bound): botocore connect 3 s plus a read timeout of `timeout_seconds - 3` per socket read, no
     SDK retry. An LLM plan is exploration zone with a measured confidence (every element traced);
     everything else is verified zone.
4. **Close.** `closure.py` adds only catalogue facts: the endpoints of selected edges, a bounded
   connecting path (at most 8 added nodes), the induced edges that belong to the same flow,
   boundary groups and nothing isolated. Every addition and exclusion has a reason in the report.
5. **Views.** `views.py` splits a large selection instead of squeezing it into one figure. A
   small selection (at most 6 nodes and 8 edges) is tried as one generic view. Otherwise the
   selection is covered by presets (each drawn whole; elements outside the selection are context)
   and then by flow views partitioned by the vocabulary's `flow_order`. At most 4 views
   (`diagram.max_views` can only lower it); more is a bounds decline that suggests narrower
   prompts, never a silent trim. "draw the architecture end to end" gives the four example
   presets as four views.
6. **Layout and quality, per view.** `agents/diagram/layout.py` and `route.py` lay each view out
   for a README: the canvas is at most 992 px wide, and the check measures it as shown in an
   880 px README column, where 13 px text appears at 11.5 px or more and 48 px icons at 42 px or
   more (16 px ports, lanes at least 24 px apart). `quality.py` measures the geometry against the
   kit's own rule table (`MESSAGES` plus `NOT_APPLICABLE` in that file) and scores it (pass mark
   85). Up to three layout candidates are tried per view; if none passes, the best is returned as
   a `-draft` with its hard findings listed. Generic and flow views ignore the `col`, `row` and
   `align` hints, which apply only inside their preset.
7. **Render.** SVG and drawio (both embed the sanitized official icons; the drawio uses
   `shape=image` data URIs, no `mxgraph.aws4`), a 2x PNG from the SVG by resvg (in-process,
   self-contained SVG only), and `<name>.report.json`. The PNG scale is `diagram.png_scale` (1 or
   2, default 2), so the shipped topics render about 1700 to 2000 px wide, not at a fixed width
   such as 4800 px. The SVG keeps the figure's structure so an
   outside checker can measure it from the file alone: nested `<g data-name data-kind>` groups
   with their border first, nodes as titled groups with an `overflow="visible"` icon tile, arrows
   in `<g data-name="connectors">` with marker heads, and labels as halo text in
   `<g data-name="arrow-labels">` keyed to their arrow. A multi-view answer names its views
   `<base>-<i>-<view id>`; each view is its own `gac.diagram/1` bundle (`diagram` is view 1,
   `diagram_views` holds views 2 to 4), and one payload budget applies across all views.
8. **Gate.** The manifest text (bounded at 10 KiB; per-view counts replace the per-element lines
   in a multi-view answer) passes the gate with explicit sources (fact files, topics,
   `icon-manifest:sha256:...`). The footer adds the planner, the fact counts, the views and
   "Diagram source: knowledge base, not the live deployment". Artefacts leave only with a passing
   gate.

**Honest declines.** When the knowledge base cannot support a request, no file is produced and
the answer says so: what it recognised ("Asked for"), what is missing ("Missing: no diagram facts
mention ..."), and up to 5 prompts that work (each preset's curated `suggestion`, "draw the
architecture end to end", then "what talks to <component>" for the best-connected components). A
request made only of ignored words ("draw our architecture", "diagram gac") names nothing to
anchor on and is drawn end to end rather than declined. Every
suggestion is replayed before it is offered and kept only if it draws exactly what it promises.
A render whose request named something the knowledge base lacks adds "Not in the knowledge base:
..." to the text. `./run.sh diagram` and `./run.sh diagram-fetch` exit 4 for a decline; the MCP tool returns
the decline text with no saved paths.

Every rendered response carries the line "Drawn from the knowledge base, not from the live
deployment." and the vocabulary's `topology_note`. The front doors are in
[`FRONT-DOORS.md`](FRONT-DOORS.md); the payload and size limits in `agentcore/README.md`.

#### Diagram quality rules

The check applies the figure practice of the kit's own committed figures in
`docs/architecture/diagrams/`, which follow the published
[AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) usage guidance:

- official service icons and AWS group boundaries (AWS Cloud, Region, VPC, subnet, corporate
  data centre), nested in that order;
- arrows follow the data (from = sender); a dashed line is optional or configuration-dependent, a
  red dashed line is a denied call, and a dashed outline around an icon marks an opt-in resource;
  a generic grouping (AgentCore) has a solid border, so dashed always means opt-in (the AWS
  Region keeps its standard dashed teal boundary, named by its own label and icon); the legend
  lists every style used;
- an arrow never runs through an icon, including its own end node: only the run that leaves or
  enters a port may touch it (DQ-P101);
- legible at README width: 13 px text shown at 11 px or more (DQ-A101), 48 px icons;
- hard findings (the figure is a `-draft` until they are gone): overlapping nodes or groups,
  arrows through icons or text, arrows that cross (DQ-X101), shared or doubled lines, a long
  stretched arrow (DQ-L108: a straight run of more than twice the node pitch and 240 px of empty
  line), zig-zags and avoidable bends (DQ-L114, DQ-L119), a group or the canvas more than 65 %
  empty (DQ-L111) and uneven group padding (DQ-A102);
- warnings lower the score but do not block: uneven gaps, short stubs, a tip under a caption.

The report lists every finding with its code; the score is 100 less 15 per FAIL, 6 per FIX and
1.5 per WARN (warnings capped at 15).

### Describe your topology for diagrams

Only `diagram_facts` are drawn; prose KB entries never become nodes or arrows (free text cannot
be traced to one element). A fork adds its own facts, vocabulary and presets in Tier-B (or the S3
overlay) without editing Tier-A:

```
knowledge/tier_b/<domain>/diagram_facts.yaml   # diagram_facts owned by that domain's specialist
knowledge/tier_b/diagram/vocabulary.yaml       # optional: team words, flow order, topology note
knowledge/tier_b/diagram/topics.yaml           # optional presets: id, title, triggers, suggestion
```

Fact schema (validated by `agents/diagram/facts.py`):

| Field | Rule |
|---|---|
| `kind` | `node`, `edge` or `group` |
| `id` | `^[a-z0-9][a-z0-9_-]{0,47}$`, unique per kind |
| `label` | required for node and group; at most 48 ASCII characters; no account ids, ARNs, IPs, CIDRs, resource ids or hostnames; a label that reads like an instruction drops the fact |
| `icon` | node only; an icon catalog key such as `AmazonCognito` (unknown keys draw a labelled generic icon and a warning) |
| `type` | group only: `aws-cloud`, `account`, `region`, `vpc`, `private-subnet`, `public-subnet`, `corporate-dc`, `generic` |
| `parent` / `group` | a group id; nesting follows aws-cloud > account > region > vpc > subnet |
| `from`, `to` | edge only; node ids (arrows follow the data: from = sender) |
| `optional` | edge: a dashed line; node: a dashed outline around the icon (opt-in or configuration-dependent) |
| `style` | `data` (default) or `denied` (red dashed) |
| `topics` | optional, 0 to 10 preset ids; only facts that belong to a curated preset need them |
| `aliases` | optional, node and group: at most 8 names people use for it (`^[a-z0-9][a-z0-9 -]{1,31}$`), matched with weight 2; never drawn |
| `tags` | optional, any kind: at most 8 (`^[a-z0-9-]{2,24}$`); a flow name such as `request` or `storage`, or a role such as `endpoint` or `identity`; never drawn |
| `description` | optional, at most 120 characters in the label charset; shown only to the opt-in planner, withheld when it trips a detector; never drawn |
| `ref` | optional repo-relative source file the fact was checked against; listed per element in the report's `elements`, never drawn |
| `col`, `row`, `align` | optional node layout hints, never drawn, applied only inside a preset the node belongs to: `col` puts the node in that column of its container (instead of the rank its arrows give it), `row` orders a column (lower first), `align` names a node whose centre line this one moves down to |

A bad alias, tag or description drops only that metadata, never a correct node.

**Vocabulary** (`knowledge/tier_a/diagram/vocabulary.yaml`, extended by Tier-B; Tier-A wins on a
clash): `synonyms` (phrase to canonical term, for example `kb: knowledge base`), `ignore` words,
`scopes` (phrase to scope: `end-to-end`, `path`, `path-in`, `neighbourhood-in`,
`neighbourhood-out`, `security`, `focus`), `flow_order` (the order of flow views and the "draw
the <tag> flow" prompts), `roles` (transit, connector, security, entry and provider tags; an
incoming edge with a provider tag, for example a storage read, counts as a dependency of
"what does X depend on") and
`topology_note` (the sentence every rendered answer ends with). Every `flow_order` and role tag
must be used by a fact.

**Presets** (`topics.yaml`): `id`, `title`, `triggers` and an optional `suggestion`, the prompt a
decline offers for the preset. The suggestion must replay to the preset itself (default "draw the
<first trigger>"). A preset of 6 to 8 nodes with `col`, `row` and `align` hints is how a fork gets
measured-quality views of a large architecture; a natural-language request reuses the presets as
views.

**Walkthrough for a fork:**

1. **Pick the owning specialist.** Its `knowledge_dirs` decide whose facts these are. A new team
   specialist inherits `diagram_facts()` and is registered in `build_specialists()`.
2. **Write the facts** in `knowledge/tier_b/<domain>/diagram_facts.yaml` (or the S3 overlay):
   groups, one per boundary; nodes with `label`, `icon`, `group`, `aliases`, `tags` and
   `description`; edges with `from`, `to`, `label`, `optional`, `style`, flow `tags` and a `ref`
   to the file in the fork that performs the behaviour; `topics` only for preset facts.
3. **Add vocabulary** in `knowledge/tier_b/diagram/vocabulary.yaml`: team words, the fork's
   `flow_order` and its `topology_note`.
4. **Add presets** for the views you care about in `knowledge/tier_b/diagram/topics.yaml`, each
   with a `suggestion`.
5. **Check.** `./run.sh diagram --check-facts` validates every fact, the vocabulary, aliases,
   tags, descriptions and presets, lays out and scores each preset (85 or more) and replays
   each suggestion, without writing files; it exits 1 when anything is flagged, so a fork can run it in CI.
   Then try a few `./run.sh diagram "<question>"` prompts and add them to the fork's own golden
   file.
6. **Promote** through the fork's normal review. Tier-B is human-reviewed; a fork never edits
   Tier-A. Customer facts stay in the customer's repository or bucket (Tenet 6).

**Example: memory.** The example KB has no Memory facts, so "how does memory get written"
declines honestly. A fork whose engine writes AgentCore Memory adds one node and two edges in
Tier-B, each `ref` naming the fork's own module that writes the events:

```yaml
# knowledge/tier_b/platform/diagram_facts.yaml
diagram_facts:
  - {kind: node, id: agentcore-memory, label: "AgentCore Memory", icon: AmazonBedrockAgentCore,
     group: agentcore-services, aliases: [memory], tags: [memory], ref: "<your session store module>"}
  - {kind: edge, id: eni-to-dataplane-memory, from: runtime-eni, to: dataplane-endpoint,
     label: "Memory API", tags: [memory], ref: "<your session store module>"}
  - {kind: edge, id: dataplane-to-memory, from: dataplane-endpoint, to: agentcore-memory,
     label: "events", tags: [memory], ref: "<your session store module>"}
```

Then "how does memory get written" renders in that fork.

Each `<name>.report.json` lists every drawn element in `elements`: its generated id (`g1`, `n1`,
`e1`, the ids in the `.svg` and `.drawio`), kind, label, the KB fact it came from (`kb_refs`, such
as `tier_a/platform/diagram_facts#gateway`) and the repo file that fact was checked against
(`refs`), plus a `plan` record (planner, scope, selected facts, closure additions with reasons,
exclusions with reasons, catalogue SHA-256 and the views). A reviewer can trace any node or arrow
back to its fact from the files alone. The shipped example describes this kit's own default
private topology; a fork that deploys differently (for example public mode) replaces those facts
in its own Tier-B.

**Phase 2 (not in 3.4.0): facts proposed from prose.** Drawing from ordinary KB prose is not
built: a deterministic check can prove a passage exists but not that it means "A sends to B", and
live use would bypass human promotion (Tenet 8). The planned shape is an offline
`--propose-facts` step whose candidates (each with a quoted source span, verified verbatim) are
staged for human review through the feedback loop; nothing proposed renders until a human
promotes it.

## Operating model — ReAct, operated by a human (HITL)

The companion's core loop is **ReAct (Reasoning + Acting)**, but the "acting" is deliberately
bounded and the human is the operator, not a bystander:

- **Reason:** the engine classifies the request, routes to a specialist, and reasons over the
  knowledge base (Tier-A + Tier-B), citing sources.
- **Act (produce, don't execute):** the only "action" the engine takes is producing a
  **review-ready artifact** — an answer, a plan, a config, a command to run, a diff to apply. It
  never deploys, applies, promotes, restarts, or writes a system of record.
- **Human decides (HITL):** the human reads the reasoning + the governance footer, judges it, and
  takes the real-world action if they agree. Every decision point is human-owned — a deployment
  (Tenet 1), a knowledge promotion (Tenet 8), any system-of-record change (Tenet 3).

This is why the governance gate and the trust zones exist: they are the machinery that keeps the
"act" side bounded so a human can safely operate the loop. **The human uses the tool; the tool does
not act on the world.** Accountability for the outcome stays with the human.

## Three-tier execution model

- **Tier 1 — deterministic (always on).** Rule/knowledge-based; no LLM dependency. This is the
  **verified zone**: grounded by construction, reproducible, no per-response confidence heuristic.
- **Tier 2 — local LLM (Ollama, fork-added).** For depth without leaving the host.
- **Tier 3 — cloud LLM (Amazon Bedrock, fork-added).** The fork's provider should retry with
  backoff and use a circuit breaker that degrades to Tier 1 under sustained failure; the kit
  reserves these settings under `llm.resilience` in `config.yaml.example`.

The example engine in this kit is **deterministic-only**: `llm.provider` defaults to `none`, and no
provider code ships. A fork adds the Tier 2 or Tier 3 provider and passes each LLM answer through
`BaseAgent.llm_answer_or_decline`, so the gate's grounding bar still applies. The runtime execution
role already grants Amazon Bedrock model invocation in the deployment Region.

Tiers 2/3 are the **exploration zone**: non-deterministic, treated as a claim to verify. The gate
blocks an exploration-zone answer below `grounding.fact_threshold` (default 0.95). The threshold is
configurable upward only (tighten-only): a value below 0.95 is clamped to 0.95, and an invalid value
falls back to 0.95 (`agents/config.py`). A verified-zone answer is grounded by construction and not
subject to the heuristic.

## Request & governance data flow

```
IDE / CLI
  → (gateway path) AgentCore Gateway validates JWT → interceptor injects verified actor
  → runtime: Orchestrator.handle(prompt)
      → classify → one specialist
          → Tier 1 deterministic answer (or Tier 2/3 if enabled + grounded)
          → (feedback staging passes agents/data_protection.py — T6; a fork's KB/memory writes call it too)
      → gate_output()  ── BLOCK ─→ withhold text, return block notice
                        └─ PASS ─→ answer + governance-outcome footer
  → caller
```

The gate hard-blocks any answer that claims a deployment/mutation happened (T1), claims a write to
a read-only system of record (T3), leaks secret material (T6), or is an ungrounded exploration-zone
guess below the bar (T4). See [`GOVERNANCE.md`](GOVERNANCE.md) for the full detector table.

## Trust boundaries

- **Network (private mode, default):** the VPC boundary plus AWS PrivateLink. Endpoint policies
  narrow what can be reached through each endpoint; AgentCore resource policies, the KB bucket
  policy and the ECR repository policies explicitly deny requests that do not arrive through the
  approved endpoints (except listed knowledge admins). Public mode has no network boundary.
- **Front door ↔ gateway:** the IdP-issued JWT (inbound authorizer), plus `aws:SourceVpce` in
  private mode.
- **Gateway ↔ runtime:** SigV4 with the gateway execution role (private) or OAuth2 M2M
  client-credentials (public). A fork's runtime trusts the interceptor's injected actor only on
  the gateway path, because a direct caller does not traverse the interceptor (ADR-0001); the
  example engine does not read the actor yet.
- **IDE assistant:** outside the trust boundary. Tool results enter the IDE's model context
  ([`SECURITY.md`](SECURITY.md) "What private mode does not protect").
- **Runtime ↔ AWS:** one least-privilege, region-scoped execution role.
- **Engine ↔ knowledge:** the only engine write (feedback staging) passes the data-protection guard; a fork's KB or memory writes call it too; Tier-A is never overlaid from S3.

## Deployment

The full stack is Terraform under [`../agentcore/terraform/`](../agentcore/terraform/) — a human
runs `terraform apply` (Tenet 1). `network_access` selects the posture: `private` (default) needs
VPC inputs (or `create_vpc = true` for a sandbox), client CIDRs and the knowledge-admin list;
`public` is a demo opt-in behind `acknowledge_public_endpoint = true`. See [`DEPLOY.md`](DEPLOY.md)
for the ordered walkthrough, the private-mode prerequisites and the post-deploy tests, and
[`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) for the resource inventory
and inputs. For a production multi-account topology, the security and auth patterns here align with
the AWS *Guidance for Enterprise Agentic AI Platform on AWS*, so the step up is incremental.

## Related resources

- [`GOVERNANCE.md`](GOVERNANCE.md) — the always-on gate, detectors, integrity.
- [`SECURITY.md`](SECURITY.md) — security model + accepted security debt.
- [`COST.md`](COST.md) — cost considerations and a sample estimate.
- [`../PRINCIPLES.md`](../PRINCIPLES.md) — the 13-tenet constitution.
