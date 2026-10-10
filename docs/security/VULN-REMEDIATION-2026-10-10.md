<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Vulnerability remediation record — 2026-10-10

Companion to the triage plan `docs/security/VULN-TRIAGE-2026-10-10.md`. This document records
the **implemented** remediation and the **verification evidence** for every detected row in the
GitLab vulnerability export
`backup/personal_projects-alias_b-baljay-governed-agentic-companion_vulnerabilities_2026-10-10T0512.csv`.

Governed by `PRINCIPLES.md`: no environment was deployed/applied/mutated (Tenet 1 — no
`terraform plan`/`apply`), no customer-identifying data is present (Tenet 6), and **no security
control was weakened to silence a scanner** (Tenet 4).

## Detected-row accounting (source of truth: the CSV)

Parsed directly from the CSV (`csv.DictReader`):

- **Total data rows:** 108
- **Dismissed (out of scope — not remediated):** 15
- **Detected (in scope):** **93**

Detected rows by tool / scanner:

| Tool | Scanner | Rows |
|---|---|---:|
| dependency_scanning | GitLab SBoM | 30 |
| dependency_scanning | Trivy | 28 |
| sast | PSScriptAnalyzer | 15 |
| sast | ShellCheck | 3 |
| sast | kics | 11 |
| sast | Checkov | 5 |
| secret_detection | Gitleaks | 1 |
| **Total detected** | | **93** |

Disposition arithmetic (must cover all 93):

| Disposition | Rows | Where |
|---|---:|---|
| **FIXED** | 58 | Category A (PyJWT, 56) + Category B (mcp, 2) — dependency pins bumped |
| **FALSE POSITIVE** | 2 | Category C Gitleaks (1) + Category D PSUseProcessBlockForPipelineCommand (1) |
| **ACCEPTED / already-remediated** | 33 | Category D SAST-lint (17 remaining) + Category E IaC (16) |
| **Total** | **93** | ✓ 58 + 2 + 33 = 93 |

> "ACCEPTED / already-remediated" means the fix or disposition is **already present in the
> working tree at the scanned commit `b7595a1`**; these are stale GitLab records that a fresh
> default-branch scan auto-resolves. Each was re-verified in this pass (evidence below). No new
> `docs/SECURITY.md` SD rows were required — every IaC acceptance already maps to an existing SD
> row with a reviewed inline suppression.

---

## Category A — PyJWT 2.13.0 dependency CVEs (56 rows) — FIXED

14 distinct CVEs, each recorded 4× (GitLab SBoM + Trivy, across the orchestrator and mcp_runtime
`requirements.txt`) = 56 rows.

**Fix:** bumped `PyJWT[crypto]==2.13.0` → `PyJWT[crypto]==2.15.1` in **both**
`agentcore/app/orchestrator/requirements.txt` (line 22) and
`agentcore/app/mcp_runtime/requirements.txt` (line 34), and replaced the stale CVE comment
(`CVE-2026-32597 / -48526 / PyJWKClient SSRF-DoS`) with the full fixed-CVE family.

| CVE | Fixed in | Covered by 2.15.1? |
|---|---|---|
| CVE-2026-101917 (JWKS-refresh amplification) | 2.14.0 | yes |
| CVE-2026-101918 (RecursionError DoS) | 2.14.0 | yes |
| CVE-2026-102265 (nested-header RecursionError) | 2.14.0 (+2.15.0 hardening) | yes |
| CVE-2026-102266 (empty HMAC keys as JWK) | 2.14.0 | yes |
| CVE-2026-102267 (PyJWKClient follows redirects) | 2.14.0 | yes |
| CVE-2026-102268 (PEM-detection HMAC confusion) | 2.14.0 | yes |
| CVE-2026-102269 (non-canonical JWS segments) | 2.14.0 | yes |
| CVE-2026-102270 (is_pem_format ReDoS) | 2.14.0 | yes |
| CVE-2026-102271 (DER public key as HMAC) | 2.14.0 | yes |
| CVE-2026-102272 (BOM-prefixed key bypass) | 2.14.0 | yes |
| CVE-2026-102273 (public JWK as HMAC secret) | 2.14.0 | yes |
| CVE-2026-103001 (options-dict mutation claim bypass) | 2.14.0 | yes |
| CVE-2026-102274 (malformed RSA JWK DoS) | 2.15.0 | yes |
| CVE-2026-102275 (inconsistent OKP x/d JWK) | 2.15.0 | yes |

