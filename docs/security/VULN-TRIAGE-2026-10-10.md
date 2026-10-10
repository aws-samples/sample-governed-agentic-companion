<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Vulnerability triage and remediation plan — 2026-10-10

Source of truth: `backup/personal_projects-alias_b-baljay-governed-agentic-companion_vulnerabilities_2026-10-10T0512.csv`
(GitLab vulnerability export, 108 data rows). Scanned commit per the pipeline log: `b7595a1`
(current `HEAD`/`origin/main`). This is the design/triage phase — **no source is edited here**.

Governed by `PRINCIPLES.md`: do not deploy/apply/mutate any environment (Tenet 1 — no
`terraform plan`/`apply`), no customer-identifying data in files or commit messages (Tenet 6),
and no security control weakened to silence a scanner.

## 1. Detected-row count and grouping

- **Total rows:** 108
- **Dismissed (ignore):** 15
- **Detected (in scope):** **93**

| Category | Rows | Scanners | Disposition |
|---|---:|---|---|
| A. PyJWT 2.13.0 dependency CVEs | 56 | GitLab SBoM + Trivy (both files × 14 CVEs ≈ 28 each) | **FIX** — bump pin |
| B. mcp 1.x dependency CVEs | 2 | GitLab SBoM | **FIX** — bump pin |
| C. Gitleaks "Password in URL" (false positive) | 1 | Gitleaks | **SUPPRESS** inline (real detector regex) |
| D. SAST lint — onboarding scripts (PS1 + sh) | 19 | PSScriptAnalyzer (16) + ShellCheck (3) | **ALREADY FIXED in `b7595a1`** / 1 false positive |
| E. IaC — Terraform (kics + Checkov) | 15 | kics (11) + Checkov (5), incl. 5 `unknown` | **ALREADY DISPOSITIONED in `b7595a1`** |
| **Total detected** | **93** | | |

> Row-count note: the per-CVE PyJWT rows are duplicated across two scanners (SBoM + Trivy) and two
> requirements files, which is why category A is the bulk. The exact detected rows were enumerated
> by parsing the CSV; nothing was dropped. See the appendix for the full CVE list.

### The central finding of this triage

