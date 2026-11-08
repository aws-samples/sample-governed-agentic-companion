<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Security-remediation review — CI vulnerability export 2026-10-10

The remediation bumps two dependency pins (PyJWT, mcp) across the AgentCore app requirements files,
suppresses one Gitleaks false positive inline on the repo's own password-in-URL *detector*, annotates
`.gitleaks.toml`, and de-collides the `leak_scan.sh` customer-pattern placeholders. Everything in the
SAST-lint and IaC categories is a verify-and-disposition against the already-remediated working tree
at the scanned commit `b7595a1`. The triage plan, the remediation report, and the recorded gates are
internally consistent with the CSV and with the actual diff.

Watch for: the s3_kb.tf TLS-deny the bar calls out as a "real fix" is **present and enforced in code
but predates this diff** (added in `64b5f67`, not added by this remediation) — the report correctly
labels it "REAL — already fixed" rather than claiming to add it, so the control's substance is met,
confirmed. Local gitleaks was not run (binary absent) and is honestly recorded as not-locally-verified,
likely acceptable since the detector regex is byte-unchanged and tests pass.

**Verdict**: APPROVED

## High-level view

The dependency pins are correct and consistent. PyJWT moves `2.13.0 → 2.15.1` in **both**
`agentcore/app/orchestrator/requirements.txt` (line 22) and `agentcore/app/mcp_runtime/requirements.txt`
(line 34) — same pin in both files — and `2.15.1` is the latest released 2.x that closes the entire
14-CVE PyJWT family the CSV flags (2.14.0 would leave CVE-2026-102274/-102275 open; 2.15.0 closes those;
2.15.1 is 2.15.0 plus a non-security fix). mcp moves `1.28.0 → 1.30.0` only in the mcp_runtime file (the
orchestrator carries no mcp pin), picking up the GHSA-qx49-fqc8-xw99 OAuth-leak fix on top of the session
fixes. No pin was downgraded; the comments were refreshed with the new CVE family. The report records a
clean `pip download` resolvability tail for both files (no `ResolutionImpossible`), and there is no
bedrock-agentcore 1.18.1 conflict.

The password-in-URL detection control was not weakened. The regex
`(?i)://[^/\s:@]{1,256}:[^/\s:@]{3,256}@` is byte-for-byte identical in the diff; the only change on that
line is a trailing `# gitleaks:allow`. No detection logic was removed. `.gitleaks.toml` gains an
explanatory note (no allowlist entry removed). This is suppression-not-weakening, done the way the bar
requires.

The S3 non-TLS deny is a real, enforced control: `aws_s3_bucket_policy.kb` denies `s3:*` on
`aws:SecureTransport=false`, and the `kb_logs` policy both TLS-gates its Allow and adds a matching deny.
It exists at the scanned commit and the report verifies it as a real fix rather than dispositioning the
finding away. The one honest caveat is that it was committed earlier (`64b5f67`), not added by this diff.

Every ACCEPTED IaC finding traces to a `docs/SECURITY.md` SD row (SD-3, SD-6, SD-8, SD-9, SD-10, SD-11,
all present) and carries a reviewed inline suppression (6 Access-Analyzer `kics-scan ignore-line`s citing
SD-8, the iam.tf exfiltration FALSE-POSITIVE ignore-line, interceptor notification `ignore-block`s, and
checkov skips citing SD-3/9/10). No blanket scanner disable; no over-broad skip.

Coverage is complete and the arithmetic checks out against the CSV. Parsing the CSV independently yields
exactly the report's figures: 108 data rows, 15 dismissed, 93 detected — SBoM 30, Trivy 28,
PSScriptAnalyzer 15, ShellCheck 3, kics 11, Checkov 5, Gitleaks 1. The disposition math (58 FIXED + 2
FALSE POSITIVE + 33 ACCEPTED = 93) ties out: 14 PyJWT CVEs × 4 occurrences = 56, plus mcp CVE-52869/-52870
= 58 FIXED. No row is silently dropped.

