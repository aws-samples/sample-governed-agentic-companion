<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Security — Governed Agentic Companion

This document describes the security model of the starter kit: the shared-responsibility split,
identity and authorization, data protection, the always-on governance boundary, and the security
controls the Terraform stack applies. It closes with an honest **accepted-security-debt** table so
every non-hardened default is documented with a rationale and a migration path.

> **⚠️ Sample code — not for production as-is.** Review the IAM, identity, network, and
> credential-provisioning settings against your account's threat model and landing-zone standards
> before deploying. Nothing here should be treated as a turnkey production security posture.

## Shared responsibility

AWS secures the cloud (the managed AgentCore, Bedrock, Cognito, S3, KMS infrastructure). You are
responsible for security *in* the cloud: the IAM policies, identity configuration, network posture,
KMS key choices, log retention, and the knowledge you load. This kit ships secure-by-default
choices for the controls it owns and documents the rest.

## Identity and authorization

Two independent flows (never conflate them):

| Flow | Mechanism | Purpose |
|---|---|---|
| Builder → companion | OIDC JWT (Amazon Cognito PKCE, or Microsoft Entra ID) | A human authenticates; the IDE presents a Bearer token the gateway/runtime authorizer validates |
| Gateway → runtime | Private mode (default): SigV4 signed with the gateway execution role. Public mode: OAuth2 M2M client-credentials (AgentCore Identity) | The gateway authenticates *as itself* to the runtime; no user identity is involved |

- The inbound authorizer is IdP-agnostic (`CUSTOM_JWT` + discovery URL + allowed client ids), so
  swapping Cognito ↔ Entra ID is a configuration change, not a code change.
- Cognito hardening (matches the AWS AgentCore reference samples): `AdvancedSecurityMode ENFORCED`
  (adaptive auth + compromised-credential detection), a strong password policy, admin-only user
  creation, short-lived access/id tokens (60 min) with 30-day refresh, and token revocation.
  MFA is configurable (`cognito_mfa_mode`; set `ON` for production).
- **Verified-identity propagation (ADR-0001).** Per-builder memory is fail-closed by default:
  through the gateway the builder's JWT is validated but not forwarded, so an opt-in REQUEST
  interceptor Lambda derives the verified subject and injects it (`X-Gac-Actor-Sub`). A fork's
  runtime reads `X-Gac-Actor-Sub`; the example engine does not read it yet. A direct caller does
  not traverse the interceptor, so a fork trusts the injected actor only on the gateway path.
- **`auth_mode = "none"` (wording correction).** Earlier kit comments described a runtime without
  an authorizer as "reachable without a token". AgentCore documents that a runtime without a JWT
  authorizer uses the default IAM (SigV4) inbound authorization
  ([runtime-oauth.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).
  The kit now describes it as "no JWT authorizer; SigV4 IAM is the default". This is UNVERIFIED
  against a live deploy. The guards stay conservative: `auth_mode = "none"` still needs
  `allow_unauthenticated = true`, is refused with a gateway, and is refused outright in public mode.
- **Entitlement (action-level authorization, 3.4.0).** Authentication alone no longer grants
  access. With a JWT authorizer and `require_entitlement_group = true` (the default; refused as
  `false` in public mode), the gateway's and the orchestrator runtime's `custom_jwt_authorizer`
  carry a `custom_claim` rule: `cognito:groups` (`entitlement_claim_name`) must CONTAIN the
  companion's group (`<name_prefix>-users`). A token with no groups claim, another group only, or
  another flavor's client is denied. The orchestrator no longer allows the M2M client; in public
  mode with the gateway the MCP runtime allows only the gateway's M2M client (client-credentials
  tokens carry no groups, so the gateway is the entitlement point). The identity interceptor
  re-checks the same claim (defence in depth, not signature verification: the authorizer already
  verified the token, and only the gateway can invoke the interceptor) and fails closed with HTTP
  403 and the JSON-RPC body "GAC: not entitled"; it logs only "entitled" or "not entitled". Its
  environment is encrypted with its own customer managed key, usable only through Lambda for this
  function. On a shared pool the companion never creates or changes a group; it checks at plan
  time (read-only `ListGroups`) that its group exists. Adding a user is a human-run step for the
  pool's owner. The release is verified deny first (`scripts/e2e_deployed.py`): anonymous,
  malformed, unentitled, wrong-group and other-client tokens must be denied before any allowed
  test runs, and an allowed deny test is stop-ship.
- **`autoApprove` is empty in every shipped MCP sample** (`frontdoor/mcp.example.json`,
  `onboarding/companion-kiro-*.example.json`, the `docs/FRONT-DOORS.md` snippets). Each companion
  tool call returns proprietary knowledge into the IDE assistant's context, so each call asks the
  user for confirmation. A team may add tool names, knowing those calls then run without
  confirmation.

## Least-privilege IAM

- **One shared runtime execution role** for both runtimes, with narrowly scoped, region-bound
  grants: ECR pull on the two repos, CloudWatch Logs on `/aws/bedrock-agentcore/runtimes/*`, X-Ray,
  `cloudwatch:PutMetricData` gated on the `bedrock-agentcore` namespace, `bedrock:InvokeModel*` on
  region-scoped foundation-model + inference-profile ARNs, workload-identity tokens, and S3 KB
  read+write scoped to the KB prefix.
- **Per-component roles** for the gateway and the interceptor. The interceptor role carries a
  **permission boundary** capping it at CloudWatch Logs / X-Ray / DLQ writes even if its inline
  policy is later widened.
