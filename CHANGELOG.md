# Changelog

All notable changes to the Governed Agentic Companion starter kit are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
semantic versioning (MAJOR.MINOR.PATCH), tracked in `config.yaml` (`system.version`).

- **MAJOR** — breaking changes to the CLI, config schema, or governance model.
- **MINOR** — new capabilities added in a backward-compatible way.
- **PATCH** — fixes and documentation corrections that add/remove no capability.

## [2.0.1] - 2026-10-10 — Content-security-review remediation (customer-agnostic + supply-chain hygiene)

Patch release. **No capability change and the governance core is untouched** — the 13 tenets,
Tenets 1/3, and the `PRINCIPLES.md` SHA-256 baseline are unchanged. This clears the findings from a
content/code security review (CSR) plus the GitLab security-dashboard scan. Upgrade is a straight
pull; no config migration.

- **Dependency CVEs (dependency scanning).** Upgraded **PyJWT 2.13.0 → 2.15.1** in both runtime
  requirements files, closing the full PyJWT 2.13.0 CVE family flagged by the scan (CVE-2026-102266
  / -102267 / -102268 / -102271 / -102272 / -102273 — JWK/HMAC authentication-bypass and token-forgery
  issues). Upgraded **mcp (MCP Python SDK) 1.28.0 → 1.30.0** in the MCP runtime, closing the
  session-hijack / cross-session task-access advisories (CVE-2026-52869 / -52870, GHSA-qx49-fqc8-xw99).
  Both requirements files re-verified to resolve cleanly against the `bedrock-agentcore==1.18.1` floor
  on linux/arm64 + Python 3.12.
- **Secret-detection false positive.** The Gitleaks "Password in URL" hit in
  `agents/data_protection.py` is the data-protection scrubber's own password-in-URL **detection**
  regex, not a secret. Suppressed inline with `# gitleaks:allow` (and a `.gitleaks.toml` allowlist);
  the detection regex is byte-for-byte unchanged — the control is suppressed, not weakened.
- **Remediation record.** Added `docs/security/VULN-TRIAGE-2026-10-10.md` and
  `docs/security/VULN-REMEDIATION-2026-10-10.md` dispositioning every scanned row
  (fixed / false-positive / accepted-debt mapped to an SD row).
- **Customer-agnostic hardening (Tenet 6).** Replaced real upstream-engagement identifiers in
  `scripts/leak_scan.sh` (`CUSTOMER_PATTERNS`) with generic placeholder tokens, so the public
  starter kit no longer embeds a real customer identity. A fork now substitutes its own engagement's
  identifiers. The leak-scan guardrail's purpose is unchanged.
- **Supply-chain pinning.** Pinned the CI `pyyaml` install to `6.0.3` (matches `requirements.txt`)
  in `.gitlab-ci.yml`, and pinned the `pre-commit` install instruction to `4.6.2` in
  `.pre-commit-config.yaml`, so CI and local hooks resolve reproducible versions.
- **`plugin/.mcp.json` supply-chain note.** Documented the `@latest` risk inline (`_supply_chain_note`)
  with instructions to pin each MCP tool entry to a tested version before use (new SD-13).
- **KMS least-privilege guidance.** Added an inline production-hardening note to the ECR KMS
  key-administration policy in `agentcore/terraform/ecr.tf`, advising a scope-down of the
  service-scoped wildcards (`kms:Delete*`/`kms:Disable*`/`kms:Create*`) for production (new SD-12).
- **Docs.** Added accepted-security-debt rows **SD-12** (KMS wildcards) and **SD-13** (`@latest`)
  to `docs/SECURITY.md`.

## [2.0.0] - 2026-09-25 — Self-evolution loop + governance substrate for an agentic SDLC

Major uplift. **The governance core is unchanged** — the same 13 tenets, Tenets 1/3 still
absolute, and the gate still has **no off switch** (`PRINCIPLES.md` SHA-256 baseline untouched).
This release is MAJOR because it adds new capability surfaces and CLI commands, with an always-on
feedback loop woven into every piece of work. Upgrade: copy the new `config.yaml.example`
(`system.version: 2.0.0`); new runtime data lives under the gitignored `.audit/`, `.feedback/`,
and `.plans/`.

