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
