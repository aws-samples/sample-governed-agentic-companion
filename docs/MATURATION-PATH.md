# GAC Maturation Path — from empty seed to productionizable SME

> **The maturation path is the product.** GAC is a seed designed to *evolve into* a subject matter
> expert (see [`CORE-INTENT.md`](CORE-INTENT.md)). This document defines the stages that journey
> moves through, the measurable bar an instance must clear before builders consume it, and how
> each stage maps onto mechanisms GAC already has. It is a **readiness model**, not a governance
> tenet — the binding rules remain in [`PRINCIPLES.md`](../PRINCIPLES.md).

## Why GAC needs an explicit growth model

Today a forked GAC instance has a **birth state** (empty of domain, governance floor present) and
measures **per-answer** confidence and grounding. What it lacks is any notion of
*instance-level maturity*: "how expert is this companion in its field right now, and is it ready
for builders to rely on?" Without that, "self-evolving into an SME" is a hope, not a tracked,
graduatable journey. This model makes the becoming first-class and measurable.

It is deliberately parallel to the Context Spine's `leaf-status` state machine
(`ai-dlc-context/toolkit/schemas/leaf-status.yaml`), which tracks a leaf's *delivery* readiness
(`bootstrapped → investigating → … → delivered`). GAC's ladder tracks an instance's *expertise*
readiness. Same shape (a persisted status, advanced by evidence, never hand-waved); different
axis (domain mastery, not milestone delivery).

## The maturation ladder

A persisted instance status — `sme-maturity` — advanced only by evidence drawn from mechanisms
GAC already runs (grounding rate, coverage, decline rate, promotion activity). Never hand-set to
flatter an instance; a human ratifies the graduation to `productionized`.

```
seeded  ──▶  cultivating  ──▶  grounded  ──▶  productionized  ──▶  sustaining
   │                                                                    │
   └──────────────────── (never regresses silently; demotes on decay) ◀─┘
```

| Stage | Meaning | What's true at this stage |
|---|---|---|
| **seeded** | Just forked. Governance floor live; domain near-empty (example specialists + generic KB). | Safe from birth, but mostly declines in-domain questions — it has little to ground on. Correct starting state. |
| **cultivating** | Real specialists and seed knowledge dropped in; the learning loop is **on** and actively staging candidates. | Answers some in-domain questions grounded; many still decline. Feedback candidates accumulating; humans promoting regularly. This is the *active growth* stage. |
| **grounded** | Domain coverage broad enough that in-domain questions are usually answered grounded, not declined. | Grounding rate and coverage clear the readiness bar (below). The expert is *real* but not yet ratified for production consumption. |
| **productionized** | A human has ratified the instance against the readiness bar. Builders consume it as the project's SME. | The promise is met: a trustworthy, in-domain expert, safe by construction, human-in-the-loop. |
| **sustaining** | In production, still learning. Monitors for knowledge **decay** and domain drift. | Keeps compounding; demotes to `cultivating` if readiness metrics decay below the bar (knowledge gone stale, domain shifted). |

Design notes, mirroring `leaf-status`:

- **`cultivating` is the working stage and it cycles.** An instance can sit in `cultivating`
  through many promotion rounds; that is healthy, not stalled.
- **`seeded` and `sustaining` are the only states that aren't "climbing"** — one is pre-growth,
  one is steady-state-with-watch.
- **Graduation to `productionized` is human-owned** (consistent with Tenet 1/4): the metrics make
  an instance *eligible*; a human *ratifies* it. The machine never promotes itself into
  production.
- **Demotion is allowed and silent-regression is not.** `sustaining → cultivating` on measured
  decay keeps the status honest, the same way a `blocked` leaf is surfaced rather than hidden.

## The readiness bar (what "expert enough to productionize" means)

Trust-to-productionize must be *measurable*, built from mechanisms GAC already has — not a new
subjective judgment. Candidate metrics, all derivable from the existing audit trail
(`agents/audit.py`), feedback loop (`agents/feedback.py`), and per-answer grounding
(`assess_confidence_detail` in `agents/base_agent.py`):

| Metric | Source today | Why it gates productionization |
|---|---|---|
| **In-domain grounding rate** | audit trail: share of in-domain answers that cleared the 0.95 fact bar with cited sources | The expert must actually *know* its field, not decline it. |
| **In-domain decline rate** | feedback candidates vs. answered | A high decline rate means the seed hasn't grown enough yet. |
| **Domain coverage** | KB keys populated across the field's topic map | Breadth of expertise, not just depth in a corner. |
| **Promotion health** | feedback: candidates reviewed/promoted vs. piling up unreviewed | Proves the *learning loop itself* is alive, not just the KB. |
| **Zero governance regressions** | audit: no Tenet 1/3/6 blocks that indicate misuse; integrity clean | Trust is non-negotiable even at full maturity. |

The exact thresholds are an engagement choice (a regulated domain sets them higher), but the
*shape* is fixed: an instance is eligible for `productionized` only when in-domain grounding is
high, decline is low, coverage is broad, the loop is demonstrably active, and governance is clean.
`./run.sh verify` is the natural home for a `--maturity` report that computes these and states the
current stage with evidence.

## Where this sits in the three-layer toolchain

The maturation path governs GAC in its **SME role** (a fork becoming a domain expert). That is a
different role from GAC's **governance-substrate role**, where the gate is wired under an agentic
SDLC as an always-on floor (`integrations/aidlc/`, verified working 2026-10-01). Keep the two
separate: maturity measures the *expert growing up*; the substrate role lends GAC's *floor* to
another workflow and does not advance maturity. The full three-layer relationship (AI-DLC builds,
the Context Spine coordinates, GAC governs) is recorded in [`CORE-INTENT.md`](CORE-INTENT.md).

