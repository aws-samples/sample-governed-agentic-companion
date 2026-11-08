<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Governed Agentic Companion — AgentCore Terraform

Infrastructure-as-Code to deploy the Governed Agentic Companion onto **Amazon Bedrock
AgentCore**: it packages the engine into two container runtimes, provisions the identity,
knowledge-base, and IAM infrastructure, and generates the MCP front-door config builders use
to start querying the solution.

This is a **starter kit** — fork it per engagement, keep the governance, and adjust the
inputs. It is designed to be **safe by construction**: no agent deploys, secrets never land in
the repo, and the security defaults follow published AWS AgentCore guidance.

> **Governance (Tenet 1 — human-owned deployment).** Terraform prepares and applies
> infrastructure; **a human runs `terraform apply`** with their own credentials. No agent in
> this repo runs Terraform, builds images, or mutates an environment. The build/push helper
> under `scripts/` is invoked only by a human-run `apply`.

> **⚠️ Reference implementation — not for production as-is.** Review the IAM, identity, and
> network settings against your account's threat model and landing-zone standards before use.
> Every non-hardened default is documented as accepted security debt in
> [`../../PROD-READINESS.md`](../../PROD-READINESS.md) with a migration path.

---

## What it creates

| Concern | Resource(s) |
|---|---|
| **Container registry** | Two ECR repos (`<prefix>-orchestrator`, `<prefix>-mcp-runtime`), KMS-encrypted, scan-on-push, immutable tags, keep-last-5 lifecycle |
| **Image build/push** | `null_resource` + `scripts/build_and_push.sh` — builds both images from the **repo root** context and pushes to ECR (skippable with `auto_build_push_images=false`) |
| **Two runtimes** | `aws_bedrockagentcore_agent_runtime` × 2 — `orchestrator` (HTTP `/invocations`) and `mcp_runtime` (stateless MCP `/mcp`, the gateway target) |
| **Execution role** | One **shared** role for both runtimes, with ECR pull, CloudWatch Logs/metrics, X-Ray, `bedrock:InvokeModel*` (region-scoped), workload tokens, and **S3 KB read+write baked in** |
| **Knowledge base** | Private, CMK-encrypted, versioned S3 bucket (Tier-B overlay `kb/curated/overlay.json`; the rest of `kb/` is reserved for a fork's own knowledge tier) with HTTPS-only policy, access logging, EventBridge, and lifecycle |
| **Memory** *(opt-in)* | `enable_memory=true` creates an `aws_bedrockagentcore_memory` + a `SUMMARIZATION` strategy as **ready infrastructure** — the kit's example engine has no memory seam, so it is OFF by default; a fork wires its engine to `GAC_MEMORY_ID` |
| **Guardrail** *(opt-in)* | `create_bedrock_guardrail=true` creates a starter `aws_bedrock_guardrail` (PROMPT_ATTACK + AWS-key/PEM/JWT regex denies + PII), **defense in depth** with `agents/data_protection.py` |
| **Identity** | `auth_mode` = **cognito** (Terraform creates the pool + clients, `AdvancedSecurityMode ENFORCED`), **entraid** (existing tenant), or **none** (unauthenticated — internal test only, interlocked) |
| **Gateway** *(opt-in)* | `deploy_gateway=true` creates a native MCP gateway → mcp_server target → the MCP runtime, over SigV4 with the gateway role (private mode) or an outbound OAuth M2M credential (public mode), with an optional identity interceptor (ADR-0001) |
| **Front-door config** | `generate_frontdoor_config=true` writes populated Kiro/Claude MCP config + a front-door env template into `generated/` (in private mode only after every private-mode policy is attached) |
| **VPC endpoints** *(private mode, default)* | Interface endpoints with private DNS: `ecr.api`, `ecr.dkr`, `logs`, `bedrock-agentcore` (data plane), `bedrock-agentcore.gateway` (with the gateway), `bedrock-runtime`, `xray`, `comprehend` (with `pii_backend=comprehend`), plus `extra_interface_endpoint_services`; an S3 gateway endpoint. Tightened endpoint policies on the gateway, data-plane, Bedrock, Comprehend and S3 endpoints |
| **Security groups** *(private mode)* | `<prefix>-runtime` (no ingress; 443 egress to the endpoint SGs and the S3 prefix list), `<prefix>-vpce-client` (443 from the runtime SG + `private_client_cidrs`), `<prefix>-vpce-runtime` (443 from the runtime SG only). No `/0` rule is possible |
| **Resource policies** *(private mode)* | `aws_bedrockagentcore_resource_policy` on the gateway and orchestrator (deny unless `aws:SourceVpce`), on the MCP runtime (gateway role only) and on Memory; private-only statements in the KB bucket policy, the KB key policy and both ECR repository policies; `DenyBoundarySelfModification` on the runtime role |
| **Sandbox VPC** *(private mode, `create_vpc=true`)* | A minimal VPC: 2 private subnets in AgentCore-supported AZs, one route table (local route + S3 endpoint), the default SG stripped, VPC Flow Logs (ALL traffic, 365-day log group with its own CMK). No internet gateway, no NAT, no Elastic IP |

### The runtime env contract — only what the engine consumes

The runtime environment is deliberately scoped to what the kit's example engine actually reads
(verified in `agents/kb_overlay.py`): `GAC_KB_OVERLAY`, `GAC_KB_BUCKET`, `GAC_KB_OVERLAY_KEY`,
`GAC_AWS_REGION`, the data-protection vars (`GAC_DATA_PROTECTION_MODE`, `GAC_PII_BACKEND`,
`GAC_PROPRIETARY_TERMS`, `GAC_GUARDRAIL_*`, `GAC_PII_REGION`), and `GAC_ENGINE_ROOT`. Memory
env (`GAC_SESSION_STORE`/`GAC_MEMORY_ID`/`GAC_MEMORY_SHARING`) is added **only** when
`enable_memory=true` — a fork that adds a session seam consumes it. We do not inject env the
engine never reads.

---

## Prerequisites

- Terraform **≥ 1.6**, the **AWS CLI**, and **Docker** on PATH.
- AWS credentials for a human operator (`AWS_PROFILE` / `AWS_REGION`) with permission to create
  ECR, IAM, S3, KMS, Cognito, Secrets Manager, Lambda, and Bedrock AgentCore resources.
- A region where **AgentCore Runtime is GA** and your Bedrock **model/inference profile** is
  available (default `us-east-1`).
- The `hashicorp/aws` provider **v6.66+** (native AgentCore gateway resources) when
  `deploy_gateway=true`.
- Private mode (default): VPC inputs or `create_vpc=true`, `private_client_cidrs`,
  `knowledge_admin_principal_arns`, and the deployer permissions listed in
  [`../../docs/DEPLOY.md`](../../docs/DEPLOY.md) "Private mode prerequisites".

---

## Quick start

```bash
cd agentcore/terraform
cp terraform.tfvars.example terraform.tfvars      # fill in — this file is gitignored
terraform init
terraform plan -out tf.plan                        # review — a human decides
terraform apply tf.plan                            # a human applies (Tenet 1)
```

Then wire a builder's IDE from the generated config:

```bash
cat generated/README-frontdoor.md                  # what to copy where
# gateway mode:   copy generated/kiro-mcp.json  -> .kiro/settings/mcp.json
# stdio-bridge:   copy generated/gac-frontdoor.env -> ~/.gac-frontdoor.env (fill secrets)
```

---

## Auth modes

Set `auth_mode` in `terraform.tfvars`:

- **`cognito`** *(default, reference deployment)* — Terraform creates a Cognito user pool
  (`AdvancedSecurityMode` configurable, ENFORCED by default), a front-door user client
  (ADMIN_USER_PASSWORD_AUTH for the local bridge), an optional PKCE public client for the IDE
  OAuth path, and the `gac-engine/invoke` resource server. The M2M client_credentials client for
  the gateway's outbound hop is created in public mode only (private mode uses SigV4).
- **`entraid`** — bring an existing Microsoft Entra ID tenant. Supply `entraid_discovery_url` +
  `entraid_frontdoor_client_ids`. Terraform creates **no** IdP resources; the inbound JWT
  authorizer just trusts Entra-issued tokens. The IdP-agnostic `CustomOauth2` provider means
  swapping IdP is a discovery-URL change, not a code change.
- **`none`** — **unauthenticated, internal test only.** Requires `allow_unauthenticated=true`
  and is refused if `deploy_gateway=true` (guards in `guards.tf`).

The GovernanceGate (Tenets 1/3/6) is identical in every mode — `auth_mode` controls only who
may reach the front door, never what the engine may do.

---

## Network access

`network_access = "private"` is the **default**: both runtimes run in `VPC` network mode in your
private subnets, and the gateway, the runtimes, the KB bucket, Memory and the ECR images accept
data requests only through your VPC endpoints (AWS PrivateLink) or a listed knowledge admin. A
valid JWT presented from the internet is refused by an explicit resource-policy `Deny`.
`network_access = "public"` is a **demo opt-in** that keeps the previous behaviour (internet
reachable, JWT only) and requires `acknowledge_public_endpoint = true`; `auth_mode = "none"` is
refused in public mode. See [`../../docs/SECURITY.md`](../../docs/SECURITY.md) "Network posture",
[`../../docs/DEPLOY.md`](../../docs/DEPLOY.md) (prerequisites and post-deploy tests) and
[`../../docs/FRONT-DOORS.md`](../../docs/FRONT-DOORS.md) "Private mode access (default)".

| Variable | Default | Purpose |
|---|---|---|
| `network_access` | `"private"` | `private` or `public` (demo) |
| `acknowledge_public_endpoint` | `false` | Required `true` for public mode |
| `create_vpc` | `false` | Sandbox VPC for a one-apply trial (no IGW/NAT) |
| `vpc_cidr` / `private_subnet_az_ids` | `"10.80.0.0/16"` / `[]` | Sandbox VPC CIDR (/16 to /24) and optional AZ IDs |
| `vpc_id` / `private_subnet_ids` / `private_route_table_ids` | empty | Your VPC (DNS hostnames + resolution on), >= 2 subnets in supported AZs, the route tables that serve them |
| `private_client_cidrs` | `[]` | IDE/developer IPv4 CIDRs (/8 or longer, never `/0`); required unless `create_vpc` |
| `bedrock_runtime_client_access` | `false` | Also let client CIDRs reach `bedrock-runtime` |
| `existing_s3_gateway_endpoint_id` | `""` | Reuse an S3 gateway endpoint |
| `existing_interface_endpoint_ids` / `existing_endpoint_security_group_ids` | `{}` / `[]` | Reuse interface endpoints (and their SGs) that already own the private DNS names |
| `additional_dataplane_vpce_ids` / `additional_gateway_vpce_ids` | `[]` | Extra approved endpoint IDs (for example a hub VPC) |
| `enable_bedrock_runtime_endpoint` / `enable_xray_endpoint` | `true` / `true` | Create those endpoints |
| `extra_interface_endpoint_services` | `[]` | Extra endpoints by short name (for example `sts`); `bedrock-agentcore-control` is rejected |
| `ecr_pull_restriction` | `true` | Deny ECR image pulls outside the ecr endpoints and exempt principals |
| `dataplane_endpoint_extra_actions` | `[]` | Extra `bedrock-agentcore:` actions through the data-plane endpoint |
| `knowledge_admin_principal_arns` | `[]` | **Required in private mode**: terraform runners, CI roles, curators (narrow globs only) |
| `trace_tool_content` | `false` | `false` disables the ADOT `aws_mcp` instrumentation so tool content stays out of traces |
| `manage_existing_endpoint_ingress` | `false` | Add TCP 443 ingress from the runtime SG on every `existing_endpoint_security_group_ids` entry (shared-network consumers) |
| `existing_client_endpoint_security_group_ids` | `[]` | The client-endpoint subset of `existing_endpoint_security_group_ids` (probe only) |
| `enable_resolver_inbound_endpoint` / `resolver_inbound_client_cidrs` | `false` / `[]` | Optional Route 53 Resolver inbound endpoint for DX/VPN DNS (TCP/UDP 53 from validated CIDRs) |
| `enable_private_access_probe` / `private_access_probe_reserved_concurrency` | `false` / `1` | Optional test stand-in for a DX/VPN workstation; disable after testing |
| `engine_resource_server_identifier` | `"gac-engine"` | Cognito resource-server identifier; unique per companion in a shared pool |

Private-mode outputs: `network_access`, `vpc_id`, `private_subnet_ids`, the three SG ids,
`vpc_endpoint_ids`, `gateway_vpce_id` / `gateway_vpce_dns_names`, `agentcore_dataplane_vpce_id` /
`agentcore_dataplane_vpce_dns_names`, `s3_gateway_endpoint_id`, the two
`*_require_service_s3_endpoint` values (expected `false`), `kb_kms_key_arn` and
`private_access_test_command` (no token is interpolated), `resolver_inbound_ip_addresses`,
`private_access_probe_function_name` and `private_access_probe_invoke_command` (the token is read
from `GAC_GATEWAY_TOKEN` by `jq`, never interpolated). `cognito_m2m_client_id` is empty in private
mode.

### Modules, platform stack and adoption paths

The network is built by [`modules/private-network`](modules/private-network/README.md), called as
`module.private_network` in private mode (`moved.tf` keeps resources created before 3.2.0 at their
new addresses without replacement). The optional shared layer is a separate root,
[`platform/`](platform/README.md): [`modules/backend-bootstrap`](modules/backend-bootstrap/README.md)
(remote state), [`modules/shared-identity`](modules/shared-identity/README.md) (one Cognito pool)
and, optionally, one shared `modules/private-network`. Path A: apply the platform first, then each
companion consumes it (`companion_consumer_inputs` output). Path B (default): a standalone
companion that owns its own pool and network. Details: [`../../docs/DEPLOY.md`](../../docs/DEPLOY.md)
"Adoption paths".

### Plan-only tests

`terraform test` (Terraform 1.7 or later) runs the combination matrix in `tests/matrix.tftest.hcl`
(and `platform/tests/platform.tftest.hcl` from `platform/`) with mocked providers: no AWS
credentials, no Docker, no files written. `terraform test` auto-loads a local `terraform.tfvars`,
which overrides the test variables and fails a mocked run, and a plan of this directory rewrites
`.identity_interceptor.zip`. Run `terraform init -backend=false && terraform test` in a copy of the
repository without `terraform.tfvars`, `backend.tf`, `.terraform/` and `.terraform.lock.hcl`, for
example:

```bash
rsync -a --exclude .git --exclude .venv --exclude terraform.tfvars --exclude backend.tf \
  --exclude '*.tfstate*' --exclude .terraform --exclude .terraform.lock.hcl --exclude generated \
  --exclude '*.zip' ./ /tmp/gac-tftest/
cd /tmp/gac-tftest/agentcore/terraform && terraform init -backend=false && terraform test
cd platform && terraform init -backend=false && terraform test
```


`terraform validate` checks both modes' types and references in one run; it does not evaluate
per-mode values. The static tests in `tests/test_terraform_private_mode_static.py` assert the
structure, and only the post-deploy tests in `docs/DEPLOY.md` prove the behaviour.

---

## Data protection & guardrail (defense in depth — Tenet 6)

Two independent layers protect every write and model interaction:

1. **Deterministic guard** (`agents/data_protection.py`, always on) — calibrated to PRESERVE
   operational config (ports, topics, endpoints) and block only real secrets + personal data.
   Mode via `data_protection_mode` (balanced | strict | redact).
2. **Managed guardrail** (opt-in) — `create_bedrock_guardrail=true` creates a starter Bedrock
   Guardrail (PROMPT_ATTACK filter; regex denies for AWS access-key ids / PEM private-key
   headers / JWT shapes; PII anonymize/block). When `pii_backend="guardrails"`, the runtime
   applies it on each write via `bedrock:ApplyGuardrail`.

IAM grants (`comprehend:DetectPiiEntities` or `bedrock:ApplyGuardrail`) are added ONLY when the
corresponding backend is enabled; `pii_backend="guardrails"` without a guardrail (created or
supplied) fails at plan time (`guards.tf`).

---

## Files

```
versions.tf            required_versions + pinned providers (aws ~> 6.66, archive, null, local, random)
variables.tf           all inputs (region, model, auth_mode, gateway, guardrail, cognito hardening)
locals.tf              derived values + the container env contract + auth + guardrail resolution
guards.tf              deploy-time preconditions (unauth interlock, entraid reqs, gateway+none, guardrail,
                       network_guards for private/public mode) + the runner_is_knowledge_admin check
network.tf             private mode: module.private_network call, existing-VPC lookups, runtime SG + rules
moved.tf               moved blocks for the network resources extracted into modules/private-network (3.2.0)
probe.tf               opt-in private access probe (test stand-in Lambda, role, SG, CMK, DLQ, log group)
modules/               backend-bootstrap, shared-identity, private-network (reusable modules)
platform/              optional shared layer root: state backend, shared identity, shared network
tests/                 plan-only terraform test matrix (mocked providers)
resource_policies.tf   private mode: endpoint policies + AgentCore resource policies (gateway, runtimes, Memory)
ecr.tf                 two ECR repos (CMK) + lifecycle + repo policy
iam.tf                 shared runtime execution role (S3 KB grant baked in)
s3_kb.tf               private/CMK/versioned KB bucket + access logging + overlay seed
memory.tf              opt-in AgentCore Memory (session Events + task-summary strategy)
guardrail.tf           opt-in starter Bedrock Guardrail (defense in depth)
cognito.tf             Cognito IdP (auth_mode=cognito only; AdvancedSecurityMode + optional MFA)
runtimes.tf            the two aws_bedrockagentcore_agent_runtime resources
gateway.tf             opt-in native AgentCore gateway + target (+ M2M provider in public mode);
                       private mode waits 30 s for the gateway role's grants before the target
interceptor.tf         opt-in identity interceptor Lambda (ADR-0001 Phase 2)
secrets.tf             creates no secret: resolves a bring-your-own gateway_m2m_secret_arn (rare)
build.tf               build/push null_resources (repo-root context)
access_analyzer.tf     opt-in account IAM Access Analyzer
frontdoor_config.tf    generates Kiro/Claude MCP config + front-door env into generated/
outputs.tf             runtime ARNs, image URIs, KB bucket, auth ids, gateway URL (no secrets)
scripts/build_and_push.sh   docker build+push helper (arm64)
terraform.tfvars.example    copy to terraform.tfvars (gitignored)
```

---

## What is NOT committed

`.gitignore` excludes state, `terraform.tfvars`, plan files, the interceptor zip, and
`generated/` — anything that could hold an account id, ARN, endpoint, secret, or customer
identifier, plus `.terraform.lock.hcl` (local; `versions.tf` bounds each provider within its major version, without
checksum pinning, SD-28). Only `*.tf`,
`*.example` and test files are tracked. For a shared team, copy `backend.tf.example` to `backend.tf`:
an S3 backend with native locking (`use_lockfile`), bucket from `modules/backend-bootstrap` (state
may contain resource attributes; treat it as sensitive).

---

## Scaling up

This stack is single-account. For a production, multi-account topology (management +
shared-tooling + workload accounts, cross-account observability via CloudWatch OAM, CI/CD
promotion with evaluation gates), see the AWS
[Guidance for Enterprise Agentic AI Platform on AWS](https://github.com/aws-solutions-library-samples/guidance-for-enterprise-agentic-ai-platform-on-aws)
— the security and auth patterns here are aligned with it, so the step up is incremental.

## Attribution

The Cognito hardening (AdvancedSecurityMode, token TTLs), the starter Bedrock Guardrail
(PROMPT_ATTACK + credential-shape regex + PII), and the shared least-privilege execution-role
shape are modeled on two AWS-published MIT-0 samples: the *Guidance for Enterprise Agentic AI
Platform on AWS* and the *Sample Lambda Test Event Generator*. Content was adapted and
genericized for this starter kit.