- Grants that genuinely require `"*"` (X-Ray, workload-token minting) are documented inline with
  the reason they cannot be resource-scoped.
- **`DenyBoundarySelfModification` applies in both modes (3.4.0).** The runtime role's explicit
  Deny (resource policies; runtime, endpoint, gateway, target, memory and harness changes; code
  interpreter and browser creation; and
  from 3.4.0 `SynchronizeGatewayTargets`, `DeleteGateway`, `DeleteGatewayTarget`,
  `DeleteAgentRuntime`, `DeleteAgentRuntimeEndpoint` and `DeleteMemory`; 23 actions) was
  private-mode only. It is now unconditional, so in public mode a compromised engine cannot, for
  example, call `UpdateGateway` to remove the entitlement claim rule. The engine never calls the
  AgentCore control plane, and the deployer's identity is a different principal, so no engine
  path or apply changes. The gateway tool re-sync runs only inside the human-run apply.
- **Opt-in diagram planner grants.** Only with `diagram_planner = "bedrock"`:
  `DiagramPlannerGuardrail` (`bedrock:ApplyGuardrail` on the configured guardrail) and, with
  `diagram_planner_model_regions`, `DiagramPlannerCrossRegionModels` (`bedrock:InvokeModel` on
  the listed Regions' foundation models, conditioned on this account's inference profiles). The
  default deterministic planner adds no grant.

## Data protection

- **Encryption at rest:** the S3 knowledge base and the ECR images use two **customer-managed KMS
  keys** (rotation enabled; `s3_kb.tf`, `ecr.tf`); the access-log bucket uses SSE-S3
  (proportionate for operational logs). All are private with public access blocked. The kit
  creates no Secrets Manager secret (SD-5).
- **Encryption in transit:** the KB bucket policy **denies any non-TLS request**
  (`aws:SecureTransport=false`); all AWS API calls use TLS.
- **The data-protection guard** (`agents/data_protection.py`, Tenet 6) is a built, unit-tested,
  pattern-based guard (22 tests). It **rejects secrets**, **redacts PII and proprietary terms**,
  and **preserves** operational config (ports, endpoints). Mode is configurable
  (`balanced` | `strict` | `redact`). Feedback staging calls `scrub_or_reject` before it stores a
  request: a request with secret material is not staged, and PII is redacted. The example engine
  writes no knowledge or memory, so a fork calls `scrub_or_reject` on each write path it adds.
- **Optional Bedrock Guardrail:** provisioned as the optional managed backend for the write guard
  (`GAC_PII_BACKEND=guardrails`). The example engine does not apply it to builder prompts or
  responses. Prompt-injection screening of builder input is a fork step
  ([AGENTSEC08-BP01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec08-bp01.html)).
- **No secrets in the repo:** credentials live in gitignored env files and Secrets Manager; the
  knowledge base holds no credentials or PII; a CI leak scan enforces this.

## The always-on governance boundary

