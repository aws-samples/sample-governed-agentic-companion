# GAC Core Intent — the north star

> This document records the **founding intent** of the Governed Agentic Companion and the test
> every future change is measured against. It is a statement of purpose, not a governance tenet:
> the binding, integrity-baselined rules live in [`PRINCIPLES.md`](../PRINCIPLES.md). This document
> explains *what GAC is for*; `PRINCIPLES.md` enforces *how it must behave while getting there*.

## The intent (verbatim)

> **A self-evolving, self-learning subject matter expert assistant in a specific field/application,
> who is always safe to trust on real delivery work by the builders, and at the same time depends
> on human decision.**

## Reading the intent precisely

The sentence carries four claims. The order and the *tense* both matter.

1. **A subject matter expert — in one specific field/application.** The value is domain *depth*,
   not generic breadth. GAC is not a chatbot wrapper; it is meant to become the project-trained
   expert on tap.
2. **Self-evolving, self-learning.** The expertise *compounds* over the life of an engagement.
   It gets sharper through use, rather than being static.
3. **Always safe to trust on real delivery work.** The expertise is usable on
   production-adjacent work precisely because trust is *structural* (code, not prompts), present
   from birth.
4. **At the same time depends on human decision.** The expert reasons and recommends; the human
   decides and acts. Held *simultaneously* with claim 2 — never traded off.

## The critical nuance: GAC is a seed, not a born expert

**GAC never intends to be *born* an SME. It is a starter kit designed to *evolve into* one.**
An empty-of-domain birth state is correct by design, not a deficiency. The fork-and-build model
*is* the maturation mechanism: fork the kit, drop in the engagement's specialists and knowledge,
and let it learn through use until it is expert enough to **productionize** for builders to consume.

This reframes the product's reason for existing. GAC's deliverable is not "an expert" shipped in a
box — it is **the governed journey from empty seed to trustworthy, productionizable SME.** The
maturation path is the product. See [`docs/MATURATION-PATH.md`](MATURATION-PATH.md).

## How the four claims reconcile (the seam most "governed AI" gets wrong)

A thing that **self-evolves** (claim 2) and a thing that **depends on human decision** (claim 4)
sound contradictory — autonomy versus control. GAC's design is the reconciliation:

- It self-evolves its **knowledge** — staged → human-reviewed → promoted (Tenet 8). The knowledge
  base grows; the expert gets deeper.
- It never self-evolves its **authority to act** — Tenets 1 and 3 are absolute and cannot change,
  even by the governance handshake.

**It learns what it *knows*; it never learns the right to *do*.** That is the precise seam where
"self-learning" and "human decides" coexist instead of fighting.

## Heart vs circulatory system

The subject-matter expertise is the **heart** — the reason GAC exists. The governance floor (the
always-on gate, the integrity baseline, bounded agency) is the **circulatory system** — what makes
a *learning* expert safe to trust on real work instead of dangerous. Both are essential; they are
not co-equal in *purpose*. Governance is in service of a trustworthy expert, not the other way
around. A narrative or a roadmap that leads with the floor and treats the growing expertise as a
side feature has the emphasis inverted.

## The north-star test (apply to every change)

> **Does this change make the expert deeper, or its learning wider/faster/more reliable — without
> ever relaxing the dependence on human decision?**

- A change that strengthens **the becoming** (depth of domain, or the maturation path from seed to
  productionizable SME) is *on intent*.
- A change that adds governance machinery with no effect on the becoming is *neutral at best* —
  and, past a point, is over-building the circulatory system around a heart that isn't growing.
- A change that relaxes Tenet 1 or Tenet 3 to buy autonomy is *off intent*, full stop, regardless
  of how much capability it adds.

## Assessment against the intent (2026-10-01)

Measured as a *seed*, not as a finished expert:

