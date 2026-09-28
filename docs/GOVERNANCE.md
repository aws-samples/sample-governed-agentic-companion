# Governance — how the boundary is enforced

The companion's governance is enforced **as code**, layered, and integrity-protected. This is the
property that lets a security team approve it. Full constitution: [`../PRINCIPLES.md`](../PRINCIPLES.md).

## The always-on gate ([`../agents/governance_gate.py`](../agents/governance_gate.py))

Every response path funnels through **one shared gate instance** — there is no mode or flag that
disables it. It runs deterministic detectors and either returns the response (with a
governance-outcome footer) or a block notice (the original, possibly-violating text is
**withheld**, never echoed):

| Detector | Tenet | Severity |
|---|---|:---:|
| Executed deploy/mutation claim | 1 | **BLOCK** |
| Write to a system of record | 3 | **BLOCK** |
| Secret material in output | 6 | **BLOCK** |
| Ungrounded LLM answer (below the fact bar, or unmeasured) | 4 | **BLOCK** |
| Fetch-and-execute **directive** | 11 | **BLOCK** |
| Fetch-and-execute in a **description** | 11 | INFO |
| Egress-circumvention claim | 11 | INFO |
| Self-narrated resource runaway | 12 | INFO |

The **directive-vs-description** distinction (Tenet 11) blocks an agent *instructing* a
fetch-and-execute while surfacing — not withholding — a legitimate *assessment* that merely
mentions a `curl | bash`-shaped step. It fails safe toward surfacing, because the absolute
no-execution guarantee comes from Tenet 1 + the permission layer, not this text scan.

## Layered enforcement (Tenet 13) — and honesty about it

- **Mechanical (action layer):** read-only tools/credentials + a permission boundary that denies
  mutating tool calls at invocation. **This is what guarantees no mutation.**
- **Observability (text layer):** the gate scans output text. **This is the honesty/audit signal,
  never treated as proof of what the agent actually did.**

Several mechanical controls (a network-layer egress allowlist, hard resource ceilings, a single
action-layer seam) ship as first-increment text detectors now, with mechanical enforcement on a
tracked roadmap ([`../knowledge/PRINCIPLES.yaml`](../knowledge/PRINCIPLES.yaml)). The kit states
which controls are deterministic vs advisory rather than overclaiming — that honesty is a tenet.

## Grounding (Tenet 4)

Confidence is **measured** (knowledge-match ratio + coverage), not guessed, and the grounding
sources are returned. An LLM (exploration-zone) answer below the fact bar — or with no measurement
at all — is **blocked**, not shown with a warning. Deterministic (verified-zone) output is grounded
by construction and is not blocked on the heuristic. Every passing response shows the zone, the
tenets checked, the confidence, the sources, and the gate status in its footer.

## Integrity (Tenet 9)

`PRINCIPLES.md` is SHA-256 baselined in [`../governance/integrity.yaml`](../governance/integrity.yaml)
and verified at startup (`./run.sh status`). Changing the constitution requires the governance
handshake: state intent + justification, review the diff, then re-baseline with
[`../scripts/rebaseline_integrity.py`](../scripts/rebaseline_integrity.py). Do **not** add a gate
off switch — an unbypassable principle must not have a bypass.

## Knowledge safety (Tenet 8)

Field corrections are staged, checked against locked constraints by a contradiction guard, and
promoted by a human — never absorbed silently. Locked Tier-A constraints are never overlaid from
S3 (the overlay allowlist is explicit; see [`../agents/kb_overlay.py`](../agents/kb_overlay.py)).

---

# Operations — running the companion as a best practice

Governance is only credible if it is operated well. This section is the operational companion to
the enforcement model above.

## Change management (the governance handshake)

Changing the constitution (`PRINCIPLES.md`) is a deliberate, auditable act:

1. **State intent + justification** for the change (what tenet wording changes and why).
2. **Review the diff** — never edit a tenet silently.
3. **Re-baseline** the integrity hash with
   [`../scripts/rebaseline_integrity.py`](../scripts/rebaseline_integrity.py).
4. **Never add a gate off switch.** An unbypassable principle must not have a bypass. The two
   absolute tenets (T1 human-owned deployment, T3 bounded agency) are not relaxable by config,
   prompt, or agent reasoning.

## Knowledge promotion (human-in-the-loop)

- Tier-A (locked) constraints change only via code review — never from the S3 overlay.
- Tier-B knowledge is overlay-eligible via an explicit allowlist; a curated overlay is promoted by
  a human to `kb/curated/overlay.json`.
- Every write to any knowledge/memory store passes the data-protection guard (Tenet 6) — secrets
  rejected, PII/proprietary redacted — so the shared KB stays customer-agnostic by construction.

## Monitoring & audit

- **Logs:** CloudWatch Logs scoped to `/aws/bedrock-agentcore/runtimes/*`; set a retention policy
  for your compliance scope (SD-4 in [`SECURITY.md`](SECURITY.md)).
- **Tracing:** X-Ray active tracing on the interceptor request path.
- **The governance footer** on every response (trust zone, tenets checked, confidence, sources,
  gate status) is the per-response audit record — surface it, don't strip it.
- **Recommended alarms:** repeated inbound-auth failures, gateway target connection failures, and
  unusual `bedrock:InvokeModel` volume (a cost + misuse signal).

## Operational runbook (essentials)

| Situation | Action |
|---|---|
| MCP runtime crash-loops after deploy | Read `/aws/bedrock-agentcore/runtimes/<mcp>-DEFAULT`; fix the container dependency; bump `image_tag`; re-apply |
| Gateway target `FAILED` | Almost always the MCP runtime failing its boot/health check — fix the runtime, then re-apply (the target recreates) |
| Token expired / 401 in the IDE | The PKCE flow auto-refreshes; for the Bearer path re-run `frontdoor/gateway_token.py` |
| Rotate the M2M secret | Manual per SD-5 until a rotation Lambda is supplied; see the terraform README rotation section |
| Stop all cost | `terraform destroy` (human-run) — see [`COST.md`](COST.md) |

## Security standards this kit implies

The controls above map to widely-recognized standards; a fork can cite these in a review:

- **AWS Well-Architected — Security & Operational Excellence pillars:** least-privilege IAM,
  encryption at rest/in transit, centralized logging/tracing, safety-by-construction.
- **AWS Well-Architected Agentic AI Lens:** bounded agents (read-only tools + permission
  boundaries), behavior-as-code (the gate), proportionate human oversight (Tenet 1), verified
  identity propagation (ADR-0001), end-to-end traceability (the footer + logs).
- **OWASP LLM Top 10 (indicative):** prompt-injection defense (guardrail + gate), sensitive-info
  disclosure (data-protection guard + secret-leak block), insecure output handling (gated output),
  excessive agency (bounded agency / no-deploy).
- **CIS-style baseline hygiene:** MFA-capable identity, KMS key rotation, TLS-only storage,
  immutable image tags, no long-lived plaintext secrets.

These are alignment claims for a starter kit, not a certification. Validate against your own
compliance program before relying on them.

## Related resources

- [`SECURITY.md`](SECURITY.md) — the full security model + accepted security debt.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — trust boundaries.
- [`../PROD-READINESS.md`](../PROD-READINESS.md) — readiness gates.