The recorded gates are present and green: pytest 169 passed, YAML validation PASS, leak_scan CLEAN,
governance integrity verified, terraform fmt/validate clean (no plan/apply — Tenet 1).

<details>
<summary>Issues (2)</summary>

1. **s3_kb.tf TLS-deny predates this diff (non-blocking)** — the bar frames the non-TLS deny as a fix
   this remediation adds; it is actually present in code since `64b5f67` and unchanged in the working
   tree. The report is honest about this ("REAL — already fixed"), the control is genuinely enforced on
   both the KB and log buckets, so the substance of the bar (a real control, not a paper disposition) is
   met. No action required.
2. **Local gitleaks not executed (non-blocking)** — the Gitleaks suppression was not confirmed by a local
   `gitleaks detect` run (binary absent); the report records this honestly and verifies indirectly (regex
   byte-unchanged, tests pass, standard inline directive). Will be confirmed by the next run of
   the CI secret-detection job. No blocking gap.

</details>

<details>
<summary>Details</summary>

### Dependency pins — PyJWT and mcp

Verified directly against the files, not just the report:

```
agentcore/app/orchestrator/requirements.txt:22:PyJWT[crypto]==2.15.1
agentcore/app/mcp_runtime/requirements.txt:34:PyJWT[crypto]==2.15.1
agentcore/app/mcp_runtime/requirements.txt:24:mcp==1.30.0
```

Both files land on the **same** PyJWT pin (2.15.1) — the consistency bar is met. mcp (1.30.0) is only in
the mcp_runtime file; the orchestrator file has no mcp pin, so there is nothing to reconcile there. Both
pins are **bumps, not downgrades** (2.13.0 → 2.15.1; 1.28.0 → 1.30.0). The inline comments were rewritten
to enumerate the new CVE family (CVE-2026-101917/-101918, -102265 through -102273, -103001 fixed by
2.14.0; -102274/-102275 by 2.15.0) and the mcp GHSA-qx49-fqc8-xw99 OAuth-leak fix at 1.30. The plan's
version selection matches: 2.14.0 is insufficient (leaves -102274/-102275), 2.15.1 is the latest released
2.x. (confirmed — pins and comments read from the files)

The report records a clean resolvability tail for both files on the real target (linux/arm64, py3.12):
`pyjwt-2.15.1-py3-none-any.whl` and `mcp-1.30.0-py3-none-any.whl` both resolve, no `ResolutionImpossible`,
exit 0, no conflict with `bedrock-agentcore==1.18.1`. Per the review instruction I did not re-run
`pip download`; the recorded tails are detailed and leave no articulable doubt. (confirmed from report;
pip not re-run by design)

### Password-in-URL detector — suppressed, not weakened

The diff on `agents/data_protection.py` is a single line and the compiled pattern is unchanged:

```
-    (re.compile(r"(?i)://[^/\s:@]{1,256}:[^/\s:@]{3,256}@"), "connection-string password",
+    (re.compile(r"(?i)://[^/\s:@]{1,256}:[^/\s:@]{3,256}@"), "connection-string password",  # gitleaks:allow
```

No detection logic removed, no bound relaxed — only a trailing allowlist annotation. `.gitleaks.toml`
gains a note explaining that the CI secret-detection job runs the default ruleset and does not consume the
repo-local path allowlist, so the inline directive is what suppresses the finding there; the existing
`paths` entries are retained for local `--config` runs. This is exactly the "suppress, do not weaken"
posture the bar requires. (confirmed from the diff)

### S3 non-TLS deny

`agentcore/terraform/s3_kb.tf` carries two real deny statements:

```
104: resource "aws_s3_bucket_policy" "kb"      → Sid "DenyInsecureTransport", aws:SecureTransport=false
217: resource "aws_s3_bucket_policy" "kb_logs" → TLS-gated Allow + Sid "DenyInsecureTransport" deny
```

The control is genuinely enforced on both buckets. `git log -S` shows it was introduced in `64b5f67`
(the initial Terraform stack), and `s3_kb.tf` is unmodified in the current working tree, so this
remediation did not add it. The bar's phrase "actually added (a real fix, not a disposition)" is about
ensuring the finding is met by a real control rather than waved away as "accepted"; that substance holds
— the report labels it "REAL — already fixed" and does not falsely claim authorship. Treated as
non-blocking. (confirmed via git history and file read)

