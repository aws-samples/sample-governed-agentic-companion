# Changelog

All notable changes to the Governed Agentic Companion starter kit are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
semantic versioning (MAJOR.MINOR.PATCH), tracked in `config.yaml` (`system.version`).

- **MAJOR** — breaking changes to the CLI, config schema, or governance model.
- **MINOR** — new capabilities added in a backward-compatible way.
- **PATCH** — fixes and documentation corrections that add/remove no capability.

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
