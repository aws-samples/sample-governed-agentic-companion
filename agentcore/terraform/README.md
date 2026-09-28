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
| **Knowledge base** | Private, CMK-encrypted, versioned S3 bucket (self-learning store under `kb/` + Tier-B overlay `kb/curated/overlay.json`) with HTTPS-only policy, access logging, EventBridge, and lifecycle |
| **Memory** *(opt-in)* | `enable_memory=true` creates an `aws_bedrockagentcore_memory` + a `SUMMARIZATION` strategy as **ready infrastructure** — the kit's example engine has no memory seam, so it is OFF by default; a fork wires its engine to `GAC_MEMORY_ID` |
| **Guardrail** *(opt-in)* | `create_bedrock_guardrail=true` creates a starter `aws_bedrock_guardrail` (PROMPT_ATTACK + AWS-key/PEM/JWT regex denies + PII), **defense in depth** with `agents/data_protection.py` |
| **Identity** | `auth_mode` = **cognito** (Terraform creates the pool + clients, `AdvancedSecurityMode ENFORCED`), **entraid** (existing tenant), or **none** (unauthenticated — internal test only, interlocked) |
| **Gateway** *(opt-in)* | `deploy_gateway=true` creates a native MCP gateway → mcp_server target → the MCP runtime, over an outbound OAuth M2M credential, with an optional identity interceptor (ADR-0001) |
| **Front-door config** | `generate_frontdoor_config=true` writes populated Kiro/Claude MCP config + a front-door env template into `generated/` |

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
  OAuth path, and an M2M client_credentials resource-server/client for the gateway's outbound
  hop.
- **`entraid`** — bring an existing Microsoft Entra ID tenant. Supply `entraid_discovery_url` +
  `entraid_frontdoor_client_ids`. Terraform creates **no** IdP resources; the inbound JWT
  authorizer just trusts Entra-issued tokens. The IdP-agnostic `CustomOauth2` provider means
  swapping IdP is a discovery-URL change, not a code change.
- **`none`** — **unauthenticated, internal test only.** Requires `allow_unauthenticated=true`
  and is refused if `deploy_gateway=true` (guards in `guards.tf`).

The GovernanceGate (Tenets 1/3/6) is identical in every mode — `auth_mode` controls only who
may reach the front door, never what the engine may do.

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
versions.tf            required_versions + providers (aws, null, local, random)
variables.tf           all inputs (region, model, auth_mode, gateway, guardrail, cognito hardening)
locals.tf              derived values + the container env contract + auth + guardrail resolution
guards.tf              deploy-time preconditions (unauth interlock, entraid reqs, gateway+none, guardrail)
ecr.tf                 two ECR repos (CMK) + lifecycle + repo policy
iam.tf                 shared runtime execution role (S3 KB grant baked in)
s3_kb.tf               private/CMK/versioned KB bucket + access logging + overlay seed
memory.tf              opt-in AgentCore Memory (session Events + task-summary strategy)
guardrail.tf           opt-in starter Bedrock Guardrail (defense in depth)
cognito.tf             Cognito IdP (auth_mode=cognito only; AdvancedSecurityMode + optional MFA)
runtimes.tf            the two aws_bedrockagentcore_agent_runtime resources
gateway.tf             opt-in native AgentCore gateway + M2M provider + target
interceptor.tf         opt-in identity interceptor Lambda (ADR-0001 Phase 2)
secrets.tf             opt-in Terraform-managed M2M secret (Secrets Manager + CMK)
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
identifier. Only `*.tf` and `*.example` files are tracked. For a shared team, add a
`backend.tf` with an S3 backend + a DynamoDB lock table (state may contain resource
attributes — treat it as sensitive).

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
