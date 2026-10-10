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
| Gateway → runtime | OAuth2 M2M client-credentials (AgentCore Identity) | The gateway authenticates *as itself* to the runtime; no user identity is involved |

- The inbound authorizer is IdP-agnostic (`CUSTOM_JWT` + discovery URL + allowed client ids), so
  swapping Cognito ↔ Entra ID is a configuration change, not a code change.
- Cognito hardening (matches the AWS AgentCore reference samples): `AdvancedSecurityMode ENFORCED`
  (adaptive auth + compromised-credential detection), a strong password policy, admin-only user
  creation, short-lived access/id tokens (60 min) with 30-day refresh, and token revocation.
  MFA is configurable (`cognito_mfa_mode`; set `ON` for production).
- **Verified-identity propagation (ADR-0001).** Per-builder memory is fail-closed by default:
  through the gateway the builder's JWT is validated but not forwarded, so an opt-in REQUEST
  interceptor Lambda derives the verified subject and injects it (`X-Gac-Actor-Sub`). A direct
  caller cannot forge it — the runtime trusts the injected actor only on the gateway path.

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

## Data protection

- **Encryption at rest:** the S3 knowledge base and the M2M secret use **customer-managed KMS
  keys** (rotation enabled); ECR images are KMS-encrypted; the access-log bucket uses SSE-S3
  (proportionate for operational logs). All are private with public access blocked.
- **Encryption in transit:** the KB bucket policy **denies any non-TLS request**
  (`aws:SecureTransport=false`); all AWS API calls use TLS.
- **The unified data-protection guard** (`agents/data_protection.py`, Tenet 6) runs on every write
  to a knowledge or memory store: it **rejects secrets**, **redacts PII and proprietary terms**,
  and **preserves** operational config (ports, endpoints). Mode is configurable
  (`balanced` | `strict` | `redact`).
- **Optional Bedrock Guardrail** (defense in depth): a starter guardrail blocks prompt-attack
  input and denies credential-shaped output (AWS access-key ids, PEM private keys, JWTs) plus PII.
- **No secrets in the repo:** credentials live in gitignored env files and Secrets Manager; the
  knowledge base holds no credentials or PII; a CI leak scan enforces this.

## The always-on governance boundary