`2.14.0` would leave CVE-2026-102274 and CVE-2026-102275 open; `2.15.1` is the latest released 2.x
and covers the entire set. **Zero residual PyJWT CVE.** `bedrock-agentcore 1.18.1` does not
constrain PyJWT, so no build conflict (proof below).

## Category B — mcp dependency CVEs (2 rows) — FIXED

CVE-2026-52869 (session hijack) and CVE-2026-52870 (cross-session task access), SBoM-recorded
against mcp 1.23.0. The file was already at `1.28.0` (clears both), but the later OAuth-credential
leak **GHSA-qx49-fqc8-xw99** is fixed only in **1.30.0**.

**Fix:** bumped `mcp==1.28.0` → `mcp==1.30.0` in `agentcore/app/mcp_runtime/requirements.txt`
(line 24) and extended the inline comment to cite GHSA-qx49-fqc8-xw99. mcp appears in only the
mcp_runtime file (the orchestrator file has no `mcp` pin — nothing to reconcile). 1.30.0 satisfies
`bedrock-agentcore 1.18.1`'s `mcp<2.0.0,>=1.23.0` strands extra and stays on the 1.x line (2.x
renamed `FastMCP`→`MCPServer`). **Zero residual.**

Both requirements files end on the **same** PyJWT pin (2.15.1); mcp (1.30.0) is only in the
mcp_runtime file by design.

## Category C — Gitleaks "Password in URL" (1 row) — FALSE POSITIVE (suppressed inline)

`agents/data_protection.py` ~line 122 (the CSV points at the start of the comment block; the
regex is on line 127). This is the repo's own **connection-string password DETECTOR regex**
(`re.compile(r"(?i)://[^/\s:@]{1,256}:[^/\s:@]{3,256}@")`) — a security control that redacts
secrets, not a secret.

**Fix:** appended `# gitleaks:allow` to the detector line. The regex is **unchanged** — detection
behavior is intact (verified by `tests/test_data_protection.py`). Added a note to `.gitleaks.toml`
explaining that GitLab Secret-Detection runs the default Gitleaks ruleset (it does not consume the
repo-local path allowlist), so the inline annotation is what suppresses the finding there; the
existing `paths` allowlist still covers local `--config .gitleaks.toml` runs. No detector removed
or weakened.

## Category D — SAST lint on onboarding scripts (19 rows) — ALREADY FIXED / 1 FALSE POSITIVE

All 19 rows were re-verified against the working tree at the scanned commit. No code change was
required — the fixes are already present (stale GitLab records that auto-resolve on the next scan).
1 of the 19 is a false positive.

| CSV finding (count) | Verified state in working tree | Disposition |
|---|---|---|
| PSAvoidUsingWriteHost ×14 (`Install-CompanionFrontDoor.ps1`) | File contains **no `Write-Host`**; a `Write-Info` helper wraps `Write-Information -InformationAction Continue` (a redirectable stream) | ACCEPTED — already fixed; stale record |
| PSUseBOMForUnicodeEncodedFile ×1 (PS1 line 1) | File **begins with a UTF-8 BOM** — byte check: `efbbbf3c230a…` (`EF BB BF` + `<#`) | ACCEPTED — already fixed; stale record |
| PSUseProcessBlockForPipelineCommand ×1 (PS1) | `Write-Info` takes a bound `[string]$Message` **parameter**, not pipeline input, so a `process {}` block does not apply | FALSE POSITIVE — no code change (adding a process block for a non-pipeline helper would be incorrect) |
| ShellCheck SC2164 ×1 (`install-companion-frontdoor.sh` line 35) | Line 35 is already `cd "$REPO_ROOT" || exit 1` | ACCEPTED — already fixed; stale record |
| ShellCheck SC2094 ×2 (sh) | `install_claude_pointer` computes the newline prefix **before** opening the append redirect, with an explicit "never read and write … in the same block (ShellCheck SC2094)" comment | ACCEPTED — already fixed; stale record |

