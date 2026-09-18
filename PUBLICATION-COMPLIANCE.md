# Publication Compliance — Content & Intellectual Property

This document records an evidence-based self-review of this repository against Amazon's
**Content & Intellectual Property** publication requirements. It covers the requirements that
can be verified from the source itself. The peer-review requirement is an internal process step
and is intentionally out of scope for this document.

- **Repository:** `governed-agentic-companion`
- **License:** MIT No Attribution (MIT-0) — see [`LICENSE`](LICENSE)
- **Last reviewed:** 2026-09-14

## Why this matters

Amazon must protect inventions, customer data, and confidential information. Releasing code that
implements patents or competitive features, or that leaks customer/confidential data, creates
legal and competitive risk. This review confirms the repository only demonstrates the use of AWS
services, contains no confidential or customer data, and reimplements no product features.

## Requirement checklist

| # | Requirement | Status |
|---|---|---|
| 1 | Only demonstrate the use of AWS services or products — nothing more | ✅ Pass |
| 2 | Write the code yourself, or use only Amazon code intended for open source publication | ✅ Pass |
| 3 | Don't use Confidential Information or code/data from other Amazon teams without permission | ✅ Pass |
| 4 | Don't reimplement features of Amazon products — only call their APIs | ✅ Pass |
| 5 | Demonstrate AWS best practices without inventing new non-obvious methods | ✅ Pass |

## Evidence

### 1 — Only demonstrates the use of AWS services

AWS is used only as an integration target, accessed through published SDKs/APIs:

- **Amazon Cognito** — `frontdoor/cognito_token.py` calls `admin_initiate_auth` to mint a
  short-lived access token (standard client-secret `SECRET_HASH` contract via stdlib `hmac`/`hashlib`).
- **Amazon S3** — `agents/kb_overlay.py` performs a single read-only `get_object` for the optional
  Tier-B knowledge overlay.
- **Amazon Bedrock AgentCore** — `agentcore/app/orchestrator/runtime_entrypoint.py` and
  `agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py` are thin adapters that register an
  entrypoint / MCP tools and delegate to `Orchestrator().handle(...)`.
- **Amazon Bedrock (model tier)** — referenced as a configurable Tier-3 provider in
  `config.yaml.example`; invoked as a model provider, never extended.

### 2 — Written for open source publication

- No internal-Amazon package imports, no internal build-system dependencies, no vendored
  third-party code, and no foreign copyright headers.
- Dependencies are mainstream open source: `pyyaml`, `pytest`, and (optional) `boto3`, `mcp`,
  `bedrock-agentcore`, `PyJWT`, OpenTelemetry packages.
- Licensed under MIT-0 (the standard AWS-samples license), Amazon copyright retained.
- Example specialists under `agents/specialists/` are explicitly marked as placeholders to be
  replaced by adopters.

### 3 — No confidential information or other-team code/data

- No customer names, real hostnames, account IDs, or ARNs in the repository.
- The only literal IP addresses are `127.0.0.1` (localhost) and `169.254.169.254` (the public
  EC2 Instance Metadata Service address).
- The only `AKIA`-shaped strings are AWS-documented **example** values that appear inside a
  secret-*detector* regex (`agents/governance_gate.py`) and a test fixture — not real credentials.
- Secrets are read from environment variables only; the token helper logs token length/TTL, never
  the token or secret value.

### 4 — Calls APIs; does not reimplement Amazon product features

- Every AWS touchpoint is a thin SDK/HTTPS client call. No AWS product internals (Bedrock model
  logic, AgentCore Runtime/Gateway internals, Cognito issuance/validation, S3 behavior) are
  re-created.

### 5 — Best practices, no non-obvious invention

- Mechanically, the core is a composition of well-known patterns: the governance gate is a set of
  regex detectors plus a confidence threshold and an audit footer; the orchestrator is
  keyword-scored routing; the integrity check is a standard file-hash-vs-baseline (SHA-256)
  comparison; the knowledge overlay is a config-merge with an allowlist and fail-open behavior.
  None of these is a novel, non-obvious method.
- Best practices demonstrated: environment-only secrets, short-lived tokens with pre-expiry
  refresh, `0600` permissions on written token files, read-only SDK calls, and justified static-
  analysis (`nosec`) suppressions.

## Notes for reviewers (non-blocking)

- The README/config advertise a Tier-2/Tier-3 (Ollama/Bedrock) LLM path that is not fully
  implemented in this repository; publication claims should be read as design intent for those
  tiers, with Tier-1 (deterministic) implemented.
- This kit is a customer-agnostic genericization of an internal multi-agent lineage; no customer-
  or internal-identifying content is present.

## Out of scope for this document

- **Peer review** is an internal Amazon process step and is tracked outside this repository.
