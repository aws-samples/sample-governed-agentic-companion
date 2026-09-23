---
name: governed-agentic-companion
description: >
  A governed, reusable multi-agent companion. An orchestrator routes each request to a
  specialist and every response passes an always-on Governance Gate: human-owned deployment
  in every environment, autonomous grounded review, and read-only access to all systems of
  record. Fork per engagement and replace the example specialists + knowledge with your own.
inclusion: manual
---

# Governed Agentic Companion

## Purpose

Turn a team's delivery knowledge into governed, reusable agents. Use this skill to produce and
review artifacts (platform/infrastructure guidance, security guidance, and whatever specialists
you add) as review-ready outputs. Nothing here deploys — a human applies every change in every
environment (see `PRINCIPLES.md`, Tenet 1).

## When to use

- You want a governed front door where every agent response is gated, grounded, and traceable.
- You are producing or reviewing platform/infrastructure or security guidance.
- You want a starter kit to fork: add your own specialists + knowledge and keep the governance.

## Agents

| Agent | Responsibility |
|-------|----------------|
| `companion-orchestrator` | Entry point. Classifies the request, routes to one specialist, presents the gated result. |
| `companion-platform` | Example: platform/infrastructure topology, environments, runtime, scaling, regions. |
| `companion-security` | Example: secrets handling, auth/identity, TLS/mTLS, egress, permissions, encryption. |

> `companion-platform` and `companion-security` are EXAMPLE specialists (they mirror the
> engine's `agents/specialists/`). Replace them with your own domain specialists when you fork.

## Knowledge base

Grounding comes from the tiered YAML knowledge base (`knowledge/`):

- `tier_a/platform`, `tier_a/security` — locked, high-trust domain knowledge.
- `tier_b/…` — lower-velocity or engagement-specific knowledge you add.
- `PRINCIPLES.yaml` — the governing constitution, integrity-verified at startup.

Every factual claim traces to one of these files or a read-only system of record.

## Workflow

1. **Route** — `companion-orchestrator` classifies the request and picks a specialist.
2. **Ground** — the specialist reads relevant knowledge and read-only source state.
3. **Generate** — it produces the artifact with assumptions, risks, and sources.
4. **Gate** — every response passes the always-on Governance Gate (Tenets 1/3/4/6).
5. **Present** — the artifact is returned with a "human action required to deploy" note.
6. **Record (optional)** — grounded findings may be written to Jira/Confluence/GitHub.

## Governance (deterministic, never bypassed)

- **Tenet 1 — Human-owned deployment:** no agent deploys, applies, promotes, restarts, or
  mutates any environment, including non-production.
- **Tenet 3 — Bounded agency:** all systems of record are read-only; writes go only to
  Jira/Confluence/GitHub.
- **Tenet 4 — Grounded reasoning:** claims trace to the knowledge base; an ungrounded
  exploration-zone answer below the fact bar is blocked, not decorated with a warning.
- **Tenet 6 — Data governance:** no secrets or customer-identifying data in the knowledge base
  or artifacts.

## Customization (per engagement)

Fork the repository and replace `agents/specialists/` + `knowledge/tier_a|tier_b` with your own.
Never commit customer identifiers, credentials, hostnames, IP ranges, or schemas — keep the
knowledge base product-agnostic (`PRINCIPLES.md`, Tenet 6).

## Limitations

- Systems-of-record MCP servers are read-only; the skill cannot change live systems.
- This skill is not legal or compliance advice; a qualified team assesses regulatory fit.
