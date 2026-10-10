<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Cost analysis — Governed Agentic Companion

Approximate AWS running cost of the deployed AgentCore topology, across every layer, with
per-team-size models for builders running daily tasks and the levers that move the number. Cost
proportionality is a governance tenet (Tenet 7 — Sustainability & Cost), so the kit is built so you
pay only for the postures and opt-ins you turn on.

> **Pricing basis (verify before quoting).** The rates below are AWS public **list prices** for
> `ap-southeast-2`-class Regions, for planning only. AgentCore and Amazon Bedrock are
> **consumption-priced** — you pay per request/token/GB-second, with **no idle instance**. Always
> re-check the live [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) and
> [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) pages; Regional rates vary. These are
> **estimates, not a quote**, and rates change — confirm current pricing for your Region and model.

## 1. Unit prices used

| Layer | Unit | List price (indicative) |
|---|---|---|
| AgentCore Runtime — CPU | per vCPU-hour | ~$0.0895 |
| AgentCore Runtime — memory | per GB-hour | ~$0.00945 |
| AgentCore Gateway — tool invocations | per 1M tool API calls | ~$5.00 |
| AgentCore Gateway — semantic tool search | per 1,000 queries | ~$7.00 |
| AgentCore Memory — short-term events | per 1,000 events | ~$0.25 |
| AgentCore Memory — long-term storage | per 1,000 memories | ~$0.75 (tiers down) |
| AgentCore Memory — long-term retrieval | per 1,000 retrievals | ~$0.50 |
| Bedrock — Claude Sonnet-class input | per 1M tokens | ~$3.00 |
| Bedrock — Claude Sonnet-class output | per 1M tokens | ~$15.00 |
| Bedrock — Sonnet-class cache read | per 1M tokens | ~$0.30 |
| Bedrock Guardrail | per 1,000 text units | ~$0.75 |
| Amazon S3 (KB bucket) | per GB-month | ~$0.025 |
| AWS Secrets Manager | per secret-month | ~$0.40 |
| AWS KMS | per key-month + per 10k requests | ~$1.00 + ~$0.03 |
| Amazon CloudWatch Logs | per GB ingested | ~$0.50–$0.67 |
| Amazon ECR | per GB-month stored | ~$0.10 |
| Amazon Cognito | per MAU (free tier ≤ 10k) | $0.00 typical |

## 2. The cost shape (what actually drives the bill)

**Bedrock tokens dominate.** Everything else — Runtime compute, Gateway calls, Memory, storage —
is cents-to-low-dollars per builder-month. Because the companion answers **deterministically with
no LLM in the path** by default, a factual-recall answer (`companion_kb`, status-type queries)
costs **$0 in tokens** — it never calls the model. An LLM answer (`ask_companion` on a model tier)
with a grounded context of ~8k input + ~1k output tokens costs roughly **$0.024 + $0.015 ≈ $0.039**.
So **cost per builder is governed by how many LLM calls they make and how big the context is**, not
by the platform footprint.

**Fixed monthly floor (present whether or not anyone uses it):**

| Item | Qty | ~$/month |
|---|---|---|
| KMS keys (KB + ECR + M2M) | 3 | ~$3.00 |
| Secrets Manager (M2M secret) | 1 | ~$0.40 |
| S3 (KB + logs, few MB) | <1 GB | ~$0.05 |
| ECR (2 images) | ~1–2 GB | ~$0.20 |
| CloudWatch Logs (idle-ish) | ~1 GB | ~$0.60 |
| **Fixed floor** | | **~$4.25/month** |

There is **no idle Runtime/Gateway/Memory charge** — those bill only on use. An unused full
deployment costs roughly **$4/month** (mostly the 3 CMKs). A minimal deployment
(`deploy_gateway=false`, `enable_memory=false`) drops the M2M key + secret and lands near **$3**.

## 3. Per-request cost model

A typical builder request that reaches the LLM through the gateway:

| Component | Amount | Cost |
|---|---|---|
| Gateway tool invocation | 1 call | $5/1M = **$0.000005** |
| Runtime compute | ~1 vCPU × ~6 s + 2 GB × ~6 s | **~$0.00018** |
| Bedrock tokens (Sonnet-class) | ~8k in + ~1k out | **~$0.039** |
| Bedrock Guardrail (if enabled) | ~1 text unit | **~$0.0008** |
| Memory (event write + summary + retrieval, if enabled) | ~3 ops | **~$0.0015** |
| **Per LLM request** | | **~$0.041** |
| **Per deterministic request** (`companion_kb`, status) | Gateway + compute only | **~$0.0002** |

The LLM request is ~95% Bedrock tokens. Deterministic requests are effectively free. This is why
the three-tier design (deterministic → local → cloud) is a **cost** control, not just a latency one.

## 4. Team-size models (builders running daily tasks)