> I did **not** add a BOM or convert `Write-*` calls (they are already correct), and I did **not**
> add a process block to `Write-Info`, because it takes a parameter rather than pipeline input —
> adding one would change the helper's contract to no effect. These are dispositions, not silencing.

## Category E — Terraform IaC (16 rows) — ALREADY DISPOSITIONED

All 16 IaC rows were re-verified in the working tree. Each maps to an existing `docs/SECURITY.md`
SD row (or is a verified fix) and carries a reviewed inline suppression. No blanket skips; no new
SD rows required.

| CSV finding (file) | Disposition | Verified in working tree |
|---|---|---|
| kics "S3 Bucket Policy Accepts HTTP Requests" (`s3_kb.tf`) | **REAL — already fixed** | `aws_s3_bucket_policy.kb` has a `DenyInsecureTransport` statement on `aws:SecureTransport=false`; `kb_logs` policy both TLS-gates its Allow and adds a `DenyInsecureTransport` |
| kics "IAM policy allows for data exfiltration" ×2 (`iam.tf`) | **SD-11** (FP — `s3:GetObject`/`PutObject` scoped to one bucket + `kb/` prefix) | inline `kics-scan ignore-line` + SD-11 rationale |
| kics "IAM Access Analyzer Not Enabled" ×6 (`ecr.tf`, `iam.tf`, `guardrail.tf`, `s3_kb.tf`, `gateway.tf`, `runtimes.tf`) | **SD-8** (landing-zone baseline; opt-in via `enable_access_analyzer`) | one `kics-scan ignore-line` + SD-8 ref per file |
| kics "S3 bucket notifications disabled" ×2 (`interceptor.tf`) | mis-attributed to the Lambda/SQS pair; KB buckets enable EventBridge | `kics-scan ignore-block` with rationale |
| Checkov CKV_AWS_144 S3 CRR ×2 (`s3_kb.tf`) | **SD-10** (CRR disproportionate for small single-region KB) | inline `checkov:skip=CKV_AWS_144` on both buckets + `.checkov.yaml` |
| Checkov CKV_AWS_145 S3 KMS-by-default ×1 (`s3_kb.tf`) | **SD-3** (access-log bucket SSE-S3 by design) | inline `checkov:skip=CKV_AWS_145` + `.checkov.yaml` |
| Checkov CKV_AWS_117 Lambda-in-VPC ×1 (`interceptor.tf`) | **SD-6** (interceptor makes no data-plane calls) | inline `checkov:skip=CKV_AWS_117` + `.checkov.yaml` |
| Checkov CKV_AWS_272 Lambda code-signing ×1 (`interceptor.tf`) | **SD-9** (in-repo single-file source pinned by `source_code_hash`) | inline `checkov:skip=CKV_AWS_272` + `.checkov.yaml` |

`docs/SECURITY.md` remains the single source of truth; no SD rows were added or changed because
nothing newly accepted was introduced.

---

## Files modified in this remediation

1. `agentcore/app/orchestrator/requirements.txt` — PyJWT `2.13.0` → `2.15.1`; CVE comment refreshed.
2. `agentcore/app/mcp_runtime/requirements.txt` — PyJWT `2.13.0` → `2.15.1`; mcp `1.28.0` → `1.30.0`; both CVE comments refreshed.
3. `agents/data_protection.py` — appended `# gitleaks:allow` to the connection-string password **detector** line (regex unchanged).
4. `.gitleaks.toml` — added an explanatory note on the inline suppression vs the default GitLab ruleset (no allowlist entry removed).
5. `docs/security/VULN-REMEDIATION-2026-10-10.md` — this report.

(`scripts/leak_scan.sh` was staged by the orchestrator before this step and was left as-is.)

---