- **Action-level audit trail** (`agents/audit.py`, `./run.sh audit`). Every gated response from
  every front door appends a metadata-only event (front door, agent, trust zone, measured
  confidence, grounding sources, gate decision, findings) to `.audit/trail.jsonl`. No raw text,
  secrets redacted. Observability only — the gate remains the sole enforcement path. Closes the
  action-audit gap behind the say-vs-do concern (knowledge/PRINCIPLES.yaml ENF-004).
- **Feedback / evolution loop** (`agents/feedback.py`, `./run.sh feedback {list|promote|dismiss}`).
  Every work item emits a signal; a real grounding gap stages a *candidate* learning. A human
  reviews and promotes it to a Tier-B overlay entry at a `team` or `project` memory layer. Staged
  → reviewed → promoted (Tenet 8) — never silently absorbed, never self-written; Tier-A stays
  locked (a promotion to a Tier-A key is refused).
- **Layered memory** (`agents/memory_layers.py`). org/team/project precedence over the existing
  Tier-A/Tier-B knowledge base; `project > team > org`; a layer never escalates trust. No-op by
  default (behavior unchanged unless promoted layer entries exist).
- **Staged mode** (`agents/staged.py`, `./run.sh plan {create|show|approve|step}`). Opt-in
  intent → checkboxed plan → **human approval** → execute one gated step at a time. Executing a
  step produces a review-ready artifact and never deploys or mutates anything (Tenet 1); the
  orchestrator stays single-hop (no autonomous agent-to-agent spawning). Plans persist under
  `.plans/` with per-step traceability.
- **Sensors + parity guard** (`agents/sensors.py`, `scripts/parity_check.py`, `./run.sh verify
  [--list|--parity]`). Advisory, inspectable verification manifests (claim-sources,
  required-sections, traceability, no-absolute-claim) that report but never block, plus a
  determinism/parity guard proving every gate front door reaches the same verdict for the same
  input.
- **Governance substrate for an agentic SDLC.** The gate is exposed as a `Stop`-style command
  hook (`integrations/aidlc/`) and a local, deterministic MCP tool
  (`frontdoor/gate_mcp_server.py` → `govern_artifact`), so an agentic SDLC (e.g. AI-DLC) or any
  harness can run an AI-produced artifact through GAC's unbypassable floor before accepting it.
  Fails open when GAC is absent, fails closed on an absolute violation. This is a **technical
  integration, not a partnership or endorsement**, and GAC does not replace an agentic SDLC.
- Suite: 135 tests passing; `./run.sh status` integrity-clean; `./run.sh verify --parity` green.
  MIT-0 preserved.

## [1.1.2] - 2026-09-24 — CSR remediation, confidence calibration, fork-ready LLM peering

- **Content Security Review (Holmes) remediation.** Added non-production/sample disclaimers to
  the README and deployment docs (Cognito/JWT/IAM/OAuth guidance is illustrative, not for
  production as-is); pinned the core dependencies (`pyyaml`, `pytest`) and gave the
  previously-unpinned `aws-opentelemetry-distro` a `>=0.17.1` floor in the AgentCore
  requirements; dropped the bare unpinned `pytest` from the installer. The derived evidence
  corpus (`scripts/generate_bible.py`) now emits **repo-relative** Evidence paths, so a
  generated `BIBLE.md` carries no developer-username PII; the orchestrator's allowed evidence
  roots include the repo root so relative citations resolve.
- **Confidence calibration (relevance damping).** `assess_confidence_detail` now damps the
  grounding score by how much of the request the answer actually addresses, so a response full
  of KB vocabulary that ignores the question can no longer score high on vocabulary alone. An
  on-topic grounded answer still clears the fact bar; a term is matched by stem as well as exact
  substring so paraphrase isn't falsely penalised.
- **Fork-ready LLM peering.** The peering decline is now a reusable `_peering_decline` method,
  and `llm_answer_or_decline(llm_answer=...)` is provided so a fork that adds an LLM tier
  degrades an ungrounded answer to the peering decline instead of a hard gate block — without
  the deterministic base kit taking any LLM dependency (the example specialists remain
  deterministic-only).