## How this maps onto the Context Spine

The spine is not just coordination — for GAC it is the **readiness substrate for the SME growing
up**, and it widens the becoming from one instance to a fleet:

- A GAC instance is a **leaf**; its `sme-maturity` is announced upward the same way `leaf-status`
  is — observe-and-broadcast, advisory, never gating (ADR-0012 / leaf-spine-integration.md).
- The spine lets one matured expert's knowledge become a **queryable, citeable grounding source**
  for other leaves (query-not-copy), so a new seed in a related field starts further up the
  ladder instead of from bare `seeded`. That is "self-learning" widened across a program.
- The spine never relaxes any leaf's gate. Each leaf stays safe-by-construction and
  human-in-the-loop; the spine only widens the *radius* of grounded knowledge.

## What this implies for the roadmap (intent-aligned, not feature-count)

Measured by the north-star test ("does this strengthen the becoming?"), the highest-value work is
**not** more governance. It is:

1. **Turn the learning loop on by default** — a seed with its growth switched off doesn't grow.
   For a kit whose first word is "self-evolving," off-by-default learning is the one setting in
   tension with the intent.
2. **Instrument `sme-maturity` + a `verify --maturity` report** — make the stage and the readiness
   bar visible, computed from the audit/feedback/grounding data already captured.
3. **Make domain-seeding and promotion low-friction and frequent** — the effort should go into
   making *the expert deepen easily*, not into more approval machinery around learning.
4. **Adopt the spine as the maturation substrate** — leaf-status-style readiness broadcast, plus
   cross-leaf grounded knowledge, so maturity compounds across a program.

Each item strengthens *the becoming* and touches Tenet 1/3/4 not at all. That is the signature of
work that is on-intent.

## Implementation design — `agents/maturity.py` + `verify --maturity`

> This section is the design spec for the shipped implementation. It follows the kit's existing
> conventions: deterministic, stdlib-only, fail-open, read-only over the data already captured by
> `agents/audit.py` and `agents/feedback.py`. It adds **no** LLM dependency, mutates **nothing**,
> and changes **no** principle. Maturity is an *observability* read, exactly like the audit trail —
> it reports; it never gates.

### Inputs (all already captured, read-only)

| Signal | Read from | Via |
|---|---|---|
| Answered / grounded work | `.audit/trail.jsonl` `gate_decision` events (`sources` present, `blocked=false`) | `AuditTrail.tail(n)` |
| Governance blocks | `gate_decision` events with `blocked=true` + `findings` | `AuditTrail.tail(n)` |
| Grounding gaps (proxy for in-domain decline) | `.feedback/candidates.jsonl` open candidates | `FeedbackLoop.list_candidates("candidate")` |
| Learning-loop health | promoted vs open candidates | `FeedbackLoop.list_candidates("promoted" / "candidate")` |
| Domain coverage | populated Tier-A + Tier-B knowledge keys | `BaseAgent.load_knowledge` / KB walk |

### The honest proxy (stated, not hidden)

The audit trail has **no explicit "in-domain" flag** — it records that a gate decision happened,
not whether the question was in the instance's field. So the implementation uses a transparent
proxy:

- **grounded answers** (audit events with `sources` and not `blocked`) stand in for *in-domain,
  answered*;
- **open feedback candidates** (ungrounded gaps) stand in for *in-domain, declined / thin*.

This is a reasonable proxy because an out-of-domain question that declines produces the same
feedback candidate as an in-domain gap — both say "the KB is thin here." The report **labels this
as a proxy** and never presents the maturity stage as a measured fact beyond what the data
supports (Tenet 4/5 honesty). A future enhancement (tagging audit events with a domain marker)
would sharpen it; until then the proxy is disclosed every time the report prints.

### The computed metrics

Over the last `N` audit events (default: all available) and the current feedback store:

- `grounding_rate` = grounded answers / (grounded answers + open gaps)
- `decline_rate` = open gaps / (grounded answers + open gaps)
- `coverage` = count of populated KB keys (absolute + a coarse band: sparse / growing / broad)
- `promotion_health` = promoted / (promoted + open candidates); plus an "unreviewed backlog" count
- `governance_clean` = no `blocked=true` events attributable to misuse in the window AND integrity verified

### Stage inference (deterministic)

```
if coverage is ~empty and almost no grounded answers      -> seeded
elif learning loop active (candidates accruing/promoting)
     and grounding_rate below the bar                     -> cultivating
elif grounding_rate >= bar and decline_rate <= bar
     and coverage broad and governance_clean              -> grounded      (eligible)
elif grounded AND a human ratification marker is present  -> productionized
elif productionized and metrics later decay below bar     -> sustaining -> (demote to cultivating)
```

Thresholds live in `config.yaml` under a new optional `maturity:` block (grounding-rate bar,
decline-rate ceiling, coverage band sizes), defaulting to sensible values a regulated engagement
can raise. Absent config, defaults apply — no config change is required for the report to work.

### Graduation is human-owned (Tenet 1/4)

The computation can mark an instance **`grounded` (eligible)**. It can **never** set
`productionized` itself — that transition requires a human ratification marker (a human runs an
explicit ratify action / writes the marker). The machine measures eligibility; a human confers
production status. This mirrors `leaf-status` graduation and keeps the growth model itself inside
the human-decision tenet. Demotion (`sustaining -> cultivating` on measured decay) is automatic
and surfaced, never silent — the honest direction is always allowed.

### Non-goals

- Not a gate. `verify --maturity` prints a report and returns 0; it never blocks anything.
- Not a new persistence store. It reads existing `.audit/` and `.feedback/` data; the only new
  persisted artifact is the optional human ratification marker.
- Not an LLM feature. Pure arithmetic over existing records.