Assumptions: 22 working days/month; a "task" mixes deterministic + LLM calls. We model an
**LLM-call/day/builder** rate at the ~$0.041/call figure above, with Memory/Gateway/compute rolled
in at ~$0.003/request all-in on top of tokens. All opt-ins (gateway + memory + guardrail + model
tier) ON.

### Small team — 5 builders, ~10 LLM calls/builder/day
- LLM calls: 5 × 10 × 22 = **1,100/month** → × $0.041 ≈ **$45**
- Deterministic calls (negligible tokens): ~$1
- Memory (a few hundred sessions): ~$1
- Fixed floor: ~$4
- **≈ $51/month** (~$10/builder/month)

### Medium team — 25 builders, ~15 LLM calls/builder/day
- LLM calls: 25 × 15 × 22 = **8,250/month** → × $0.041 ≈ **$338**
- Memory (more sessions + cross-builder summaries): ~$8
- Gateway tool calls (~30k/mo): ~$0.15
- Compute: ~$3; Fixed floor: ~$4
- **≈ $355/month** (~$14/builder/month)

### Large team — 100 builders, ~20 LLM calls/builder/day
- LLM calls: 100 × 20 × 22 = **44,000/month** → × $0.041 ≈ **$1,800**
- Memory (thousands of sessions, long-term recall): ~$40
- Gateway (~150k tool calls/mo): ~$0.75
- Compute: ~$12; CloudWatch (higher log volume): ~$15; Fixed floor: ~$4
- **≈ $1,875/month** (~$19/builder/month)

**Takeaway:** cost scales almost linearly with LLM-call volume and lands around **$10–$19 per
builder per month**, dominated by Bedrock tokens. The AgentCore service layers
(Gateway/Memory/compute/storage) stay under ~3% of the bill at every size. Numbers scale with your
model choice and context size — a larger model or bigger contexts move the per-call figure up.

## 5. Optimization levers (highest impact first)

1. **Prefer the deterministic tier.** Knowledge recall via `companion_kb` and status-type queries
   cost **$0 in tokens** — they never call the model. Route anything answerable deterministically
   away from `ask_companion`. This is the engine's design default (Tier 1 before Tier 3); the lever
   is builder habit + prompt guidance.
2. **Control context size.** Token cost is linear in context. Ground answers from the KB
   (targeted retrieval) rather than stuffing large blobs into the prompt; keep session history
   bounded. Halving average context roughly halves the token bill.
3. **Prompt caching.** Sonnet-class cache reads are ~$0.30/1M vs ~$3.00/1M (10× cheaper). For the
   stable system/governance preamble that prefixes every call, caching cuts the input portion at
   scale — most impactful for the large-team tier.
4. **Right-size the model per task.** Use a cheaper (Haiku-class) model for classification/routing
   and reserve a Sonnet-class model for deep reasoning. The three-tier design also supports a local
   Ollama Tier-2 for zero-cloud-cost development.
5. **Set a Bedrock spend guardrail.** Put a CloudWatch billing alarm + an AWS Budget on the
   account; model expected session volume up front (the §4 table is the starting point).
6. **Trim CloudWatch retention.** Runtime/OTEL logs are a growing line at scale; set a log-group
   retention policy (e.g. 30 days) rather than never-expire (SD-4 in [`SECURITY.md`](SECURITY.md)).
7. **Memory hygiene.** `memory_event_expiry_days` (default 30) bounds short-term Events; let
   long-term memory store *summaries*, not raw turns.
8. **Disable what you don't use.** `enable_memory=false` removes the Memory layer;
   `deploy_gateway=false` uses the direct-runtime path (no Gateway tool-call charge + drops the M2M
   key/secret) for a single-team internal deployment. Both are supported flags.

## 6. Cost vs the governance model (no conflict)

The cost controls reinforce the governance design rather than fight it: the deterministic-first
tier (Tenet 4/7) is also the cheapest path; the fail-closed identity guard (ADR-0001) adds no cost;
and human-owned deployment (Tenet 1) means no runaway autonomous spend. The Tenet-12 "bounded
resource" ceilings are, among other things, a cost guardrail.

## 7. What this does NOT include

- Data egress beyond normal API responses (minimal for this workload).
- The identity **interceptor Lambda** (opt-in): a Lambda invocation per gateway tool call — at
  ~$0.20/1M requests + a few ms of 128 MB compute, it adds **well under $1/month** even at the
  large-team tier. Negligible; not broken out in §4.
- One-time/human costs: builder time, initial setup, Cognito (free tier covers typical MAU).
- Any fork-specific systems-of-record integration you add.

## Related resources

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — what each component does.
- [`../agentcore/terraform/README.md`](../agentcore/terraform/README.md) — the opt-in flags that move cost.
- [AWS Pricing Calculator](https://calculator.aws/) — build a precise estimate for your Region and usage.