- Suite: 105 tests passing; `./run.sh status` integrity-clean. MIT-0 preserved.

## [1.1.1] - 2026-09-23 — Peering decline + lint/dependency hygiene

- **Peering decline (self-evolution).** A specialist that can't ground an answer no longer
  dead-ends. It acknowledges intent and offers grounded paths forward — route via the
  Orchestrator (`ask`), refine within scope, or co-build a genuine new requirement and
  capture the confirmed facts so the knowledge base grows (Tenet 8). Governance intact: no
  fabrication, no action, a human still builds and deploys (Tenet 1).
- **Lint/dependency hygiene.** PowerShell installer: `Write-Host` → an information-stream
  helper (PSAvoidUsingWriteHost) + UTF-8 BOM. Shell installer: `cd … || exit` (SC2164) and a
  read/write-same-file fix (SC2094). MCP runtime floor `mcp>=1.28.0` (CVE-2026-52869/-52870/
  -59950); root advisory guidance refreshed. MIT-0 and publication-compliance preserved.
- Suite: 100 tests passing; `./run.sh status` integrity-clean.

## [1.1.0] - 2026-09-23 — Evidence gateway + multi-IDE front-door parity

- **Optional evidence gateway (`evidence.provider`, default `none`).** Provider-neutral
  evidence-provenance contract (`agents/evidence_gateway.py` + `agents/evidence_providers/`).
  When enabled, a packet is built before the orchestrator dispatches to a specialist, and the
  gate BLOCKS on a missing/tampered/not-READY/route-mismatched packet. GOVERNANCE GUARDRAIL:
  the packet reaches only the gate's `_detect_evidence` detector — never confidence scoring —
  so evidence can only TIGHTEN the gate (Tenet 4 measurement boundary). `./run.sh bible`
  derives `.evidence/BIBLE.md` from `knowledge/` (a pointer into the KB, not a rival ledger —
  Tenet 8); `.evidence/` is gitignored. Byte-for-byte no-op when `provider: none` (the default).
- **Wired front door (was a reference stub).** `frontdoor/runtime_client.py` now invokes the
  deployed AgentCore runtime with a Cognito bearer token (retry-once on 401/403), and
  `frontdoor/mcp_bridge.py` is a working stdio MCP server exposing `ask_companion` /
  `companion_kb`. Governance is unchanged: the bridge relays; the runtime response is gated
  in the cloud.
- **Installable Claude Code plugin (`plugin/`).** Orchestrator + example specialists as
  sub-agents (each `disallowedTools: Bash`), a domain Skill, MCP wiring, `permissions.example.json`,
  and a `Stop`-hook governance backstop that runs the engine's gate over each sub-agent's
  completed response. MIT-0.
- **One-command multi-IDE onboarding (`onboarding/`).** `install-companion-frontdoor.sh` (+
  `.ps1`) sets up the venv, runs `status`, and optionally installs routing pointers for BOTH
  front doors — a Kiro `inclusion: auto` steering pointer and a marked Claude Code `CLAUDE.md`
  block — without overwriting a team's own files. Includes Kiro MCP example configs (local
  bridge + gateway).
- **Docs:** `docs/FRONT-DOORS.md` rewritten to three connection styles (local engine / Claude
  Code plugin / cloud MCP via bridge or gateway); README "What you get" + layout updated; added
  this CHANGELOG and an `AGENTS.md`.
- MIT-0 and the publication-compliance posture preserved. Suite: 100 tests passing;
  `./run.sh status` integrity-clean.

## [1.0.0] - 2026-09-11 — Public starter-kit release

- Initial public release (MIT-0): governed orchestrator + two example specialists (platform,
  security) over a common `BaseAgent`, the always-on Governance Gate (13 tenets, integrity-
  verified), a three-tier execution model (deterministic → Ollama → Bedrock), a hybrid Tier-A/
  Tier-B knowledge base, and the CLI front door (`status` / `ask` / `gate`).