Every response — from any path — passes one shared gate (`agents/governance_gate.py`) with no off
switch. It hard-blocks a response that claims a deployment/mutation happened (T1), claims a write
to a read-only system of record (T3), leaks secret material (T6), or is an ungrounded LLM guess
below the grounding bar (T4). Enforcement is layered: the *action layer* (read-only tools +
permission boundaries) is the layer that stops mutation, where it is configured; the *text layer* (the gate's detectors) is
the honesty/audit signal. See [`GOVERNANCE.md`](GOVERNANCE.md) for the detector table.

## Network posture

`network_access` selects the posture. **Private is the default**; public is a demo opt-in.

The knowledge plane has these stores and entry points, each of which can return domain
knowledge: the AgentCore Gateway, the orchestrator runtime, the MCP runtime, the KB bucket (and its
KMS key), AgentCore Memory (when `enable_memory = true`), and the two ECR repositories (both images
`COPY knowledge/` into the image, so an image pull discloses the corpus).

**Private mode (`network_access = "private"`, default).** Requests are accepted only when they
arrive through your AWS PrivateLink interface endpoints (or the S3 gateway endpoint), or from a
named knowledge-admin principal. Each control is an explicit `Deny`, so it holds regardless of
identity-based Allows (explicit deny wins:
[policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)).

| Store / entry point | Control (built) | Source |
|---|---|---|
| Gateway `/mcp` | Resource policy: `Allow` with `Principal "*"` only when `aws:SourceVpce` is the gateway endpoint, `Deny` otherwise. OAuth tokens are validated before policy evaluation, so a valid JWT from the internet is refused | [resource-based-policies.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html) |
| Orchestrator runtime | Same shape on `InvokeAgentRuntime`; the `Deny` covers all eight runtime actions | same |
| MCP runtime | No JWT authorizer (SigV4 only). Resource policy allows only the gateway execution role and denies every other principal (`ArnNotEquals aws:PrincipalArn`) | [runtime-oauth.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html), [gateway-target-MCPservers.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html) |
| Memory | Resource policy denies the 15 data-plane actions unless via the data-plane endpoint. On a reused or hub data-plane endpoint it also denies every principal except this companion's runtime role, memory role and knowledge admins (`DenyOtherPrincipalsOnSharedEndpoint`, SD-27) | resource-based-policies.html |
| KB bucket | Bucket policy denies object/list data actions unless via the S3 gateway endpoint, a listed knowledge admin, or an AWS service principal. Bucket-management actions are never denied (no lockout). Block Public Access stays fully on | [example-bucket-policies-vpc-endpoint.html](https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-bucket-policies-vpc-endpoint.html) |
| KB KMS key | `KnowledgeAdminS3Use` lets listed admins use the key through S3 only (`kms:ViaService`) | [key-policy-default.html](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-default.html), [conditions-kms.html](https://docs.aws.amazon.com/kms/latest/developerguide/conditions-kms.html) |
| ECR images | Repository policy denies `BatchGetImage`/`GetDownloadUrlForLayer` unless via the ecr endpoints, a listed admin, the runtime role, or an AWS service principal (`ecr_pull_restriction`, default on) | [repository-policy-examples.html](https://docs.aws.amazon.com/AmazonECR/latest/userguide/repository-policy-examples.html) |
| Runtimes' own egress | `network_mode = "VPC"` in your private subnets, no NAT or internet route. Runtime SG: no ingress, egress TCP 443 only to the endpoint SGs and the S3 prefix list | [agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html) |
| Endpoint SGs | Client endpoints (gateway, data plane): TCP 443 from the runtime SG and `private_client_cidrs` only. Runtime-only endpoints: from the runtime SG only. No rule can carry a `/0` CIDR (variable validation) | [security-group-rules.html](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html) |
| Runtime role | `DenyBoundarySelfModification`: explicit Deny on 17 boundary-changing control-plane actions | [list_bedrock-agentcore.html](https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-agentcore.html) |
| Traces | ADOT `aws_mcp` instrumentation disabled (`trace_tool_content = false`) | see "What private mode does not protect" |
| Unsafe combinations | `guards.tf` `network_guards` preconditions (missing VPC inputs, empty client CIDRs, empty knowledge-admin list, DNS off, unsupported AZs, uncovered route tables, `auth_mode = "none"` on a shared data-plane endpoint, the probe or resolver outside private mode, ...) | n/a |

The gateway hostname stays a public AWS name. Inside networks that use the endpoint's private DNS
it resolves to the endpoint's private IPs; elsewhere it resolves to public AWS addresses, and a
request arriving there is denied by the resource policy. In a sandbox account on 2026-10-31 the
gateway, orchestrator runtime and Memory denials from outside the VPC, the MCP runtime's refusal of
a JWT from outside the VPC (an authorization-method mismatch: it accepts SigV4 only), the
in-VPC gateway and orchestrator paths, the attached ECR and KB bucket policies, and the runtime
role's explicit deny were confirmed against kit 3.2.1 (post-deploy tests 2, 3, 4a, 5a, 5c, 6, 8, 12
and 16 validated; 5b validated for the SigV4 resource-policy denial, but where it ran was not recorded; 9 validated for the
outside denial only; see the
[sandbox validation record](DEPLOY.md#sandbox-validation-record-2026-10-31)). The kit 3.2.2
additions are UNVERIFIED in AWS. The KB bucket denial
from a workstation (test 7), the endpoint SG (test 10) and the ECR pull denial from a workstation
(test 13) stay UNVERIFIED until you run them.

**Public mode (`network_access = "public"` + `acknowledge_public_endpoint = true`, demo only).**
Today's behaviour: the runtimes use `network_mode = "PUBLIC"` and the knowledge plane is reachable
from the internet, protected only by the inbound JWT authorizer. Anyone holding a valid token can
query the knowledge from anywhere, and account principals with ECR or S3 read rights can read the
images and the bucket from anywhere. `auth_mode = "none"` is refused in public mode.

**Private access probe (opt-in, `enable_private_access_probe`, default off).** A test stand-in
for a Direct Connect / VPN workstation: a VPC Lambda function with no ingress, no function URL and
no resource-based policy, whose only network reach is HTTPS to the client endpoint security group.
It uses the caller's token from the invocation payload and never stores, logs or returns it; its
HTTP client follows no redirect, ignores proxy settings, verifies TLS and sends to the gateway and
data-plane hosts only. Its role holds no AgentCore, S3, KMS or Secrets Manager permission (SD-25).
Disable it after testing.

**Route 53 Resolver inbound endpoint (opt-in, default off).** DNS on TCP/UDP 53 from the
validated client CIDRs only, inside the private subnets (SD-26).

**Shared platform network (opt-in, `platform/` with `enable_private_network = true`).** One network
boundary for several companions. Endpoint IDs no longer separate them, so separation rests on each
companion's own JWT and SigV4 authorizers, the G-none-shared guard, the Memory
`DenyOtherPrincipalsOnSharedEndpoint` statement, the probe's restriction to the client SG, and the
per-companion KMS keys (SD-27). A dedicated companion network stays the default.

- The interceptor Lambda makes no data-plane calls, so it runs without a VPC by design (documented
  rationale, not an oversight; SD-6). The gateway's interceptor and the gateway-to-runtime hop run
  on AWS-managed networks outside your VPC; AWS does not document that path further.

## What private mode does not protect

Private mode controls **where requests can come from**. It does not control what an authorized
client does with a response, and it does not stop account administrators.

- **The IDE-context path.** When an authorized IDE receives a tool result, that content enters the
  IDE assistant's context and is sent to whatever model and service the IDE uses. It may also
  persist in IDE history or logs. Mitigations, with their status:
  1. *Configure where supported (not built by the kit):* point the IDE assistant at a model in your
     own account, for example Claude Code with Amazon Bedrock
     ([Claude Code on Amazon Bedrock](https://code.claude.com/docs/en/amazon-bedrock)). With
     `bedrock_runtime_client_access = true`, client networks can reach the kit's `bedrock-runtime`
     endpoint so that reasoning stays in the account. No source verified here documents
     configuring Kiro's model routing to a customer account, so no claim is made about it.
  2. *Design guidance (partly built):* the tools return the Orchestrator's gated answer with a
     governance footer and sources, not raw KB objects. The deterministic tier can still quote a
     knowledge entry verbatim, so treat a tool response as disclosure of that entry.
  3. *Roadmap (not built):* a sensitivity label on `knowledge/**` entries plus a gate detector that
     withholds labelled content from IDE-facing front doors. Neither exists today.
  4. *Partly built:* the audit trail. The gate's `[GovernanceGate]` log line reaches the runtime's
     CloudWatch log group, and `agents/audit.py` writes metadata-only events. Not built: caller
     identity in either record, and durable storage (on the cloud path `.audit/trail.jsonl` lives on
     the runtime's ephemeral file system). A fork would log the interceptor-verified
     `X-Gac-Actor-Sub` with each decision and ship the trail to CloudWatch or S3.
  5. *Built:* `autoApprove` is empty in every sample, so each tool call needs the user's confirmation.
- **Account administrators and insiders.** Principals with AgentCore control-plane, ECR, S3, KMS or
  IAM administration rights can change the boundary from anywhere (`DeleteResourcePolicy`,
  `PutResourcePolicy`, `UpdateAgentRuntime` back to `PUBLIC`, `UpdateGateway`, a second gateway that
  passes the gateway role, `SetRepositoryPolicy`, `PutBucketPolicy`), or copy knowledge out by
  configuration (`kms:PutKeyPolicy` on the KB key, `s3:PutReplicationConfiguration` on the KB
  bucket, `ecr:PutReplicationConfiguration` or `ecr:CreatePullThroughCacheRule`). Recommendation
  (not built): an SCP or permissions boundary that denies these actions to everyone except the
  deployment role, plus `iam:PassRole` on `<name_prefix>-gateway-exec-role` and
  `<name_prefix>-runtime-exec-role`, and CloudTrail alerting on the calls. Action names are from the
  Service Authorization Reference
  ([bedrock-agentcore](https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-agentcore.html),
  [KMS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_kms.html),
  [S3](https://docs.aws.amazon.com/service-authorization/latest/reference/list_s3.html),
  [ECR](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecr.html)). The
  runtime role itself is denied the AgentCore boundary-changing actions in private mode (built).
- **Images held by admins.** Every principal in `knowledge_admin_principal_arns` can pull the
  knowledge-bearing images and read the bucket from anywhere; keep the list short and audited.
  Copies already pulled to laptops or CI caches are outside the kit's control.
- **The observability plane.** CloudWatch Logs and X-Ray are reachable through IAM from any network.
  *Traces:* the ADOT distro pinned in both images (`aws-opentelemetry-distro==0.21.0`) loads its MCP
  instrumentation automatically when `AGENT_OBSERVABILITY_ENABLED=true`; it records tool names and
  tool-call arguments (builder queries) as span attributes, and its code also has a tool-result
  attribute. Private mode disables it by default (`OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=aws_mcp`,
  `trace_tool_content = false`;
  [OpenTelemetry Python configuration](https://opentelemetry.io/docs/zero-code/python/configuration/)).
  Public mode, or `trace_tool_content = true`, keeps that content in spans (SD-24). *Logs:* the
  kit's own log lines are metadata (routing, `[GovernanceGate]` decisions, session ids). In both
  modes, restrict `xray:BatchGetTraces`, `xray:GetTraceSummaries` and Logs read
  (`logs:GetLogEvents`, `logs:FilterLogEvents`, `logs:StartQuery`, `logs:GetQueryResults`) on the
  runtime and spans log groups to the operators who need them. In the 2026-10-31 sandbox run
  (post-deploy test 8) no span carried `gen_ai.tool.call.arguments` or `gen_ai.tool.call.result`;
  re-run test 8 after any ADOT bump.
- **Cognito sign-in and token issuance.** The kit's user pool has a domain (managed login and the
  M2M flow need it), and user pools with a domain cannot complete requests through a VPC endpoint,
  so sign-in and the token endpoint stay on public Cognito endpoints
  ([Cognito and AWS PrivateLink](https://docs.aws.amazon.com/cognito/latest/developerguide/vpc-interface-endpoints.html)).
  They carry credentials and tokens, not knowledge (SD-17).
- **DNS from the runtimes.** The runtimes can resolve public DNS names through the VPC Resolver,
  and security groups cannot block that traffic
  ([security-group-rules.html](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html)).
  It is a low-bandwidth exfiltration channel. The kit's code does not use it; a fork with an LLM
  tier or a compromised dependency could. Recommendation (not built): a Route 53 Resolver DNS
  Firewall rule group on the companion VPC that allows `*.amazonaws.com` and blocks every other
  domain ([DNS Firewall](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall.html);
  it has its own charges on the [Route 53 pricing page](https://aws.amazon.com/route53/pricing/)) (SD-23).
- **KMS in public mode.** The KB key grants no IAM-delegated use in public mode, so a curator
  outside the runtime role cannot promote the overlay without an operator-added key-policy grant,
  and the runner's first `overlay_seed` create may fail on `kms:GenerateDataKey` (UNVERIFIED,
  post-deploy test 11; SD-22).

## Diagram specialist

By default the diagram specialist adds no AWS resource, IAM statement, endpoint, listener or
egress. Rendering is in-process and offline; nothing in `agents/diagram/` or
`frontdoor/diagram_*.py` imports `subprocess` (a test asserts it).

**Planner threat model (opt-in Bedrock planner).** The model is an untrusted selector: it can
only choose fact ids from the catalogue, so it cannot add a node, an edge or a label (Tenet 4).

- *Prompt injection through the request.* The request is data-protected first: a secret shape
  stops the call (fallback `request-secret`), and IPs, CIDRs, ARNs, account ids, emails, resource
  ids and hostnames are removed before anything is sent. The request and the catalogue are passed
  as data blocks, and the system prompt tells the model to ignore instructions inside them.
- *Prompt injection through the KB.* Every string a model can read (labels, aliases,
  descriptions, preset titles) passes a pinned injection detector at catalogue build: a tripped
  label drops its fact, a tripped alias, description or preset title is withheld. Tags cannot hold
  spaces. The model never sees prose KB entries, refs, sources, KB keys or specialist names.
- *A malicious or malformed answer.* The plan is validated in Python whether or not the model kept
  the tool schema: unknown, malformed or kind-mismatched ids are dropped and counted (never
  echoed), an invalid plan falls back, a model title is used only when every long word is in the
  request or the selected facts.
- *Egress and cost (Tenets 11, 12).* One Converse call per request with a forced tool, temperature
  0, a guardrail, at most 1024 output tokens, no SDK or application retry (botocore
  `total_max_attempts` 1), connect 3 s and read `timeout_seconds - 3` s, and a breaker (3
  failures, 300 s). The planner wall time is about `timeout_seconds` (per-read bound). In private
  mode the call stays on the bedrock-runtime interface endpoint. A caller can only downgrade the
  planner.
- *Zone.* An LLM plan is exploration zone with its measured traceability as confidence; any
  fallback is verified zone and names its code in the footer.

**Tenet 12 view bound.** A natural-language request can give up to 4 views (`MAX_VIEWS`, config
can only lower it with `diagram.max_views`), so the work per request rises from at most 3 layouts
to at most 15 (3 for a generic try, then 3 per view) plus 4 rasterizations, all inside the
unchanged 20 s render deadline (which starts after the planner returns), and one 4 MiB payload
budget across all views. A selection is capped at 96 nodes; more views or nodes than the bound is
a decline that suggests narrower prompts, never a silent trim.

| Control | What it does | Source |
|---|---|---|
| Icons never committed | `.gitignore` covers `agentcore/diagram-icons/*` (except its README) and `.diagram-assets/`; a test asserts `git ls-files` holds no zip or catalog | `.gitignore`, `tests/test_diagram_repo_hygiene.py` |
| Catalog sanitizer | The build reads the staged zip member by member (no extraction), refuses absolute, `..` and backslash paths, member-count, size and compression-ratio bombs, parses each SVG with `defusedxml` (no DTD or entities) and keeps an allowlist of elements and attributes (no script, foreignObject, image, event attribute or external reference); each icon at most 16 KiB | `scripts/build_icon_catalog.py` |
| Per-icon checksums | `catalog.json` records each icon's SHA-256 and size; `STATUS.json` records the zip and catalog SHA-256. The runtime verifies the catalog once and each icon before embedding it; a mismatch draws a labelled generic icon with a warning (never silent) | `agents/diagram/assets.py` |
| Optional zip checksum | `diagram_icons_sha256` / `GAC_DIAGRAM_ICONS_SHA256` fails the build on any other zip | `variables.tf`, Dockerfiles |
| Label policy | Every rendered string passes the data guard, an ASCII charset, an identifier denylist (account ids, ARNs, IPs, CIDRs, resource ids, hostnames) and the gate's T1/T3/T6/T11 detectors on the label and on its manifest line; a failing element is dropped and flagged, never redacted-and-drawn | `agents/diagram/labels.py`, `facts.py` |
| Generated ids | Artefacts use generated ids (`g1`, `n1`, `e1`), never KB fact ids | emitters |
| Rasterizer self-containment | `raster.py` refuses any SVG with `<image>`, `<foreignObject>`, `<script>`, `@import`, an external `url(` or `href` before calling resvg; image mode uses `skip_system_fonts` and the two pinned font files | `agents/diagram/raster.py` |
| Bounds | Nodes 24, edges 40, groups 12 per view; views 4; selection 96 nodes; request 2,000 chars, render deadline 20 s, one raster worker, engine text 10 KiB, default MCP result 16 KiB, wire budget 4 MiB across all views; config can only tighten | `agents/diagram/limits.py` |
| Writer boundary | The bridge and CLI write only under a proven workspace root (never `/` or the home folder), refuse traversal, symlinks and overwrites unless asked, verify SHA-256 and size first, write atomically, and on a failure remove the files written by that call (with `overwrite=true` a file already replaced is removed, not restored) | `frontdoor/diagram_writer.py` |
| Upstream size cap | The bridge and `diagram-fetch` write nothing for an upstream result over 5 MiB. The runtime path reads the body with the cap before parsing it (`UPSTREAM_CAP`); the gateway path checks the summed tool-result text against the cap after the MCP SDK has parsed the message, before the artefact JSON is parsed | `frontdoor/runtime_client.py`, `diagram_relay.py` |
| Upstream errors | A denial, HTTP or MCP error, or network failure on any front-door upstream (gateway, runtime, Cognito, probe) is one line: the status and the server's short message with URLs, ARNs, bearer values and JWT-shaped strings removed, never a traceback; `diagram-fetch` exits 3 and writes nothing; bridge tools return an MCP error result | `frontdoor/upstream_errors.py` |
| Probe diagram mode | The probe's `{token, diagram}` mode calls only the tool fixed in `GAC_PROBE_DIAGRAM_TOOL_NAME`; request 1 to 500 chars; result read up to 5 MiB and returned only under 6 MB; token redacted from the text, a token-shaped artefact withheld; `step=` logs only. The laptop helper saves through the bridge's writer | `agentcore/probe/private_access_probe.py`, `frontdoor/probe_fetch.py` |
| Gateway URL allowlist | The relay sends the bearer token only to `https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`; the rejected value is never echoed; the tool prefix is validated | `frontdoor/diagram_relay.py` |
| Pinned base and OS packages | Chainguard Python base pinned by digest (tag in a comment; refresh procedure in `docs/DEPLOY.md`, "Base image"); `ttf-dejavu=2.37-r9` pinned exactly; the venv is built with a pinned `pip==26.2.2`, then pip is uninstalled (no pip in the images); the runtime stage has no shell or package manager, runs as uid 65532, and its code, venv and catalog are root-owned | Dockerfiles |
| Image vulnerability policy | Trivy and Grype over both images (with and without icons), and Trivy over the `resvg-py` SBOM, at every severity: any finding blocks unless it matches an approved, dated entry in `security/accepted-residuals.yaml` on id, package and version | `scripts/security_gate.sh`, `docs/security/scan-results.md` |

## Security scanning

The repo is prepared for comprehensive scanning (see [`../PROD-READINESS.md`](../PROD-READINESS.md)
and `.github/workflows/ci.yml`): offline unit tests, YAML validation, and a customer-data/secret leak scan
run in CI; `.gitleaks.toml` and a pre-commit hook catch secrets before commit. Terraform is written
to pass IaC scanners (checkov/kics) — KMS key policies use discrete actions (never `kms:*`), S3 is
TLS-only + access-logged + lifecycle-managed, ECR uses immutable tags + scan-on-push, and the
documented `"*"` grants carry inline justification.

The standard pre-release gate is [`scripts/security_gate.sh`](../scripts/security_gate.sh): Checkov,
Bandit, pip-audit, detect-secrets, KICS, Trivy and Grype with pinned versions, failing on any
finding that is not an inline, justified suppression or an approved residual. Results, every
suppression and the accepted image residuals: [`security/scan-results.md`](security/scan-results.md).

## Accepted security debt

These are evaluated, intentional choices for a **starter kit** deployed to a dev/sandbox account.
Each lists the rationale and the change to make for production. This table is the honesty contract —
do not remove a row until the item is actually hardened.

| ID | Item | Status | Rationale | Production change |
|---|---|---|---|---|
| SD-1 | `force_destroy` / `force_delete` on S3 + ECR | Accepted (dev) | Lets a dev `terraform destroy` clean up without residue | Set `force_destroy=false` so a teardown cannot drop the KB or the images |
| SD-2 | Cognito MFA defaults `OPTIONAL` (not `ON`) | Accepted (dev) | TOTP MFA is available (a guardrail is defined) but not forced on a dev front door; strong password policy + `AdvancedSecurityMode ENFORCED` compensate | Set `cognito_mfa_mode="ON"` to require MFA |
| SD-3 | Access-log buckets (the KB log bucket and, since 3.2.2, the platform state log bucket) use SSE-S3, not a CMK | Accepted (service constraint) | S3 server access logging does not deliver to a target bucket with SSE-KMS default encryption; the logs are operational metadata, not secrets | None for server access logs. A scope that requires a CMK on access records uses CloudTrail S3 data events to a CMK-encrypted trail instead |
| SD-4 | Log groups that Lambda and AgentCore Runtime create on first write use account-default retention | Accepted | The kit sets retention (365 days) and a CMK only on the log groups it creates: the private access probe and the VPC flow logs | Pre-create those log groups with `retention_in_days` per your policy |
| SD-5 | No hand-managed M2M Secrets Manager secret | Resolved by design | The gateway's outbound M2M hop uses the native AgentCore Identity OAuth2 credential provider (its own managed Token Vault), so the kit creates **no** hand-managed Secrets Manager copy of the credential — avoiding a redundant long-lived secret and its rotation obligation (this also removes the CKV_AWS_304 finding at the root) | A fork needing a readable copy on a non-gateway path supplies `gateway_m2m_secret_arn` and owns that secret's rotation |
| SD-6 | Network mode: runtimes use VPC mode by default; `PUBLIC` is a demo opt-in (`network_access`). The interceptor Lambda stays outside a VPC | Accepted (public opt-in and interceptor) | Private mode is the default (see Network posture). The interceptor Lambda makes no data-plane calls, so VPC attachment adds cold-start latency for no gain (CKV_AWS_117) | Keep `network_access = "private"`; do not set `acknowledge_public_endpoint` outside a demo sandbox |
| SD-7 | Some IAM grants use `"*"` (X-Ray, workload tokens) | Accepted (unavoidable) | These actions do not support resource-level scoping; matches AWS reference IAM | None — scoping is via the role's trust policy; documented inline |
| SD-8 | IAM Access Analyzer is OFF by default | Accepted | It is an account/org baseline a landing zone usually owns; enabling here would collide | Set `enable_access_analyzer=true` only if this stack owns the account baseline |
| SD-9 | Interceptor and probe Lambdas not code-signed (CKV_AWS_272) | Accepted | Built from in-repo single-file source pinned by `source_code_hash`; Lambda code signing needs an AWS Signer profile and signed S3 artifacts, which the zip-upload path does not use | Add an AWS Signer profile and upload signed packages if your policy requires signed Lambda code |
| SD-10 | S3 buckets without cross-region replication (CKV_AWS_144): the KB bucket, its log bucket, and the platform state and state-log buckets | Accepted | Disproportionate for a small single-region KB / access-log bucket; replicating Terraform state is a per-organization DR decision. All are versioned | Enable CRR for a multi-region DR requirement |
| SD-11 | KB read grant flagged "data exfiltration" (kics) | False positive | `s3:GetObject` is resource-scoped to one bucket + the `kb/` prefix (not `"*"`); it is least-privilege and cannot retrieve arbitrary data | None: KICS reports no finding for it in 3.3.1, so the stale marker was removed; the reasoning remains as a comment in `iam.tf` |
| SD-12 | ECR KMS key-admin policy uses service-scoped wildcards (`kms:Delete*`, `kms:Disable*`, `kms:Create*`, …) scoped to the account root | Accepted (dev) | A self-contained kit lets the account root administer its own key; the full `kms:*` wildcard is already avoided | For production, replace the service-scoped wildcards with the exact KMS actions your key administrators need and restrict the statement to a narrow key-admin principal (documented inline in `ecr.tf`) |
| SD-13 | `plugin/.mcp.json` tool entries use `@latest` (unpinned uvx/npx packages) | Accepted (template) | Kept as a template placeholder so a fork picks current tooling; the file is a front-door config a fork edits before use | Pin each entry (`mcp-atlassian`, `@modelcontextprotocol/server-github`, `awslabs.aws-api-mcp-server`) to a tested version before use, and update deliberately (documented inline via `_supply_chain_note`) |
| SD-14 | Public mode: the MCP runtime accepts the front-door JWT clients as well as the M2M client, so an authenticated builder can call it directly and bypass the gateway interceptor | Accepted (demo) | Public mode keeps today's behaviour exactly. Private mode closes this: the MCP runtime has no JWT authorizer and allows only the gateway role | Use private mode |
| SD-15 | ECR, Logs, X-Ray and extra interface endpoints use the AWS default (full-access) endpoint policy | Accepted | Tightening them to `aws:PrincipalAccount` could break image pulls made by the AgentCore service on the account's behalf (which principal pulls is not documented). With the private defaults the Logs and X-Ray endpoints carry the kit's metadata log lines and spans without tool content (`trace_tool_content = false`), the ECR endpoints carry the images to the runtime role, and the runtime role is resource-scoped | Add account-scoped endpoint policies after confirming in a sandbox that image pull and refresh still work |
| SD-16 | Resolved in 3.2.2: a VPC the kit creates (`create_vpc = true`, and the platform's shared VPC) now has VPC Flow Logs (`modules/private-network/flow_logs.tf`: ALL traffic, 365-day retention, its own CMK). A VPC you bring keeps your own flow-log setup | Resolved | The CKV2_AWS_11 skip is removed | Enable flow logs on a VPC you bring |
| SD-17 | Cognito sign-in and the token endpoint stay on public endpoints | Accepted (platform) | A user pool with a domain cannot complete requests through a VPC endpoint; managed login and M2M `client_credentials` are not supported through PrivateLink ([Cognito](https://docs.aws.amazon.com/cognito/latest/developerguide/vpc-interface-endpoints.html)). These carry credentials and tokens, not knowledge | None in the kit; an IdP reachable privately is a fork decision |
| SD-18 | Only if the ECR fallback is used (`ecr_pull_restriction = false`): the images are pullable by any account principal with ECR rights, from anywhere | Not applied by default | Fallback for a runtime that cannot pull its image under the Deny (post-deploy test 13b) | First try adding `"aws:ViaAWSService": "false"` to the Deny's `BoolIfExists` (record the evidence); remove the Deny only if that also fails |
| SD-19 | First-apply exposure window: each runtime is READY for the seconds between its creation and its `PutResourcePolicy` | Accepted (provider limit) | Neither the runtime nor the gateway accepts an inline policy in `hashicorp/aws` 6.66.0. In that window the orchestrator still needs a valid JWT and its ARN is not yet distributed, the MCP runtime is reachable only by account IAM principals with `InvokeAgentRuntime`, and the gateway has no target | Treat post-deploy tests 2 and 4a as a hard gate before distributing anything |
| SD-20 | Knowledge admins (`knowledge_admin_principal_arns`) can read the bucket, use the KB key through S3, and pull the images from anywhere | Accepted (operational need) | Terraform refresh, CI image pushes and curated-overlay promotion run outside the VPC. Globs must be narrow (validation) | Keep the list short and audited; run admin tasks from inside the VPC where possible |
| SD-21 | The runtime role uses `BedrockAgentCoreFullAccess` | Accepted (dev) | Keeps the kit easy to stand up. The managed policy grants `bedrock-agentcore:*`; `s3:GetObject`, `GetObjectVersion`, `ListBucket` and `ListBucketVersions` on every bucket in the account (`s3:ResourceAccount` condition); `s3:CreateBucket`, `PutBucketPolicy` and `PutObject` on `bedrock-agentcore-runtime-*`; Secrets Manager create, put, get and delete; and `iam:PassRole` on `*BedrockAgentCore*` roles ([BedrockAgentCoreFullAccess](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/BedrockAgentCoreFullAccess.html)). Both modes add the `DenyBoundarySelfModification` explicit Deny (private mode only before 3.4.0; it now also lists `SynchronizeGatewayTargets` and the gateway, target, runtime, endpoint and memory deletes); `extra_interface_endpoint_services` rejects `bedrock-agentcore-control`; reused endpoint SGs must not front it. In private mode the S3 gateway endpoint policy limits runtime S3 reach to the KB bucket and the ECR layer bucket, and no Secrets Manager endpoint exists. In public mode the S3 and Secrets Manager grants stay reachable; the AgentCore control-plane changes listed in the Deny no longer are | Replace the managed policy with a custom policy listing only the data-plane actions the runtime calls |
| SD-22 | Public mode: the KB key grants no IAM-delegated use, so curators other than the runtime role, and the terraform runner's first `overlay_seed` create, need an operator-added key-policy grant | Pre-existing, flagged, not changed | Public mode stays exact. The runner case is UNVERIFIED until post-deploy test 11 | Use private mode (`KnowledgeAdminS3Use`), or add a key-policy grant |
| SD-23 | Runtimes can reach the VPC Resolver for public DNS names (low-bandwidth exfiltration channel) | Accepted (not built) | Security groups cannot block Resolver DNS; the kit's code makes no such lookups | Associate a Route 53 Resolver DNS Firewall rule group that allows only `*.amazonaws.com` |
| SD-24 | Public mode (or `trace_tool_content = true`): tool-call arguments, and possibly results, appear in X-Ray/CloudWatch spans | Accepted (demo) | The ADOT `aws_mcp` instrumentation records `gen_ai.tool.call.arguments` and has a `gen_ai.tool.call.result` code path. Public mode keeps today's env exactly | Use private mode with `trace_tool_content = false`, or set `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=aws_mcp` yourself; restrict trace and Logs read access |
| SD-25 | Private access probe (opt-in test stand-in): the role's VPC ENI actions (`ec2:CreateNetworkInterface`, `DescribeNetworkInterfaces`, `DescribeSubnets`, `DeleteNetworkInterface`, `AssignPrivateIpAddresses`, `UnassignPrivateIpAddresses`) and X-Ray actions use `Resource "*"`; anyone with `lambda:InvokeFunction` on it can invoke it | Accepted (test only, default off) | Lambda requires the ENI actions for a VPC function and they are not resource-scopable; X-Ray actions are not resource-scopable. `ProbeLogs` is scoped to the probe log group with `ArnEquals lambda:SourceFunctionArn` (documented by AWS; not yet observed by this kit on Lambda's own log delivery, see the DEPLOY.md no-logs check). A permission boundary repeats the same statements. Network reach is the client endpoint SG only. An invoker needs their own valid token to get anything back; the token is never stored, logged or returned. Since 3.2.2 the probe has an encrypted dead-letter queue (its role may send only to it), a CMK for its environment variables and log group, and 365-day logs; only CKV_AWS_272 stays skipped (SD-9) | Disable the probe after testing |
| SD-26 | Route 53 Resolver inbound endpoint (opt-in): answers any DNS name the VPC resolver resolves, for the approved CIDRs | Accepted (opt-in, default off) | Needed for Direct Connect / VPN workstations to resolve the endpoints' private DNS. DNS Firewall is recommended and not built. The endpoint's security group cannot be changed after creation (an SG change replaces the endpoint and its IPs). Tracked connections can cap it at about 1500 queries per second per IP; the kit keeps the tracked shape rather than open all-traffic rules | Associate a Route 53 Resolver DNS Firewall rule group; scale by adding IPs |
| SD-27 | Shared platform network: one network boundary for several companions; `aws:SourceVpce` no longer separates them, and the platform endpoint policies are account-scoped, not per companion | Accepted (opt-in, default off) | Separation rests on each companion's JWT and SigV4 authorizers, G-none-shared (no `auth_mode = "none"` on a reused or shared data-plane endpoint), the Memory `DenyOtherPrincipalsOnSharedEndpoint` statement, the probe's restriction to `existing_client_endpoint_security_group_ids`, and per-companion KMS keys. Residual: another companion's runtime role in the same account can list, but not decrypt, this companion's KB object keys through the shared S3 endpoint | Set `kb_bucket_arn_patterns` to exact bucket ARNs, or give the companion a dedicated network (Path B) |
| SD-28 | Provider lock files are not committed (`.terraform.lock.hcl` is gitignored): provider selection is bounded within v6 by the `versions.tf` constraints, but provider checksums are not pinned in the repository, and two `terraform init` runs can pick different 6.x releases | Accepted (user decision, 3.2.2) | A starter kit is forked and initialised per engagement; committed lock files went stale and pinned one platform's hashes. Each `terraform init` still writes a local lock that pins the checksums for that working copy | Commit `.terraform.lock.hcl` in your fork (`terraform providers lock -platform=...` for each platform you use) once you settle on a provider version |

> **Scanner dispositions.** Every suppression is inline on the resource or line it covers
> (`# checkov:skip=...`, `# nosec`, `# pragma: allowlist secret`, `# kics-scan ignore-line`) and traces
> to a row above or to [`security/scan-results.md`](security/scan-results.md), which lists each one with its
> justification and the scanner results. There is no repository-level skip list. A fork hardening for
> production removes the suppression and implements the control.

## Reporting a vulnerability

If you discover a potential security issue in this project, notify AWS Security through the
[AWS vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/). Do
not open a public GitHub issue. See [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Related resources

- [`GOVERNANCE.md`](GOVERNANCE.md) — the always-on gate and detectors.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — trust boundaries and component roles.
- [`../PRINCIPLES.md`](../PRINCIPLES.md) — the 13-tenet constitution.
- [`../PROD-READINESS.md`](../PROD-READINESS.md) — readiness gates.
