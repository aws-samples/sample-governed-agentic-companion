<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Source knowledge — teaching the companion about your application

A governed companion is only as useful as the knowledge it can ground answers in. This page
describes the **entry-point contract** the starter kit ships for pointing the companion at your
application's source code, docs, and infrastructure — and is honest about **what is wired vs.
what you wire**.

## What the kit ships (and what it doesn't)

- **Ships:** `sources.yaml.example` (repo root) — a manifest mapping named knowledge sources
  (application source, docs, infrastructure) to the specialists that consume them, with optional
  include/exclude globs and an optional AWS Transform (ATX)-style staging target per source.
- **Ships:** the *destination* — the curated **Tier-B overlay** in S3 (`kb/curated/overlay.json`),
  which the example engine already reads at runtime (`agents/kb_overlay.py`), and the deterministic
  **data-protection guard** (`agents/data_protection.py`) that every write must pass (Tenet 6).
- **Does NOT ship:** an ingestion *consumer*. The example engine has no code that reads
  `sources.yaml` and walks a repo. That is deliberate — a starter kit gives you the contract and
  the safe destination, not an opinionated ingestion pipeline you'd have to rip out. You wire the
  hook that fits your stack.

This mirrors how the kit treats memory: the infrastructure is ready (`enable_memory`), the contract
is defined, and a fork connects its own engine to it — no dead code pretending to be a feature.

## The manifest

Copy `sources.yaml.example` → `sources.yaml` (gitignored) and fill in paths (local or git URLs):

```yaml
application_source:
  path: "/repos/my-app"          # or a git URL
  include: ["**/*"]
  exclude: ["**/node_modules/**", "**/dist/**"]
  used_by: ["developer"]
```

Keep anything you commit customer-agnostic — the `.example` is tracked; your real `sources.yaml`
is gitignored precisely because it names real paths and bucket names.

## Two entry options for the ingestion hook

Both end at the same governed destination: sanitized knowledge in the human-reviewed Tier-B
overlay. Pick the one that fits the engagement.

### Option 1 — Offline / local ingestion (recommended starting point)

A fork adds a small ingestion script that:

1. reads `sources.yaml`;
2. walks each source's `include`/`exclude` globs;
3. **sanitizes every chunk through `agents/data_protection.py`** — secrets reject the write, PII
   and proprietary terms are redacted, operational config (ports, endpoints) is preserved;
4. writes the result as a Tier-B overlay entry and promotes it to `kb/curated/overlay.json` via the
   existing human-in-the-loop promotion path.

No new AWS services, offline and free, and the governance guarantees hold by construction because
the write goes through the same guard and the same reviewed overlay the engine already trusts.

### Option 2 — AWS Transform (ATX)-style staging

For teams that prefer a managed analysis service, each source can carry an optional `atx:` block
(`s3_bucket` / `s3_prefix`). A fork's hook stages the source bundle to that bucket for analysis,
then feeds the sanitized results back into the same Tier-B overlay. The destination and governance
are identical; only the *analysis* is offloaded. Uploading a bundle is a human-owned action
(Tenet 1) — the hook prepares it; a human runs the upload/job start.

## Governance guarantees (both options)

- **Tenet 6 (data governance):** nothing enters the knowledge base without passing
  `data_protection.py`. The shared KB stays customer-agnostic.
- **Tenet 1 (human-owned):** ingestion produces a *reviewable overlay*; a human promotes it. No
  agent mutates the live overlay unattended.
- **Tenet 4 (grounded reasoning):** ingested knowledge lands in Tier-B (overlay-eligible), never in
  the locked Tier-A constitution — so ingestion can enrich, never override, the governed core.

## Where to wire it

- Manifest: `sources.yaml` (root; gitignored).
- Guard to call on every chunk: `agents/data_protection.py` → `scrub_or_reject(...)`.
- Destination the engine reads: the Tier-B overlay at `kb/curated/overlay.json` (S3), configured by
  the Terraform stack (`agentcore/terraform/`, `GAC_KB_OVERLAY_KEY`).

## Related resources

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the hybrid Tier-A/Tier-B knowledge model.
- [`../SECURITY.md`](../SECURITY.md) — the data-protection guard (Tenet 6).