Every response — from any path — passes one shared gate (`agents/governance_gate.py`) with no off
switch. It hard-blocks a response that claims a deployment/mutation happened (T1), claims a write
to a read-only system of record (T3), leaks secret material (T6), or is an ungrounded LLM guess
below the grounding bar (T4). Enforcement is layered: the *action layer* (read-only tools +
permission boundaries) is what guarantees no mutation; the *text layer* (the gate's detectors) is
the honesty/audit signal. See [`GOVERNANCE.md`](GOVERNANCE.md) for the detector table.

## Network posture

- Runtimes run in the AgentCore-managed network (`PUBLIC` mode in this kit); exposure is controlled
  at the gateway (JWT authorizer) + the platform, not by the container bind.
- The interceptor Lambda makes no data-plane calls, so it runs without a VPC by design (documented
  rationale, not an oversight). A fork with private-network requirements can enable VPC mode.

## Security scanning

The repo is prepared for comprehensive scanning (see [`../PROD-READINESS.md`](../PROD-READINESS.md)
and `.gitlab-ci.yml`): offline unit tests, YAML validation, and a customer-data/secret leak scan
run in CI; `.gitleaks.toml` and a pre-commit hook catch secrets before commit. Terraform is written
to pass IaC scanners (checkov/kics) — KMS key policies use discrete actions (never `kms:*`), S3 is
TLS-only + access-logged + lifecycle-managed, ECR uses immutable tags + scan-on-push, and the
documented `"*"` grants carry inline justification.

## Accepted security debt

These are evaluated, intentional choices for a **starter kit** deployed to a dev/sandbox account.
Each lists the rationale and the change to make for production. This table is the honesty contract —
do not remove a row until the item is actually hardened.

| ID | Item | Status | Rationale | Production change |
|---|---|---|---|---|
| SD-1 | `force_destroy` / `force_delete` on S3 + ECR; M2M secret `recovery_window=0` | Accepted (dev) | Lets a dev `terraform destroy` clean up without residue | Set `force_destroy=false`, raise the secret recovery window, so a teardown cannot drop the KB or a secret |
| SD-2 | Cognito MFA defaults `OPTIONAL` (not `ON`) | Accepted (dev) | TOTP MFA is available (a guardrail is defined) but not forced on a dev front door; strong password policy + `AdvancedSecurityMode ENFORCED` compensate | Set `cognito_mfa_mode="ON"` to require MFA |
| SD-3 | Access-log bucket uses SSE-S3, not a CMK | Accepted | Logs are operational metadata, not secrets; avoids extra KMS cost | Use a CMK if your compliance scope requires it |
| SD-4 | CloudWatch Logs use account-default retention | Accepted | Kit does not impose a retention policy | Add `retention_in_days` to the log groups per your policy |
| SD-5 | No hand-managed M2M Secrets Manager secret | Resolved by design | The gateway's outbound M2M hop uses the native AgentCore Identity OAuth2 credential provider (its own managed Token Vault), so the kit creates **no** hand-managed Secrets Manager copy of the credential — avoiding a redundant long-lived secret and its rotation obligation (this also removes the CKV_AWS_304 finding at the root) | A fork needing a readable copy on a non-gateway path supplies `gateway_m2m_secret_arn` and owns that secret's rotation |
| SD-6 | Runtimes use `PUBLIC` network mode | Accepted | Exposure is controlled at the gateway JWT authorizer; simplest reference posture | Enable VPC mode for a private-network deployment |
| SD-7 | Some IAM grants use `"*"` (X-Ray, workload tokens) | Accepted (unavoidable) | These actions do not support resource-level scoping; matches AWS reference IAM | None — scoping is via the role's trust policy; documented inline |
| SD-8 | IAM Access Analyzer is OFF by default | Accepted | It is an account/org baseline a landing zone usually owns; enabling here would collide | Set `enable_access_analyzer=true` only if this stack owns the account baseline |
| SD-9 | Interceptor Lambda not code-signed (CKV_AWS_272) | Accepted | Built from in-repo single-file source pinned by `source_code_hash`; a Signer profile is disproportionate for a starter kit | Add an AWS Signer profile if your policy requires signed Lambda code |
| SD-10 | S3 buckets without cross-region replication (CKV_AWS_144) | Accepted | Disproportionate for a small single-region KB / access-log bucket; both are versioned | Enable CRR for a multi-region DR requirement |
| SD-11 | KB read grant flagged "data exfiltration" (kics) | False positive | `s3:GetObject` is resource-scoped to one bucket + the `kb/` prefix (not `"*"`); it is least-privilege and cannot retrieve arbitrary data | None — reviewed; scoped ignore documented inline in `iam.tf` |

> **Scanner dispositions.** The Terraform stack carries reviewed inline suppressions
> (`# checkov:skip=...`, `# kics-scan ignore-line`) plus a repo-level `agentcore/terraform/.checkov.yaml`,
> each tracing to a row above. They are documented tradeoffs for a starter kit, not blanket
> disables — a fork hardening for production removes the suppression and implements the control.

## Reporting a vulnerability

Do not open a public issue for a security vulnerability. Follow your organization's coordinated
disclosure process. For AWS-service issues, contact aws-security@amazon.com.

## Related resources

- [`GOVERNANCE.md`](GOVERNANCE.md) — the always-on gate and detectors.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — trust boundaries and component roles.
- [`../PRINCIPLES.md`](../PRINCIPLES.md) — the 13-tenet constitution.
- [`../PROD-READINESS.md`](../PROD-READINESS.md) — readiness gates.