## Verification evidence (run from the repo root on 2026-10-10)

### 1. Dependency resolvability on the real runtime target (linux/arm64, py3.12)

Command (per file):
```
pip download --python-version 3.12 --platform manylinux2014_aarch64 --only-binary=:all: \
  -r <file> -d /tmp/whl_check_<orch|mcp>
```

**Orchestrator — resolves cleanly (tail):**
```
Saved /private/tmp/whl_check_orch/uvicorn-0.54.0-py3-none-any.whl
Saved /private/tmp/whl_check_orch/cryptography-50.0.2-cp311-abi3-manylinux2014_aarch64...whl
Successfully downloaded bedrock-agentcore aws-opentelemetry-distro opentelemetry-api PyJWT
  pyyaml boto3 botocore ... cryptography cffi ... uvicorn click h11 websockets pycparser
```
Resolved wheel: `pyjwt-2.15.1-py3-none-any.whl`. No `ResolutionImpossible`, exit 0.

**mcp_runtime — resolves cleanly (tail):**
```
Saved /private/tmp/whl_check_mcp/starlette-1.7.0-py3-none-any.whl
Saved /private/tmp/whl_check_mcp/websockets-17.2-cp312-cp312-manylinux2014_aarch64...whl
Successfully downloaded bedrock-agentcore aws-opentelemetry-distro opentelemetry-api mcp
  uvicorn PyJWT pyyaml boto3 botocore ... httpx httpcore ... sse-starlette starlette ...
```
Resolved wheels: `mcp-1.30.0-py3-none-any.whl`, `pyjwt-2.15.1-py3-none-any.whl`. No conflict with
`bedrock-agentcore==1.18.1`, exit 0.

Pin confirmation:
```
agentcore/app/orchestrator/requirements.txt:22:PyJWT[crypto]==2.15.1
agentcore/app/mcp_runtime/requirements.txt:34:PyJWT[crypto]==2.15.1
agentcore/app/mcp_runtime/requirements.txt:24:mcp==1.30.0
```

### 2. Test suite
```
.venv/bin/python -m pytest -q
→ 169 passed in 0.41s
```

### 3. YAML validation
```
bash scripts/validate_yaml.sh
→ YAML VALIDATION: PASS — all tracked YAML files parse.
```

### 4. Leak scan (uses the orchestrator's already-staged leak_scan.sh as-is)
```
bash scripts/leak_scan.sh
→ LEAK SCAN: CLEAN — no known customer-identifying or secret-shaped tokens found.
```

### 5. Governance integrity
```
./run.sh status
→ ✅ Governance: PRINCIPLES.md integrity verified — no tampering detected.
```

### 6. Terraform (fmt + validate only; no plan/apply — Tenet 1)
```
cd agentcore/terraform
terraform fmt -check -recursive   → exit 0 (no formatting diff)
terraform validate                → Success! The configuration is valid.
```

### 7. Gitleaks (local binary not installed)
`gitleaks` is not on this machine's PATH, so the local `gitleaks detect --config .gitleaks.toml`
run **could not be executed here**. The suppression correctness is verified indirectly: the
detector regex is byte-for-byte unchanged (only a trailing `# gitleaks:allow` comment was added),
`tests/test_data_protection.py` passes (redaction behavior intact), and `# gitleaks:allow` is the
standard inline-allowlist directive the default GitLab Gitleaks ruleset honours. This is stated
honestly as **not locally verified**; it will be confirmed by the next GitLab Secret-Detection run.

---

## Residual risk

- **Zero residual** on the dependency CVEs (PyJWT 2.15.1 and mcp 1.30.0 cover every flagged id with
  a released fix).
- The Gitleaks row is a confirmed false positive on a detection control; suppressed without
  weakening the control. Local gitleaks execution was unavailable (noted above).
- Categories D and E are already remediated/dispositioned in the working tree; the GitLab records
  are stale and auto-resolve on the next default-branch scan. If any D/E row persists post-scan,
  dismiss it in GitLab citing the SD row / FP rationale here — do **not** add a blanket disable.