### ACCEPTED IaC findings → SECURITY.md SD rows + inline suppressions

All six SD rows the report cites exist in `docs/SECURITY.md` (SD-3 SSE-S3 log bucket, SD-6 PUBLIC runtime
mode, SD-8 Access Analyzer opt-in, SD-9 Lambda code-signing, SD-10 S3 CRR, SD-11 KB-read exfiltration FP).
Inline suppressions are present and cite their rationale: six `kics-scan ignore-line`s for Access Analyzer
(one per `ecr.tf`/`iam.tf`/`guardrail.tf`/`s3_kb.tf`/`gateway.tf`/`runtimes.tf`, each referencing SD-8),
an iam.tf `ignore-line` marking the data-exfiltration finding a reviewed false positive (SD-11 scoping),
`ignore-block`s on the interceptor notification mis-attribution, and `checkov:skip` entries for
CKV_AWS_117/144/145/272 citing SD-6/10/3/9 plus a `.checkov.yaml` belt-and-braces copy. No blanket disable;
no over-broad skip. (confirmed from SECURITY.md and the .tf files)

### SAST-lint dispositions (Category D)

The 19 onboarding-script lint rows are dispositioned as already-fixed/FP against the working tree: the PS1
has no `Write-Host` (a `Write-Info` helper over `Write-Information`), already starts with a UTF-8 BOM, and
`Write-Info` takes a bound parameter (so PSUseProcessBlockForPipelineCommand is a correct FALSE POSITIVE,
not a silenced finding); the shell script already has `cd … || exit 1` (SC2164) and computes its prefix
before the append redirect (SC2094). These are dispositions of stale records, verified in the report; the
coder correctly declined to add a no-op process block, which would change the helper contract to no effect.
(confirmed from report; consistent with the plan's independent verification)

### leak_scan.sh placeholder de-collision

The staged `scripts/leak_scan.sh` change swaps the customer-pattern placeholders (which included
`\bACME\b|Example_All_CAs` and generic CodeA/B tokens) for obvious `YourCustomerCodeHere*` fill-ins that
do not collide with the repo's own example vocabulary (`Acme`/`ProjectFoo` in terraform.tfvars.example and
the data-protection tests) — the exact self-collision that failed the earlier pipeline run. The
`SECRET_PATTERNS` half (AKIA/ASIA, private-key headers, `aws_secret_access_key`) is unchanged, so this is a
false-positive de-collision rather than weakening a secret control. (confirmed from the diff)

### Coverage and gates

Independent CSV parse (`csv.DictReader`): 108 rows, 15 dismissed, 93 detected, with the per-scanner
breakdown and the 14-PyJWT-CVE-×4 + 2-mcp-CVE structure matching the report exactly. Disposition sum
58 + 2 + 33 = 93. Recorded gates: pytest 169 passed, YAML PASS, leak_scan CLEAN, governance integrity OK,
terraform fmt/validate clean. All present and green; none missing. (confirmed — CSV re-parsed; gate values
read from report, not re-run per instruction)

</details>

<details>
<summary>File map</summary>

- `agentcore/app/orchestrator/requirements.txt` — PyJWT 2.13.0 → 2.15.1; CVE comment refreshed.
- `agentcore/app/mcp_runtime/requirements.txt` — PyJWT 2.13.0 → 2.15.1; mcp 1.28.0 → 1.30.0; comments refreshed.
- `agents/data_protection.py` — appended `# gitleaks:allow` to the detector line (regex unchanged).
- `.gitleaks.toml` — explanatory note on inline suppression vs the default CI ruleset.
- `scripts/leak_scan.sh` (staged) — customer-pattern placeholders de-collided from repo example vocabulary.
- `docs/security/VULN-TRIAGE-2026-10-10.md`, `docs/security/VULN-REMEDIATION-2026-10-10.md` — plan + record.

Full diff: `git diff main` in the repository root.

</details>
