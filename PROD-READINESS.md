<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Prod-readiness gate checklist

**Status: starter kit / reference implementation — NOT production-ready as shipped.** This file
tracks what is verified, what is opt-in, and what a fork must do before calling a deployment
production-ready. Update it as gates close; do not remove a gap until it is actually verified
closed. The `SD-*` references point to the accepted-security-debt table in
[`docs/SECURITY.md`](docs/SECURITY.md).

## What is verified today

- **Deterministic core** runs with no AWS and no LLM; the full test suite passes offline.
- **Governance gate** is always-on with no off switch; the two absolute tenets (T1 human-owned
  deployment, T3 bounded agency) are enforced at the action layer, not by prompt.
- **Governance integrity** (`PRINCIPLES.md`) is SHA-256 baselined and verified by `./run.sh status`
  and by the unit test suite (run in CI on every push).
- **Terraform stack** (`agentcore/terraform/`) `terraform validate`s and has been **deployed and
  verified end-to-end** on Amazon Bedrock AgentCore: both runtimes reach `READY`, the gateway
  target connects, and a live invocation returns a gated, grounded response with the governance
  footer.
- **Private mode (the default) in a sandbox account, 2026-10-31.** From outside the VPC the
  gateway, the orchestrator runtime, the MCP runtime (SigV4) and Memory refused a valid caller by
  resource policy (HTTP 403 / explicit deny). From inside the VPC (the private access probe) the
  endpoints resolved to private IPs and the gateway `tools/call` and the orchestrator invoke
  returned gated answers. The ECR, KB bucket and runtime-role denies were confirmed attached. Record
  and the tests not yet run: [`docs/DEPLOY.md`](docs/DEPLOY.md#sandbox-validation-record-2026-10-31).
- **Static scans:** Checkov, Bandit (every level), pip-audit and detect-secrets report zero findings
  on the tracked files; each inline suppression is listed for review in
  [`docs/security/scan-results.md`](docs/security/scan-results.md).
- **Data-protection guard** (`agents/data_protection.py`) is built and unit-tested (secret-free
  tests); feedback staging calls it, and a fork calls it on each write path it adds.
- **Scan-readiness:** offline unit tests + YAML validation + a customer-data/secret leak scan run in
  CI (`.github/workflows/ci.yml`); `.gitleaks.toml` + a pre-commit hook catch secrets pre-commit; Terraform is
  authored to pass IaC scanners (checkov/kics) — KMS discrete actions, S3 TLS-only + access-logged,
  ECR immutable + scan-on-push.

## Readiness gates (close these for production)

| Gate | Item | State | What a fork must do |
|---|---|:---:|---|
| G1 | Replace example specialists + knowledge with your domain | ⬜ Open | Swap `agents/specialists/` + seed `knowledge/tier_a`/`tier_b`; re-baseline integrity if you edit tenets wording |
| G2 | Teardown-safety of stateful resources | ⬜ Open | Flip `force_destroy`/`force_delete`=false (SD-1) |
| G3 | Identity hardening | ⬜ Open | Set `cognito_mfa_mode="ON"` (SD-2); review the inbound allow-list and token TTLs |
| G4 | Secret rotation | ⬜ Open | Rotate the Cognito M2M client secret held by the AgentCore Identity credential provider per your policy; a fork that supplies `gateway_m2m_secret_arn` owns that secret's rotation (SD-5) |
| G5 | Log retention | ⬜ Open | The kit-created log groups (probe, VPC flow logs) keep 365 days with a CMK; pre-create the runtime and interceptor log groups with `retention_in_days` per your policy (SD-4) |
| G6 | Network posture | ✅ Addressed | Private network mode is the default (see docs/SECURITY.md Network posture, SD-6). Before production: confirm the VPC inputs, narrow `private_client_cidrs`, and keep `knowledge_admin_principal_arns` short. |
| G7 | Account baseline controls | ⬜ Open | Confirm an org/landing-zone IAM Access Analyzer, or set `enable_access_analyzer=true` if this stack owns the baseline (SD-8) |
| G8 | Model-tier + guardrail review | ⬜ Open | Choose the model per task; review the starter Bedrock Guardrail against your content policy |
| G9 | Cost guardrails | ⬜ Open | Set an AWS Budget + CloudWatch billing alarm (Bedrock tokens dominate — see [`docs/COST.md`](docs/COST.md)) |
| G10 | Ingestion path (if used) | ⬜ Open | Wire an ingestion hook per [`docs/setup/source-knowledge.md`](docs/setup/source-knowledge.md); all writes must pass the data-protection guard |
| G11 | State backend | ⬜ Open | Copy `backend.tf.example` to `backend.tf`: an S3 backend with native locking (`use_lockfile`), bucket from `modules/backend-bootstrap` (via `platform/`), one key per companion (state is sensitive) |
| G13 | Probe disabled after testing | ⬜ Open | Set `enable_private_access_probe = false` and apply once the post-deploy tests are recorded (SD-25) |
| G12 | Independent security review | ⬜ Open | Have your security team review IAM, identity, network, and the accepted-security-debt table before production |

## Known limitations

- **Opt-in features are "ready infrastructure," not wired behavior.** AgentCore Memory
  (`enable_memory`) and the `sources.yaml` ingestion contract are provisioned/defined but not
  consumed by the *example* engine — a fork connects its engine to them. This is intentional and
  documented, not a defect.
- **Some governance controls are first-increment text detectors** (egress allowlist, resource
  ceilings) with mechanical enforcement on a tracked roadmap (`knowledge/PRINCIPLES.yaml`). The
  absolute boundaries (T1/T3/T6, grounding-block) are enforced today.
- **`bedrock_model_id` is a documented convenience input**, not consumed by the example engine
  (the engine selects its model via `config.yaml`); a fork wires it if needed.

## Related resources

- [`docs/SECURITY.md`](docs/SECURITY.md) — security model + the `SD-*` accepted-security-debt table.
- [`docs/DEPLOY.md`](docs/DEPLOY.md) — the human-run deploy + verify sequence.
- [`docs/COST.md`](docs/COST.md) — cost model + guardrails.
- [`PRINCIPLES.md`](PRINCIPLES.md) — the constitution.