| Claim | Status | Evidence |
|---|---|---|
| 1. Becomes an SME in a field | **On target by design** | Empty-of-domain at birth is correct. Fork-and-build *is* the mechanism. ~65 lines of example specialist + ~150 lines of generic KB is the right birth state, not drift. |
| 3. Safe to trust on real delivery work | **On target** | The gate, SHA-256 integrity baseline, and layered enforcement are real, present from birth, and tested. Trust is structural, not deferred. |
| 4. Depends on human decision | **On target — zero drift** | Tenets 1/3 absolute and unchangeable even by handshake. Every capability added since 1.0 (staged mode, feedback loop, spine direction) was engineered to stay on the artifact side of the line. |
| 2. **Self-evolving into the SME** | **The one gap worth attention** | The evolution *mechanism* exists (`feedback.py` → promote → `memory_layers.py`) but ships **dormant / off by default**, and there is **no explicit maturation model** — no defined stages from empty seed to "expert enough to productionize," and no measurable readiness bar. For a kit whose whole purpose is to *evolve into* the expert, the becoming is the least-developed, least-defaulted part. |

**Bottom line: no deviation of principle.** The seed is correctly a seed, the floor is correctly
present at birth, and human-decision is absolute. The single thing worth attention is that the
**maturation path — the evolve-into-SME journey that is the kit's entire purpose — is the thinnest
and least-defaulted part of the system.** The corrective is not more governance (that is complete,
arguably over-built); it is to make *the becoming* first-class. The Context Spine's leaf-status
model points the right way: it is a ready-made readiness ladder for exactly this journey.

## GAC's two purposes — do not conflate them

GAC is used in two distinct ways. Both are real; they serve different intents and should not be
confused when reasoning about a change.

1. **GAC-as-SME (the founding intent above).** A fork matures into a trustworthy domain expert in
   one field, following the maturation path. This is "the becoming." The north-star test applies
   here.
2. **GAC-as-governance-substrate.** The deterministic gate is wired *under someone else's agentic
   workflow* as an always-on floor — see `integrations/aidlc/`. Here GAC is not becoming an expert;
   it is lending its floor to another system (e.g. AI-DLC).

A change to the governance substrate (purpose 2) is *neutral* against the founding intent (purpose
1): it neither deepens the expert nor advances the maturation path. That is fine — it is a
legitimate separate capability — but it should not be counted as progress toward "becoming an SME,"
and it must never relax Tenet 1/3 for either purpose.

## The three-layer picture (AI-DLC + Context Spine + GAC)

GAC does not stand alone in the ProServe agentic toolchain. Three systems stack cleanly, each with
one purpose:

| Layer | System | Purpose | Enforcement style |
|---|---|---|---|
| **Build each thing** | AI-DLC Workflows (v2.7, 5 phases / 33 stages) | Run one use case's lifecycle, human-gated per stage | Per-stage human gates + ordering hooks + **advisory** sensors |
| **Coordinate many things** | Context Spine | Keep many use-case "leaves" coherent across a program | Observe-and-broadcast, **advisory**, never gates |
| **Floor under every step** | GAC Governance Gate | Block an artifact that crosses an absolute safety boundary, at any stage | Deterministic, **always-on, no off switch** |

The division of labor: **AI-DLC runs the work, the Context Spine coordinates the work, GAC governs
the work.** AI-DLC's own README is honest that its rigor is per-profile and its sensors are
advisory ("generative AI can make mistakes; review the output"). GAC is the always-on floor that
AI-DLC and the spine are honest about not providing. This is a *technical integration, not a
partnership or endorsement*, and GAC does not replace AI-DLC — they are complementary layers.

**Verified (2026-10-01).** The shipped `integrations/aidlc/companion-gate-hook.sh` was executed
against (a) a raw deployment claim, (b) a clean review-ready artifact, and (c) an AI-DLC-shaped
`Stop` event JSON with the violation buried in `messages[].content`. Results: (a) and (c) blocked
with exit 2 on Tenet 1; (b) allowed with exit 0 and the full governance footer. The hook parses the
harness Stop-event shape, not just raw text, so the end-to-end AI-DLC path is confirmed, not merely
inferred from the exit-code contract.