The scan is **largely stale**. `HEAD` equals the scanned commit `b7595a1` ("feat: v2 uplift + CSR
remediation + supply-chain hygiene"), and categories **D and E are already remediated or
dispositioned in that very commit** — the GitLab vulnerability *records* persist from an earlier
pipeline run on pre-remediation code and auto-resolve only when a fresh default-branch scan runs.
The **genuinely live, code-change-required** work is narrow:

1. **PyJWT** is still pinned at the vulnerable `2.13.0` in both requirements files (category A).
2. **mcp** is still pinned at `1.28.0` in the mcp_runtime file (category B).
3. The **Gitleaks** false positive needs an *inline* allowlist because GitLab Secret-Detection's
   default Gitleaks ruleset does not consume the repo `.gitleaks.toml` path allowlist (category C).

Everything in D and E is confirmed present in the working tree below; the action there is **verify
+ let the next scan auto-resolve**, not re-fix.

---

## 2. Resolved version pins (with released-changelog / PyPI / advisory evidence)

### PyJWT → `2.15.1`  (chosen pin)

The CSV flags **14 distinct PyJWT CVEs** against `2.13.0` (not the 6 in the brief). The orchestrator's
candidate `2.14.0` is **insufficient**: three of the flagged CVEs are only fixed in `2.15.0`. The
latest released 2.x is `2.15.1` (PyPI `upload_time 2026-09-28`), which contains every `2.14.0` and
`2.15.0` security fix. Pin **both** files to `2.15.1`.

Evidence (official PyJWT `CHANGELOG.rst` on `master`, plus the GitHub Advisory API and NVD/vendor DBs;
content rephrased for licensing compliance):

| CVE | What it is | Fixed in | Evidence |
|---|---|---|---|
| CVE-2026-102266 | PyJWK accepts empty HMAC keys | 2.14.0 | CHANGELOG v2.14.0 Security "Reject empty HMAC keys when represented as JWKs" (GHSA-pxh4-856f-4h89); SoCRadar "fixed in 2.14" |
| CVE-2026-102267 | PyJWKClient follows redirects when fetching JWKS | 2.14.0 | CHANGELOG v2.14.0 "Reject automatic redirects when PyJWKClient fetches a JWKS" (GHSA-9v7f-9g4p-ffgj); Docker Scout "first release containing the fix is 2.14.0" |
| CVE-2026-102268 | Asymmetric-PEM detection bypass (HMAC confusion via PEM) | 2.14.0 | CHANGELOG v2.14.0 HMAC-hardening (GHSA-p4g4-x82p-q773 / -r6x4-923q-g947); vuldb "Avant la version 2.14.0, `is_pem_format`…" |
| CVE-2026-102271 | Public keys in DER form accepted as HMAC | 2.14.0 | CHANGELOG v2.14.0 "…DER… input" hardening |
| CVE-2026-102272 | BOM-prefixed key bypass (token forgery) | 2.14.0 | CHANGELOG v2.14.0 "…BOM-prefixed… input" hardening |
| CVE-2026-102273 | Public JWK containers accepted as HMAC secrets | 2.14.0 | CHANGELOG v2.14.0 HMAC-via-JWK/JWKS hardening |
| CVE-2026-102269 | Non-canonical signature segments | 2.14.0 | CHANGELOG v2.14.0 "Enforce compact JWS encoding rules during decoding" (GHSA-hxm8-2xgr-2p9m) |
| CVE-2026-102270 | ReDoS in `is_pem_format` | 2.14.0 | fixed with the same `is_pem_format` hardening path (vuldb ties `is_pem_format` to the pre-2.14.0 code) |
| CVE-2026-101917 | PyJWKClient JWKS-refresh amplification (incomplete fix of CVE-2026-48524) | 2.14.0 | CHANGELOG v2.14.0 "Limit repeated JWKS refreshes caused by unknown key IDs" (GHSA-2gx3-rcp4-g85q) |
| CVE-2026-101918 | Unauthenticated RecursionError DoS (malformed JWS/JWK) | 2.14.0 | CHANGELOG v2.14.0 "Handle deeply nested and malformed JWS/JWK input without uncaught recursion" (GHSA-8wjv-2p76-3863 / -w6j9-cwv2-h6wq) |
| CVE-2026-102265 | Uncaught RecursionError in `jwt.decode()` on nested header | **2.14.0** (initial), hardened again in 2.15.0 | SoCRadar "fixed in 2.14"; CHANGELOG v2.15.0 also "Wrap recursion errors from deeply nested JWT payloads in DecodeError". 2.15.1 covers both. |
| CVE-2026-103001 | `decode()` options-dict mutation → claim-check bypass | **2.14.0** | GitHub Advisory API GHSA-gvp8-978c-rx2q `vulnerable_version_range = ">= 2.11.0, <= 2.13.0"` (so 2.14.0+ is NOT vulnerable); NVD links fix commit `0c87c8c`, present in 2.14.0+. (`first_patched` field is unset upstream, but the `<= 2.13.0` upper bound is authoritative.) |
| CVE-2026-102274 | Malformed RSA JWK aborts JWK-set parsing (DoS) | **2.15.0** | CHANGELOG v2.15.0 "Treat malformed JWK Set members as unusable keys… A single bad entry no longer fails an otherwise usable JWK Set" |
| CVE-2026-102275 | Inconsistent OKP x/d JWKs accepted | **2.15.0** | Ubuntu CVE DB "From 2.1.0 until 2.15.0… OKPAlgorithm.from_jwk… does not compare the public key derived from d"; CHANGELOG v2.15.0 "Reject OKP JWK private keys when their public x component does not match the private d"; SoCRadar "fixed in version 2.15" |

**Conclusion:** `2.14.0` leaves CVE-2026-102274 and CVE-2026-102275 unfixed. `2.15.0` fixes those plus
the extra recursion hardening; `2.15.1` is `2.15.0` + a non-security base64-padding fix and is the
latest released 2.x. **Pin `PyJWT[crypto]==2.15.1`.** No residual PyJWT CVE — every flagged id has a
released fix at or below 2.15.1.

### mcp → `1.30.0`  (chosen pin)

The CSV flags mcp CVE-2026-52869 (session hijack) and CVE-2026-52870 (cross-session task access)
against the SBoM-recorded `1.23.0`. The **current pin in the file is already `1.28.0`** (confirmed by
reading `agentcore/app/mcp_runtime/requirements.txt`), which clears those two. However a **separate,
later OAuth-credential-leak flaw (GHSA-qx49-fqc8-xw99)** is fixed only in **1.30.0** (siteguarding /
dailysecurityreview: "the fix landed in 1.30" / "affects versions prior to 1.30"). PyPI confirms the
latest released 1.x is **`1.30.0`** (2.x exists — `2.3.0` — but renames `FastMCP`→`MCPServer`, so stay
on the 1.x line per the existing pin comment). **Pin `mcp==1.30.0`** in the mcp_runtime file.

> The orchestrator requirements file has **no `mcp` pin** (confirmed) — mcp is only a dependency of the
> MCP runtime. There is nothing to reconcile on the orchestrator side; only one file carries mcp.

### Build-constraint check (no conflict expected)

`bedrock-agentcore==1.18.1` (the floor) declares, per its PyPI metadata:
`boto3>=1.43.31`, `botocore>=1.43.31`, `uvicorn>=0.34.2`, and `mcp<2.0.0,>=1.23.0` **only under the
`strands-agents` extra** (not installed here). Therefore:

- **PyJWT 2.15.1** — bedrock-agentcore does **not** constrain PyJWT at all → no conflict.
- **mcp 1.30.0** — satisfies `<2.0.0,>=1.23.0` → no conflict even if the extra were active.
- boto3/botocore `1.43.111`, uvicorn `0.54.0` already satisfy the floors → leave unchanged.

The implementer MUST still re-verify resolvability on the real target (linux/arm64, py3.12):

```
pip download --python-version 3.12 --platform manylinux2014_aarch64 --only-binary=:all: \
  -r <file> -d /tmp/whl_check
```

If PyJWT 2.15.1 or mcp 1.30.0 conflicts with `bedrock-agentcore 1.18.1`, **STOP and report the exact
pip error** — do not silently downgrade a security fix (Tenet 4 / brief hard constraint).

---

## 3. Files to edit (the live work)

- [ ] **1. Bump PyJWT in the orchestrator app deps.**
      Change `PyJWT[crypto]==2.13.0` → `PyJWT[crypto]==2.15.1`; update the inline CVE note to list
      the 2.15.1 floor and the CVE family it closes.
      Files: `agentcore/app/orchestrator/requirements.txt` (currently line 16).
      Verify: `pip download --python-version 3.12 --platform manylinux2014_aarch64 --only-binary=:all: -r agentcore/app/orchestrator/requirements.txt -d /tmp/whl_check` resolves with no error.

- [ ] **2. Bump PyJWT + mcp in the mcp_runtime app deps.**
      Change `PyJWT[crypto]==2.13.0` → `PyJWT[crypto]==2.15.1` and `mcp==1.28.0` → `mcp==1.30.0`;
      update both inline CVE notes (PyJWT floor 2.15.1; mcp floor 1.30.0 adds GHSA-qx49-fqc8-xw99
      OAuth-credential-leak fix on top of the 1.27/1.28 session/WS fixes). Keep the `< 2` ceiling note.
      Files: `agentcore/app/mcp_runtime/requirements.txt` (PyJWT line 26, mcp line 22).
      Verify: `pip download … -r agentcore/app/mcp_runtime/requirements.txt -d /tmp/whl_check` resolves
      with no error; `grep -n 'PyJWT\|^mcp==' agentcore/app/mcp_runtime/requirements.txt` shows the new pins.

- [ ] **3. Suppress the Gitleaks "Password in URL" false positive inline.**
      Append `  # gitleaks:allow` to the end of the `connection-string password` detector line
      (`agents/data_protection.py`, the `re.compile(r"(?i)://[^/\s:@]{1,256}:[^/\s:@]{3,256}@")` tuple,
      currently **line 127**; the CSV's "line 122" points at the start of its comment block). Do **NOT**
      alter the regex. Also add a one-line comment in `.gitleaks.toml` noting that GitLab
      Secret-Detection runs the default Gitleaks ruleset and the inline annotation is what suppresses
      the finding there (the existing `paths` allowlist only applies to a local `--config .gitleaks.toml`
      run). Do not remove the existing `.gitleaks.toml` entry.
      Files: `agents/data_protection.py`, `.gitleaks.toml`.
      Verify: `.venv/bin/python -m pytest -q tests/test_data_protection.py` still passes (the detector is
      unchanged, so redaction behavior is intact); `gitleaks detect --no-git --config .gitleaks.toml`
      reports no finding on the file.

- [ ] **4. (Confirmation only) Verify no behavior change and run the full suite.**
      No code besides the three edits above changes.
      Files: none.
      Verify: `.venv/bin/python -m pytest -q` passes; `./run.sh status` reports governance integrity OK.

---

## 4. Findings ACCEPTED / already-dispositioned (verify, do not re-fix)

Each of the following was confirmed **present in the current working tree (`b7595a1`)** during triage.
They are stale GitLab records that a fresh default-branch scan will auto-resolve. No new SD rows are
required — every IaC acceptance already maps to an existing `docs/SECURITY.md` row and carries a
reviewed inline suppression. **`docs/SECURITY.md` remains the single source of truth.**

### Category D — SAST lint on onboarding scripts (already fixed in `b7595a1`)

| CSV finding | Current state (verified) | Action |
|---|---|---|
| PSAvoidUsingWriteHost ×14 (PS1 lines 32,33,48,64,68,75,81,84,86,93,97,98,99) | File contains **no `Write-Host`**; a `Write-Info` helper wraps `Write-Information -InformationAction Continue` (redirectable stream) | None — stale; auto-resolves on next scan |
| PSUseBOMForUnicodeEncodedFile (PS1 line 1) | File **already starts with a UTF-8 BOM** (`EF BB BF`, verified) | None — stale |
| PSUseProcessBlockForPipelineCommand (PS1 line 30) | `Write-Info` takes a bound `[string]$Message` **parameter**, not pipeline input, so a `process {}` block is not applicable — **false positive** | Disposition as FP (no code change); if the analyzer insists, add `[CmdletBinding()]` is unnecessary — document as FP |
| ShellCheck SC2164 (sh line 35, `cd` without guard) | Line 35 is already `cd "$REPO_ROOT" || exit 1` | None — stale |
| ShellCheck SC2094 (sh lines 86,90, read+write same file) | `install_claude_pointer` already computes the newline prefix **before** opening the append redirect, with an explicit "never read and write in the same block (ShellCheck SC2094)" comment | None — stale |

### Category E — Terraform IaC (already dispositioned in `b7595a1`)

| CSV finding (file:line) | Mapped disposition | Verified present |
|---|---|---|
| kics "IAM policy allows for data exfiltration" `iam.tf:139` (KBObjectRead) | **SD-11** false positive — `s3:GetObject` scoped to one bucket + `kb/` prefix | inline `kics-scan ignore-line` + SD-11 rationale above the `KBObjectRead` statement |
| kics same, `iam.tf:150` (KBObjectWrite) | Same scoping rationale (SD-11) applies to the adjacent `s3:PutObject` write statement | The ignore-line comment precedes the read; **recommend extending the SD-11 note to explicitly cover the write grant** so both statements trace cleanly (doc-only tweak, optional) |
| kics "S3 Bucket Policy Accepts HTTP Requests" `s3_kb.tf:210` | **REAL per brief — but already fixed.** `aws_s3_bucket_policy.kb` (line 104) has `DenyInsecureTransport` on `aws:SecureTransport=false` (line 117); the `kb_logs` policy denies non-TLS (line 246) and TLS-gates its Allow (line 233) | Both TLS-deny statements verified present → no new work |
| kics "IAM Access Analyzer Not Enabled" (info) in `ecr.tf:16`, `iam.tf:33`, `guardrail.tf:23`, `s3_kb.tf:8`, `gateway.tf:53`, `runtimes.tf:9` | **SD-8** (landing-zone owns it; opt-in via `enable_access_analyzer`) | each of the 6 files carries exactly one `kics-scan ignore-line` + SD-8 reference (verified count) |
| kics "S3 bucket notifications disabled" (info) `interceptor.tf:105,144` | mis-attributed to the Lambda/SQS pair; real KB buckets DO enable EventBridge | `kics-scan ignore-block` with rationale above `aws_lambda_function.identity_interceptor` |
| Checkov CKV_AWS_144 S3 cross-region replication `s3_kb.tf:8,152` | **SD-10** | inline `checkov:skip=CKV_AWS_144` on both buckets + repo-level `.checkov.yaml` |
| Checkov CKV_AWS_145 S3 KMS-by-default `s3_kb.tf:152` | **SD-3** (access-log bucket is SSE-S3 by design) | inline `checkov:skip=CKV_AWS_145` + `.checkov.yaml` |
| Checkov CKV_AWS_117 Lambda-in-VPC `interceptor.tf:105` | **SD-6** (interceptor makes no data-plane calls) | inline `checkov:skip=CKV_AWS_117` + `.checkov.yaml` + rationale block |
| Checkov CKV_AWS_272 Lambda code-signing `interceptor.tf:105` | **SD-9** (in-repo single-file source pinned by `source_code_hash`) | inline `checkov:skip=CKV_AWS_272` + `.checkov.yaml` |

> If a future scan still reports any Category D/E row after a fresh run on `main`, dismiss it in
> GitLab with a comment citing the SD row / FP rationale above. Do **not** add a blanket scanner
> disable and do **not** weaken any detector or control (Tenet 4; brief hard constraint).

---

## 5. Verification commands (run after the Section 3 edits)

```bash
# 1. Dependency resolvability on the real runtime target (linux/arm64, py3.12). STOP on any error.
pip download --python-version 3.12 --platform manylinux2014_aarch64 --only-binary=:all: \
  -r agentcore/app/orchestrator/requirements.txt -d /tmp/whl_check_orch
pip download --python-version 3.12 --platform manylinux2014_aarch64 --only-binary=:all: \
  -r agentcore/app/mcp_runtime/requirements.txt   -d /tmp/whl_check_mcp

# 2. Pins are what we intend.
grep -n 'PyJWT' agentcore/app/orchestrator/requirements.txt agentcore/app/mcp_runtime/requirements.txt
grep -n '^mcp==' agentcore/app/mcp_runtime/requirements.txt

# 3. Detector behavior unchanged + full governed suite green (must pass before any commit).
.venv/bin/python -m pytest -q tests/test_data_protection.py
.venv/bin/python -m pytest -q
./run.sh status

# 4. Secret scan is clean (false positive now annotated).
gitleaks detect --no-git --config .gitleaks.toml --redact
```

Expected outcome: both `pip download` resolves succeed; grep shows `PyJWT[crypto]==2.15.1` in both
files, `mcp==1.30.0` in the mcp_runtime file; the full pytest suite passes; `./run.sh status` reports
governance integrity intact; gitleaks reports no finding.

> **Do NOT run** `terraform plan`/`apply` or any AWS mutation (Tenet 1). IaC changes in this plan are
> doc/disposition confirmations only; nothing in Section 3 touches `.tf` files.

---

## Appendix — full detected PyJWT/mcp CVE set (from the CSV)

PyJWT 2.13.0 (both `requirements.txt`): CVE-2026-101917, -101918, -102265, -102266, -102267, -102268,
-102269, -102270, -102271, -102272, -102273, -102274, -102275, -103001. All fixed at or below PyJWT
2.15.1 (see Section 2) — **zero residual**.

mcp (mcp_runtime `requirements.txt`): CVE-2026-52869, CVE-2026-52870 (SBoM recorded against 1.23.0;
current pin 1.28.0 already clears them). The later GHSA-qx49-fqc8-xw99 OAuth-credential-leak flaw is
the reason to go to 1.30.0. **Zero residual** at mcp 1.30.0.

_Content from external advisories/changelogs was rephrased and summarized for compliance with
licensing restrictions; see linked sources inline in Section 2._
