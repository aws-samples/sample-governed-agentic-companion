<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Deploy & implementation — Amazon Bedrock AgentCore (human-run, Tenet 1)

This is the implementation guide for putting the companion on Amazon Bedrock AgentCore with the
Terraform stack under [`../agentcore/terraform/`](../agentcore/terraform/). The companion runs
locally with no AWS; this page is how you provision the cloud footprint so builders reach it
through the AgentCore Gateway.

> **A human runs every AWS mutation (Tenet 1).** No agent in this repo runs Terraform, builds
> images, or invokes against AWS. Terraform *prepares* the plan; a human runs `terraform apply`.

> **⚠️ Sample code — not for production as-is.** The IAM, identity, network, and credential
> settings are illustrative. Review them against your account and threat model, and see the
> accepted-security-debt table in [`SECURITY.md`](SECURITY.md) before production use.

## Prerequisites

- **Terraform ≥ 1.6**, the **AWS CLI**, and **Docker** (with `buildx`) on PATH.
- AWS credentials for a human operator (`AWS_PROFILE` / `AWS_REGION`) able to create ECR, IAM, S3,
  KMS, Cognito, Secrets Manager, Lambda, and Bedrock AgentCore resources.
- A Region where **AgentCore Runtime is GA** and your Bedrock **model / inference profile** is
  available. Confirm the exact inference-profile id for your Region first — a `us.` profile only
  resolves in US Regions; use the `au.`/`apac.` profile in Asia-Pacific, etc.:
  ```bash
  aws bedrock list-inference-profiles --region <REGION> \
    --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].[inferenceProfileId,status]" --output text
  ```
- Amazon Bedrock **model access enabled** for your chosen model in that Region.

## What gets deployed

The stack provisions two AgentCore runtimes (HTTP `/invocations` + stateless MCP `/mcp`), two ECR
repos, a shared least-privilege execution role, a private CMK-encrypted S3 knowledge base, and —
as independent **opt-ins** — Cognito identity, an AgentCore Gateway + M2M credential + target, an
identity interceptor Lambda, AgentCore Memory, and a Bedrock Guardrail. See
[`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) for the full resource
inventory and every input, and [`COST.md`](COST.md) for what each opt-in costs.

## Configure

```bash
cd agentcore/terraform
cp terraform.tfvars.example terraform.tfvars      # gitignored — fill in for your engagement
```

Key inputs (all documented in `variables.tf`):

| Input | Purpose |
|---|---|
| `aws_region` / `name_prefix` | Where to deploy; resource-name prefix |
| `bedrock_model_id` | Inference-profile id valid in `aws_region` |
| `auth_mode` | `cognito` (create IdP) / `entraid` (existing tenant) / `none` (test only) |
| `deploy_gateway` | Create the gateway front door (requires cognito/entraid) |
| `enable_identity_interceptor` | Verified per-builder identity propagation (ADR-0001) |
| `enable_memory` | Provision AgentCore Memory (opt-in ready infrastructure) |
| `create_bedrock_guardrail` / `pii_backend` | Managed guardrail defense in depth |
| `cognito_mfa_mode` / `cognito_advanced_security_mode` | Cognito hardening |

## Deploy (the sequence a human runs)

```bash
export AWS_PROFILE=<your-profile>
terraform init
terraform plan -out tf.plan      # review the plan — you decide
terraform apply tf.plan          # builds+pushes both images, then creates the stack
```

Notes:
- `apply` runs the build/push helper for both arm64 images before creating the runtimes.
- ECR tags are **immutable** by default — bump `image_tag` for each redeploy (e.g. `v0-1-0` →
  `v0-1-1`); re-pushing the same tag is rejected by design.

## Verify (end-to-end)

1. **Runtimes READY:**
   ```bash
   for r in $(terraform output -raw orchestrator_runtime_arn | sed 's#.*/##') \
            $(terraform output -raw mcp_runtime_arn | sed 's#.*/##'); do
     aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$r" \
       --region <REGION> --query '[agentRuntimeName,status]' --output text
   done
   ```
2. **MCP runtime serving (not crash-looping):** check
   `/aws/bedrock-agentcore/runtimes/<mcp-runtime>-DEFAULT` for `POST /mcp ... 200 OK`.
3. **Gateway target created:** a `FAILED` target means the MCP runtime errored on boot — read its
   CloudWatch logs (a missing/incompatible dependency is the usual cause).
4. **Governed invocation:** connect an IDE (see [`FRONT-DOORS.md`](FRONT-DOORS.md)) and ask a
   question that hits a known KB marker; confirm a **gated** answer with the governance footer.
5. **Local integrity:** `./run.sh status` reports the governance integrity check passing.

## Teardown

```bash
cd agentcore/terraform
terraform destroy      # a human runs this
```

Buckets/repos use `force_destroy` and the M2M secret uses a 0-day recovery window (dev-friendly);
for production set those to retain (SD-1 in [`SECURITY.md`](SECURITY.md)). KMS keys enter a
pending-deletion window rather than deleting immediately.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pii_backend="guardrails"` fails at plan | Set `create_bedrock_guardrail=true` or supply `guardrail_id` |
| Runtime `bedrock:InvokeModel` denied | The inference-profile id is not valid in `aws_region`, or model access is off |
| Gateway target `FAILED` | MCP runtime crashed on boot — check its CloudWatch logs |
| `image tag ... immutable` on redeploy | Bump `image_tag` (immutable tags are intentional) |

## Related resources

- [`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) — full inputs & resources.
- [`FRONT-DOORS.md`](FRONT-DOORS.md) — connect Kiro / Claude Code after deploy.
- [`SECURITY.md`](SECURITY.md) — security model + accepted security debt.
- [`COST.md`](COST.md) — what the deployed footprint costs.
