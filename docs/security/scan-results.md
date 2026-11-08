<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Security scan results (kits 3.3.1, 3.4.0 and 3.4.1)

## The standard gate: `scripts/security_gate.sh`

[`scripts/security_gate.sh`](../../scripts/security_gate.sh) runs every scan on this page with the
pinned versions below and exits 1 if any stage reports any finding. There is no severity
threshold anywhere: a finding at any level fails the gate unless it carries an inline, justified
suppression listed on this page, or (image scanners only) matches an entry in
[`security/accepted-residuals.yaml`](../../security/accepted-residuals.yaml) on vulnerability id,
package AND installed version, with a `review_by` date that has not passed.

```bash
./scripts/security_gate.sh --require-icons   # full gate before a release (needs Docker and one staged icons zip)
./scripts/security_gate.sh                   # same; skips the icons images when no zip is staged
./scripts/security_gate.sh --skip-images     # no image builds or image scans
./scripts/security_gate.sh --static          # no Docker (CI job security-gate-static); CI runs KICS as job kics
```

Stages: tracked-junk check (no lock, state, plan, zip, icon, `.audit`, `outputs` or `.env` file
is tracked), relative-link check over every tracked `*.md`, Checkov (each Terraform root and
module, plus dockerfile, secrets and github_actions), Bandit at every level, pip-audit
on every tracked `requirements*.txt`, detect-secrets, the `review_by` dates of the accepted
residuals, KICS over the whole tracked tree, local `linux/arm64` builds of both runtime images
(with and without the icons zip), Trivy and Grype on each image, and Trivy over the resvg-py
wheel SBOM. It builds images locally only: it never pushes, never logs in to a registry and never
runs Terraform. Set `GAC_SCAN_WORK=<dir>` to keep the logs and raw reports.

What pip-audit covers: `pip-audit -r` resolves each requirements file with the scan venv's host
interpreter, not with the images' Python 3.15 on `linux/arm64`, so its transitive set can differ
from what the images install. The installed transitive packages are covered by Trivy and Grype on
the built images.

For forks: the gate fails once an accepted residual's `review_by` date has passed, and CI runs the
same check (`security-gate-static`). Re-review the residuals and record a new `review_by`, or
re-pin the base (see "Re-pin trigger and procedure" below), before that date, or every pipeline
fails.

## Tools and versions

| Tool | Version |
|---|---|
| Checkov | 3.4.0 |
| Bandit | 1.9.4 |
| pip-audit | 2.10.1 |
| detect-secrets | 1.5.0 |
| kics (container) | `checkmarx/kics:v2.2.0` (`sha256:ef29dbac14b046b1ef65f3280646112e927a61781c5f01bca1aeb723e0b96003`) <!-- pragma: allowlist secret --> |
| Trivy (container) | `aquasec/trivy:0.76.1` (`sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`) <!-- pragma: allowlist secret --> |
| Grype (container) | `anchore/grype:v0.121.0` (`sha256:621d56d1d68106b787022c81546f7e0a36202a25d1239f0c20195fab8c16f7ea`) <!-- pragma: allowlist secret --> |

## Commands (what the gate runs, expanded)

```bash
python3 -m venv /tmp/gac-scan-venv
/tmp/gac-scan-venv/bin/pip install checkov==3.4.0 bandit==1.9.4 pip-audit==2.10.1 detect-secrets==1.5.0

# Checkov: the companion stack (recurses into modules/ and platform/), then each root and module.
for d in agentcore/terraform agentcore/terraform/platform agentcore/terraform/modules/private-network \
         agentcore/terraform/modules/shared-identity agentcore/terraform/modules/backend-bootstrap; do
  checkov -d "$d" --framework terraform --compact --skip-download --skip-path '\.tftest\.hcl$'
done
checkov -d . --framework dockerfile,secrets,github_actions --compact --skip-download

# Bandit: every severity and confidence level.
bandit -r agents frontdoor agentcore scripts integrations plugin

# pip-audit: every tracked requirements file.
for r in $(git ls-files '*requirements*.txt'); do pip-audit -r "$r"; done

# detect-secrets: run inside the copy of the tracked files; the integrity ledger is excluded (D7).
detect-secrets scan --all-files --exclude-files '^governance/integrity\.yaml$'

# KICS over the whole tracked tree, from the pinned image. Any result fails.
K=checkmarx/kics@sha256:ef29dbac14b046b1ef65f3280646112e927a61781c5f01bca1aeb723e0b96003  # pragma: allowlist secret
docker run --rm -v "$COPY":/path:ro -v /tmp/gac-kics:/out "$K" scan -p /path -o /out --report-formats json --no-progress

# Trivy and Grype over the locally built images (built with --pull, with and without the icons
# zip staged), every severity, no --ignore-unfixed; then Trivy over the CycloneDX SBOM inside the
# resvg-py wheel. Nothing is pushed.
T=aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c  # pragma: allowlist secret
G=anchore/grype@sha256:621d56d1d68106b787022c81546f7e0a36202a25d1239f0c20195fab8c16f7ea  # pragma: allowlist secret
for img in gac-orchestrator:gate-noicons gac-orchestrator:gate-icons \
           gac-mcp_runtime:gate-noicons gac-mcp_runtime:gate-icons; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$T" image --scanners vuln \
    --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --format json "$img"
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$G" "$img" -o json
done
pip download resvg-py==0.5.0 --no-deps --only-binary=:all: --platform manylinux_2_17_aarch64 -d /tmp/gacrev
#   extract resvg_py-0.5.0.dist-info/sboms/resvg_py.cyclonedx.json from the wheel, then:
docker run --rm -v /tmp/gacrev:/s:ro "$T" sbom /s/resvg_py.cyclonedx.json
```

Notes on the flags:

- `--skip-download` stops Checkov from fetching external modules. The kit uses only local modules;
  without it the scan stalls on network calls.
- `--skip-path '\.tftest\.hcl$'` leaves out the `terraform test` files. They are test harness, not
  deployable configuration, and Checkov's HCL parser times out on `tests/matrix.tftest.hcl`
  (reported as a parsing error, not a finding).

## Results for 3.4.1 (2026-11-07)

`./scripts/security_gate.sh --require-icons` on the 3.4.1 tree (same pinned scanners, same
2026-11-03 base pin; 3.4.1 changes only workstation front-door code, no image input). The first
run failed only on Grype, on all four images, with two advisories new in the vulnerability
database against the unchanged base: CVE-2026-84243 (`glibc-2.44` 2.44-r11, High) and
CVE-2026-116566 (`python-3.15` 3.15.2_git20261030-r0, Medium). Neither has a fix in a released
Wolfi build, so a re-pin cannot remove them; the repository owner approved them as residuals R5
and R6 on 2026-11-06 (below). They also affect the deployed 3.3.1 and 3.4.0 images. No other
suppression was added or changed: the new `frontdoor/pkce_token.py` passes every scanner without
one. Results of the re-run are in the 3.4.1 release commit message.

## Results for 3.4.0 (2026-11-05)

`./scripts/security_gate.sh --require-icons` on the 3.4.0 tree (same pinned scanners, same
2026-11-03 base pin; no image input changed in 3.4.0):

| Scan | 3.4.0 result (passed / failed / skipped inline) |
|---|---|
| Tracked junk, relative links | 0 tracked lock/state/plan/zip/icon/.audit/outputs/.env files; 156 links, 0 broken |
| Checkov terraform, `agentcore/terraform` | 502 / 0 / 14 |
| Checkov terraform, `agentcore/terraform/platform` | 105 / 0 / 5 |
| Checkov terraform, `modules/private-network` | 65 / 0 / 2 |
| Checkov terraform, `modules/backend-bootstrap` | 33 / 0 / 3 |
| Checkov terraform, `modules/shared-identity` | no checks |
| Checkov dockerfile, secrets, CI | 308 / 0 / 0 and 12 / 0 / 0 |
| Bandit, every level | 0 |
| pip-audit, each of the 5 requirements files (incl. the new `requirements-frontdoor.txt`) | 0 |
| detect-secrets | 0 unallowlisted |
| `accepted-residuals.yaml` | 2 entries (R2, R4), all within `review_by` |
| KICS, whole tracked tree | 0 unsuppressed |
| Trivy, each of the 4 images, every severity | 0 |
| Grype, each of the 4 images (Grype DB built 2026-11-04 06:20 UTC) | 0 unaccepted; 2 allowlisted: R2 (CVE-2026-103995, `python-3.15`) and R4 (CVE-2026-119586, `sqlite-libs`) |
| Trivy `sbom` over the resvg-py 0.5.0 wheel | 0 |

Gate result: PASS (exit 0).

The first 3.4.0 run failed only on Grype, on all four images, with CVE-2026-119586 in
`sqlite-libs` 3.54.1-r0: a new upstream advisory against the unchanged base, so it affects the
3.3.1 images too. No code or pin fix exists, and the repository owner approved it as residual R4
on 2026-11-05 (below). No suppression was added or changed in 3.4.0: the new Terraform (gateway
re-sync, entitlement, planner IAM, interceptor key) and Python (planner, harness, plan review)
pass every scanner without one. The CKV_AWS_173 finding on the interceptor environment was fixed
with its own customer managed key, not suppressed.

## Results (before: 3.3.0, after: 3.3.1)

| Scan | 3.3.0 | 3.3.1 (passed / failed / skipped inline) |
|---|---|---|
| Checkov terraform, `agentcore/terraform` (companion, includes modules and platform) | 574 / 0 / 14 (as recorded) | 488 / 0 / 14 |
| Checkov terraform, `agentcore/terraform/platform` | 105 / 0 / 5 | 105 / 0 / 5 |
| Checkov terraform, `modules/private-network` | 65 / 0 / 2 | 65 / 0 / 2 |
| Checkov terraform, `modules/backend-bootstrap` | 33 / 0 / 3 | 33 / 0 / 3 |
| Checkov terraform, `modules/shared-identity` | no checks | no checks (Cognito resources only) |
| Checkov dockerfile (both images, every stage) | 332 / 0 / 0 | 308 / 0 / 0 |
| Checkov secrets, github_actions | 0 findings | secrets 0 findings; github_actions (`.github/workflows/ci.yml`) 148 / 0 / 0 |
| Bandit, every level | 0 | 0 (High 0, Medium 0, Low 0) |
| pip-audit, each of the 4 requirements files (host resolution, see above) | 0 | 0 |
| detect-secrets (tracked files, ledger excluded) | 0 | 0 (every allowlisted line is in the table below) |
| KICS, whole tracked tree | 33 (HIGH 8, MEDIUM 5, LOW 19, INFO 1), not re-triaged | **0 unsuppressed**; the 33 are suppressed inline as K1-K10 below, each scoped to its query id or to one attribute line; 16 new LOW "Chown Flag Exists" on the new Dockerfiles were fixed, not suppressed |
| Trivy, each of the 4 images, every severity | 177 unfixed (HIGH 46, MEDIUM 62, LOW 61, UNKNOWN 8), 0 fixable (as recorded at 3.3.0; 185 when re-measured on 2026-11-02, see the candidate table) | **0** (fixable and unfixed, every severity; Trivy DB of 2026-11-03 01:30 UTC, on the 2026-11-03 pin) |
| Grype, each of the 4 images (new in 3.3.1) | not run (168 on the 3.3.0 base) | 1 Low, the accepted residual R2 below (Grype DB built 2026-11-02 06:26 UTC) |
| Trivy `sbom` over `resvg_py.cyclonedx.json` (74 components, resvg 0.48.1) | 0 | 0 |

This repository's CI is `.github/workflows/ci.yml`, scanned by the Checkov `github_actions`
framework (148 passed, 0 failed, 0 skipped).

The 3.3.0 page recorded 574 passed checks for the companion stack. Re-running the command above on
the 3.3.0 tree gives 488, the same as 3.3.1, so the difference is in how the earlier number was
measured, not a change in the code.

Unpushed commit history was searched for 12-digit account IDs, AWS access keys, private keys,
tokens and JWT-shaped strings. The only hit is the synthetic test JWT in
`tests/test_private_access_probe.py` (header `RS256`, subject `probe-test-user`, signature
`signature-placeholder`), which is not a credential.

## Runtime base image (3.3.1)

Both runtime images use Chainguard Python (Wolfi), pinned by multi-arch index digest, with the tag
in a comment above each `FROM`:

| Stage | Tag | Digest |
|---|---|---|
| `diagram-assets`, `runtime-deps` (discarded build stages) | `cgr.dev/chainguard/python:latest-dev` | `sha256:f543e40c72e83ef926f91dba626efa4fcc6ad997cd603fee07c038e4b6902fb7` <!-- pragma: allowlist secret --> |
| runtime | `cgr.dev/chainguard/python:latest` | `sha256:1cd5d9ebc11ac60b133b1fc6bbfa9631c5156cf6334902d721b1e87160be3f20` <!-- pragma: allowlist secret --> |

These digests were pinned on 2026-11-03 (re-pin approved by the repository owner, 2026-11-03).
The first 3.3.1 pin (2026-11-02, Python 3.15.1) scanned 0 in Trivy on 2026-11-02. After the Trivy
DB update of 2026-11-03 (01:30 UTC) it showed 8 fixable findings: CVE-2026-113351 (MEDIUM) and
CVE-2026-115506 (UNKNOWN) in `libexpat1` 2.9.0-r0, fixed in 2.10.0-r0; and CVE-2026-115557 and
CVE-2026-114583 (MEDIUM in Trivy) in each of `python-3.15`, `python-3.15-base` and
`python-3.15-runtime` 3.15.1_git20261024-r1, fixed in 3.15.2_git20261030-r0. The 2026-11-03 pin
fixes all eight.

The runtime stage has no shell and no package manager, runs as uid 65532, and holds Python
3.15.2, the venv (pip removed) and two pinned DejaVu fonts (`ttf-dejavu=2.37-r9`). Code, venv and
icon catalog are root-owned; only the engine's local `.audit`, `.feedback` and `.plans`
directories are writable by the runtime user. Refresh procedure: [docs/DEPLOY.md](../DEPLOY.md),
"Base image".

Candidates measured on 2026-11-02 (built images, `linux/arm64`, both faces gave the same counts;
counts are CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN), plus the 2026-11-03 re-pin of the chosen base:

| Candidate (runtime Python) | Trivy unfixed | Trivy fixable | Grype | Size orchestrator / MCP | Runtime user |
|---|---|---|---|---|---|
| Chainguard `latest`, 2026-11-03 pin (3.15.2), **shipped**; Trivy DB 2026-11-03 | 0/0/0/0/0 | 0 | 1 (R2 below) | 282 / 294 MB (287 / 299 with icons) | 65532 |
| Chainguard `latest`, 2026-11-02 pin (3.15.1), Trivy DB 2026-11-02 | 0/0/0/0/0 | 0 | 3 (CVE-2026-115557, CVE-2026-103995, CVE-2026-114583) | 282 / 294 MB | 65532 |
| Same 2026-11-02 pin, Trivy DB 2026-11-03 | 0/0/0/0/0 | 8 (MEDIUM 7, UNKNOWN 1; listed above) | 3 | 282 / 294 MB | 65532 |
| Amazon Linux 2023 minimal + python3.12 (3.12.15) | 0/0/0/0/0 | 0 | 3 from its system pip; 0 with pip removed | 635 / 658 MB | 10001 |
| Google distroless python3-debian12 (3.11.2) | 2/14/85/50/13 | 164 | 299 | 269 / 281 MB | 65532 |
| `python:3.12-slim`, the 3.3.0 base (3.12.16), re-measured 2026-11-02 | 0/45/71/61/8 (185; 177 as recorded at 3.3.0) | 0 | 168 | 398 / 410 MB | 10001 |

Every candidate passed the same compatibility checks: arm64 build, resvg-py PNG render with the
DejaVu fonts, OpenTelemetry start under `opentelemetry-instrument`, `/ping` and `/invocations` on
the orchestrator, MCP `tools/list`, Docker health `healthy`, non-root user. Chainguard was chosen
for the smallest runtime surface (30 OS packages, no shell or package manager), the built-in
non-root user, and residuals that a second scanner can see. Amazon Linux 2023 also reaches 0 in
Trivy, but its interpreter lacked the CPython fixes below and neither scanner reported them. The
2026-11-03 re-pin passed the same checks again (results in the release verification notes:
healthchecks, MCP `tools/list`, and the resvg render with icons).

**No candidate is free of known CPython issues.** On the 2026-11-02 pin, Grype matched three
CPython CVEs by CPE. The 2026-11-03 re-pin to 3.15.2 fixes two of them: CVE-2026-115557
(Critical) and CVE-2026-114583 (Medium). Trivy lists both as fixed in 3.15.2_git20261030-r0, and
Grype no longer reports them. Their former allowlist entries (R1, R3) were removed. One remains,
checked against the shipped code:

### Accepted image residual (R2)

Machine-readable list: [`security/accepted-residuals.yaml`](../../security/accepted-residuals.yaml).
Each entry matches one vulnerability id, package and installed version; the gate fails on any
other finding and after the `review_by` date. R2 was first approved by the repository owner on
2026-11-02 for 3.15.1, and re-approved for 3.15.2 by the repository owner, 2026-11-03
(`approved_on`). The evidence below was re-measured on 2026-11-03.

| # | CVE | Severity | Package / version | Status | Evidence | Review by |
|---|---|---|---|---|---|---|
| R2 | CVE-2026-103995 | Low (Grype, NVD); not in Trivy | `python-3.15` 3.15.2_git20261030-r0 | Accepted residual: no released CPython line has the fix (NVD `< 3.16.0`; Grype fix state `unknown`); present in every candidate | `/usr/lib/python3.15/http/client.py` in the 3.15.2 image has no `_is_legal_chunk_size` validation (checked 2026-11-03) | 2026-11-30 |

Exposure (assessment, not tested): R2 affects `http.client` parsing of chunked responses. The
engine's outbound HTTP goes to AWS endpoints through the AWS SDK, and in private mode only through
VPC endpoints.

Re-review trigger: when a released CPython 3.15 build has the CVE-2026-103995 fix, re-pin the base
with the procedure in [docs/DEPLOY.md](../DEPLOY.md) ("Base image") and remove R2. Before
`review_by` (2026-11-30), the owner must re-review R2 or re-pin; the gate fails after that date.

### Accepted image residual (R4, approved 2026-11-05)

Approved by the repository owner on 2026-11-05 (`approved_on`), for the 2026-11-03 base. It
affects the 3.3.1 and 3.4.0 images alike.

| # | CVE | Severity | Package / version | Status | Evidence | Review by |
|---|---|---|---|---|---|---|
| R4 | CVE-2026-119586 | High (Grype, NVD CVSS 3.1 7.8, local, needs user interaction); not in Trivy | `sqlite-libs` 3.54.1-r0 (a dependency of the base's `python-3.15`) | Accepted residual: no upstream fix (Grype fix state empty; the newest Wolfi `sqlite-libs` is 3.54.1-r0 as of 2026-11-05, and the current Chainguard `latest` and `latest-dev` digests ship it), so a re-pin cannot remove it | The CVE is a command injection in the SQLite **command-line shell's** `.recover` output. Checked 2026-11-05 in all four gate images with `docker run --rm --entrypoint /usr/bin/python <image> -c "..."` listing `/usr/bin/sqlite3`, `/bin/sqlite3` and `/usr/lib/libsqlite3*`: no `sqlite3` binary, only `libsqlite3.so.0` and `libsqlite3.so.3.54.1`; the runtime stage has no shell; `grep -rln sqlite agents frontdoor agentcore/app main.py` finds nothing, so the kit code does not import `sqlite3`; `apk policy sqlite-libs` in `latest-dev` lists 3.54.1-r0 as the newest | 2026-11-30 |

Exposure (assessment, not tested): the vulnerable code path is in the CLI shell, which the images
do not contain; the library is present only because CPython's optional `_sqlite3` module links
it.

Re-pin trigger: the first Wolfi `sqlite-libs` build that contains the fix. Then re-pin the base
with the procedure in [docs/DEPLOY.md](../DEPLOY.md) ("Base image") and remove R4. Before
`review_by` (2026-11-30), the owner must re-review R4 or re-pin; the gate fails after that date.

### Accepted image residuals (R5 and R6, approved 2026-11-06)

Approved by the repository owner on 2026-11-06 (`approved_on`), for the 2026-11-03 base. Both
affect the 3.3.1, 3.4.0 and 3.4.1 images alike. Each matches its exact installed version.

| # | CVE | Severity | Package / version | Status | Evidence | Review by |
|---|---|---|---|---|---|---|
| R5 | CVE-2026-84243 | High (Grype, CVSS 3.1 7.0, `AV:L/AC:H/PR:L/UI:N`); not in Trivy | `glibc-2.44` 2.44-r11 | Accepted residual: no upstream fix (Grype fix state `unknown`); the current Chainguard `latest` (`sha256:f3e5face39b0...`) and `latest-dev` (`sha256:fe4dbb574f81...`) digests ship glibc 2.44-r13, still affected | Advisory GLIBC-SA-2026-0025: gettext accepts `/` or `..` in the `LANGUAGE` environment variable, so a crafted `.mo` file can be loaded | 2026-11-30 |
| R6 | CVE-2026-116566 | Medium (Grype, CVSS 4.0 5.3, `AV:A/PR:H/UI:P`); not in Trivy | `python-3.15` 3.15.2_git20261030-r0 | Accepted residual: no fix in a released Wolfi build (still present in 3.15.2_git20261106-r0); upstream fix cpython PR 160899 | `socket.getaddrinfo` accepts null bytes in the host and port. Call sites checked 2026-11-07 with `git grep` for `getaddrinfo`, `create_connection`, `socket.`, `urlopen` and `requests.post`: the only direct `getaddrinfo` is `agentcore/probe/private_access_probe.py` `_dns()`, host from `GAC_PROBE_GATEWAY_URL` / `GAC_PROBE_REGION` (Lambda env set by Terraform), port 443; the only listener is the workstation `frontdoor/pkce_token.py` (127.0.0.1, integer ports from `GAC_COGNITO_REDIRECT_PORTS`); other outbound calls go to configured, allowlisted AWS hosts; `agentcore/app` makes no direct socket call | 2026-11-30 |

Exposure, R5 (assessment, not tested): exploitation needs `LANGUAGE` set inside the container. The
runtime environment is fixed by Terraform and AgentCore, the runtime stage has no shell, and the
process runs as non-root uid 65532.

Exposure, R6 (assessment, not tested): the hosts and ports the kit resolves come from deployment
config (gateway URL, AWS endpoints), not from user input; no call site resolves a request-derived
host or port.

Re-pin triggers: R5, the first Wolfi glibc build listed as fixed; R6, the first Wolfi
`python-3.15` build with the fix. Then re-pin the base with the procedure in
[docs/DEPLOY.md](../DEPLOY.md) ("Base image") and remove the entry. Before `review_by`
(2026-11-30), the owner must re-review R5 and R6 or re-pin; the gate fails after that date.

## Fixed in 3.3.1 (no suppression)

| Finding | Where | Fix | Deployed-resource impact |
|---|---|---|---|
| Trivy: 177 unfixed OS-package findings (HIGH 46, MEDIUM 62, LOW 61, UNKNOWN 8, as recorded at 3.3.0; 185 when re-measured on 2026-11-02) from the `python:3.12-slim` base, referenced by tag | both runtime images | Chainguard Python base pinned by digest (section above); `ttf-dejavu=2.37-r9` replaces the Debian fonts | New image builds (both `null_resource` builders and both runtimes' image URIs change on the next human-run apply) |
| Trivy (DB of 2026-11-03): 8 fixable on the first Chainguard pin, `libexpat1` CVE-2026-113351 and CVE-2026-115506, and CVE-2026-115557 and CVE-2026-114583 on `python-3.15`, `-base` and `-runtime`; Grype CVE-2026-115557 (Critical) and CVE-2026-114583 (Medium) | both runtime images | Re-pinned to the 2026-11-03 digests (Python 3.15.2, `libexpat1` 2.10.0-r0); allowlist entries R1 and R3 removed | New image builds only |
| KICS "Chown Flag Exists" (16 LOW, on the first Chainguard Dockerfiles) | both Dockerfiles | No `COPY --chown`: code, venv and catalog are root-owned and read-only to the runtime user; only `/app/engine/.audit`, `.feedback` and `.plans` are created writable (uid 65532) in the build stage | New image builds; the runtime user can no longer modify its own code (it could in 3.3.0) |
| KICS markers that did not apply (all 33 findings) | `agentcore/terraform/**` | Re-triaged one by one; markers rewritten so KICS honours them (K1-K10 below) | None: comments only |
| KICS markers that masked every query on a resource or policy (K1, K4, K5/K6, K7, K9, K10: `ignore-line` on a resource header or on a whole `policy = jsonencode(` document, or `ignore-block`) | `cognito.tf`, `s3_kb.tf`, `interceptor.tf`, `probe.tf`, `modules/private-network/{main,security_groups}.tf`, `modules/shared-identity/main.tf`, `modules/backend-bootstrap/main.tf` | Replaced by file-header `disable=<query id>` (only the triaged query) or, for K1, one `ignore-line` per reported attribute line. Proven by injecting unrelated misconfigurations (below) | None: comments only |
| Stale `kics-scan` marker for "IAM policy allows for data exfiltration" | `iam.tf` | Removed: KICS reports no finding there without it | None |
| Cognito comments said MFA defaults to OFF | `cognito.tf` header and pool | Comments now say OPTIONAL, the variable's actual default | None |

## Fixed in 3.3.0 (no suppression)

| Finding | Where | Fix | Deployed-resource impact |
|---|---|---|---|
| 6 fixable CVEs in the base image's bundled `pip` 25.0.1 | both runtime images | `--pull` on every build and `pip==26.2.2` as the first final-stage `RUN` | New image builds |
| 8 fixable findings in `pip` 26.2.2's own vendored copies (`urllib3` 2.7.0, `msgpack` 1.1.2, `pkg_resources` from `setuptools` 70.3.0), still present in the latest pip | both runtime images | `python -m pip uninstall -y pip` after the requirements install; the runtime never runs pip | New image builds; no pip inside the runtime images (rebuild to change dependencies) |
| detect-secrets: the D6 row and two new fixture lines | `docs/security/scan-results.md`, `scripts/diagram_fixture.py`, `tests/test_diagram_manifest.py` | inline pragma (rows D6 and D8 below) | None |

## Fixed in 3.2.2 (no suppression)

| Finding | Where | Fix | Deployed-resource impact |
|---|---|---|---|
| CVE-2026-114603 (`opentelemetry-api` 1.44.0), CVE-2026-116694 (`opentelemetry-propagator-jaeger` 1.44.0) | both runtime images | `aws-opentelemetry-distro` 0.21.0 to 0.22.0 and `opentelemetry-api` 1.44.0 to 1.45.1 (resolves with `bedrock-agentcore` 1.18.1 on Python 3.12 / arm64) | New image builds; the runtimes pick them up on the next image push |
| CKV2_AWS_11 VPC flow logs (was skipped) | `modules/private-network` (`create_vpc = true`: companion sandbox VPC and the platform shared VPC) | `flow_logs.tf`: ALL-traffic flow log to a CloudWatch Logs group, 365-day retention, its own rotating CMK, delivery role trusted by `vpc-flow-logs.amazonaws.com` with `aws:SourceAccount` / `aws:SourceArn`, write access to that group only | Adds a KMS key and alias, a log group, an IAM role and inline policy, and a flow log when the module creates the VPC. A VPC you bring is unchanged |
| CKV_AWS_394 unpinned AZ data source | `modules/private-network` | `zone-id` filter on the AgentCore-supported AZ IDs; same AZs as before (the module already intersected with that list) | None |
| CKV_AWS_116, CKV_AWS_173, CKV_AWS_158, CKV_AWS_338 (were skipped) | private access probe | Encrypted SQS dead-letter queue, a rotating CMK for the environment variables and the log group, 365-day log retention | Only with `enable_private_access_probe = true`: adds a KMS key and alias and an SQS queue; changes the log group (KMS, retention) and the function (KMS, DLQ). The role gains `sqs:SendMessage` on its own queue |
| CKV2_AWS_61 lifecycle, CKV2_AWS_62 event notifications | `modules/backend-bootstrap` (platform state buckets) | Lifecycle on the access-log bucket (365 days); EventBridge notifications on both buckets | Platform stack: adds three resources (see the access-log delivery row for the two in-place updates) |
| State access logs not deliverable (found by review, not by a scanner) | `modules/backend-bootstrap` `state_logs` | Default encryption SSE-KMS to SSE-S3 (S3 server access logging does not deliver to an SSE-KMS target), and the bucket policy gains an `s3:PutObject` grant to `logging.s3.amazonaws.com` on `state-access-logs/*`, scoped by `aws:SourceArn` (the state bucket) and `aws:SourceAccount`. Suppression C12 records the SSE-S3 choice | Platform stack: two **in-place updates** of existing resources (`aws_s3_bucket_server_side_encryption_configuration.state_logs`, `aws_s3_bucket_policy.state_logs_tls_only`). UNVERIFIED in AWS: after the human-run apply, check that objects appear under `state-access-logs/` |
| Bandit B110 / B112 (silent `except: pass` / `continue`), 9 Low | `agents/`, `scripts/generate_bible.py` | Each handler now records the exception type at debug level (or on stderr for the script). No message content is logged; verdicts and output are unchanged | None |

## Suppressions for approval

Every entry below is inline on the resource, line, block or file. Approve, or reject and the kit
implements the control. The image residuals R2, R4, R5 and R6 are listed above, under the base image.

**Sign-off: approved by the repository owner, 2026-11-02**, as a batch: Checkov C1-C12, KICS
K1-K10, Bandit B1-B3 and detect-secrets D1-D11, with the justifications below. The two KICS design
choices were approved explicitly: K6 as not applicable (the kit VPC has private subnets only, no
internet or NAT gateway, and egress only to VPC endpoints governed by endpoint and resource
policies; **a team that adds internet egress must add network inspection, for example AWS Network
Firewall**), and K8 as out of scope (an account or Organization control; **deployers must make sure
IAM Access Analyzer is enabled at the account or Organization level**, or set
`enable_access_analyzer`).

**Narrowed marker forms: approved by the repository owner, 2026-11-02.** After the batch sign-off,
the markers of K1, K5, K6, K7, K9 and K10 were narrowed to a query-scoped `disable=` or one
attribute line, and K4 moved from an `ignore-line` on each whole bucket policy to a query-scoped
file-header `disable=`. The queries, targets and justifications are unchanged. The owner approved
the narrowed K1, K5-K7, K9 and K10 forms, and K4 on condition that it drops only its own query,
which the 2026-11-03 proof below shows. No suppression is pending.

### Checkov (Terraform)

| # | Check | Resource | Justification | Debt row |
|---|---|---|---|---|
| C1 | CKV_AWS_117 Lambda in a VPC | `aws_lambda_function.identity_interceptor` | The interceptor makes no network calls; it reshapes a header from a JWT the gateway already verified. A VPC adds ENIs, EC2 ENI permissions on `Resource "*"` and cold-start latency on the request path for no gain | SD-6 |
| C2 | CKV_AWS_272 Lambda code signing | `aws_lambda_function.identity_interceptor` | Built from single-file source in this repo by `archive_file`, pinned by `source_code_hash`. Lambda code signing needs an AWS Signer profile and signed S3 artifacts, which the zip-upload path does not use | SD-9 |
| C3 | CKV_AWS_272 Lambda code signing | `aws_lambda_function.private_access_probe` | Same as C2, for an opt-in test stand-in that is off by default | SD-9 |
| C4 | CKV2_AWS_5 SG not attached | `module.private_network` / `module.network` `aws_security_group.vpce_client` | False positive: attached to the interface endpoints through the role-keyed `local.sg_ids` map in `endpoints.tf`, which the graph check does not follow | n/a |
| C5 | CKV2_AWS_5 SG not attached | `aws_security_group.vpce_runtime` (same modules) | Same as C4 | n/a |
| C6 | CKV2_AWS_5 SG not attached | `aws_security_group.runtime` | False positive: attached to both AgentCore runtimes (`network_mode_config.security_groups` in `runtimes.tf`), a resource type the graph check does not model | n/a |
| C7 | CKV_AWS_144 cross-Region replication | `aws_s3_bucket.kb` | Small single-Region knowledge base; versioned, CMK-encrypted, rebuildable from the repo | SD-10 |
| C8 | CKV_AWS_144 cross-Region replication | `aws_s3_bucket.kb_logs` | Access-log bucket; operational metadata with a 365-day lifecycle | SD-10 |
| C9 | CKV_AWS_145 KMS default encryption | `aws_s3_bucket.kb_logs` | S3 server access logging delivers only to a target bucket with SSE-S3 default encryption, so SSE-KMS is not an option for this bucket | SD-3 |
| C10 | CKV_AWS_144 cross-Region replication | `module.backend.aws_s3_bucket.state` | Replicating Terraform state is a per-organization DR decision (second-Region bucket, key and role); the bucket is versioned with noncurrent-version retention | SD-10 |
| C11 | CKV_AWS_144 cross-Region replication | `module.backend.aws_s3_bucket.state_logs` | Access-log bucket with a 365-day lifecycle | SD-10 |
| C12 | CKV_AWS_145 KMS default encryption | `module.backend.aws_s3_bucket.state_logs` | Same as C9: S3 server access logging delivers only to a target bucket with SSE-S3 default encryption | SD-3 |

Checkov counts C4 and C5 once per module instance, which is why the companion scan shows 14
skipped checks for 12 inline markers.

### KICS (Terraform)

KICS 2.2.0 honours a marker only when it is the last comment line before its target:
`# kics-scan ignore-line` covers the next line, `# kics-scan ignore-block` the block that starts on
the next line, and `# kics-scan disable=<query id>` in a file's leading comment block covers the
whole file for that query. Each marker below has its justification on the comment lines above it.

Scope of each form: `disable=<query id>` (K4-K10) drops only that query, but for the whole file;
every other KICS query still runs on every resource in the file. `ignore-line` drops every query
reported on its one line, so it is used only for the secret-name false positives K1-K3, each on a
single attribute line or, for K3, one ARN value in a `resources` list; never on a resource header
or a whole policy document;
`ignore-block` is not used. Checkov's check-specific skips are a second net on
the same resources.

Scoping proof (2026-11-02, temporary copy, reverted): with the 3.3.1 markers, KICS reported each
injected, unrelated misconfiguration: SQS SSE off on both dead-letter queues (`interceptor.tf`,
`probe.tf`), X-Ray `PassThrough` on the interceptor Lambda, an SSH ingress from `0.0.0.0/0` on
`aws_security_group.vpce_runtime`, a password-shaped tag on `aws_vpc.this` and on the shared
Cognito pool, and a public-read S3 bucket in `cognito.tf`. With the earlier `ignore-block`
markers, the SQS and X-Ray findings were hidden.
K4 proof (2026-11-03, temporary copy, reverted): a literal `aws_s3_bucket_policy` with a
wildcard-principal `Allow s3:*` added to `s3_kb.tf` was reported by three CRITICAL queries (S3
Bucket Access to Any Principal, Allows Delete Action From All Principals, With All Permissions)
with the K4 header in place. With `4bc4dd4c` removed from both headers, KICS reports exactly the
three K4 findings and nothing else, so the header is what suppresses them. Known KICS limit, not
caused by a marker: the same wildcard `Allow` injected inside the reference-valued `kb` and
`state_logs` policies is not reported by any KICS query, with or without K4, because KICS cannot
evaluate those `jsonencode()` documents. The net for them is
`tests/test_terraform_private_mode_static.py` (the exact statement set of the `kb` policy; it
failed on the injection) and Checkov CKV_AWS_70 (it failed on the `state_logs` injection).

| # | Query (id) | Sev, count | Where (marker) | Justification |
|---|---|---|---|---|
| K1 | Passwords And Secrets - Generic Token (`baee238e`) | HIGH x6 | `cognito.tf`, the six attribute lines of both `token_validity_units` blocks (one ignore-line each) | Attribute names `access_token`, `id_token`, `refresh_token` with unit values `"minutes"` / `"days"` <!-- pragma: allowlist secret -->; no token value in the file |
| K2 | Generic Token (`baee238e`) | HIGH x1 | `probe.tf`, `local.private_access_probe_invoke_command` (ignore-line) | The jq field name `token` and the environment variable name `GAC_GATEWAY_TOKEN`; jq reads the value at run time, nothing is in the file |
| K3 | Passwords And Secrets - Generic Secret (`3e2d3b2f`) | HIGH x1 | `gateway.tf`, `OutboundOauthSecret` resource ARN (ignore-line) | An IAM resource ARN pattern for the AgentCore Identity OAuth2 secret path, not a value |
| K4 | S3 Bucket Policy Accepts HTTP Requests (`4bc4dd4c`) | MEDIUM x3 | `s3_kb.tf` `aws_s3_bucket_policy.kb` and `.kb_logs`, `modules/backend-bootstrap` `aws_s3_bucket_policy.state_logs_tls_only` (file-header `disable=4bc4dd4c-7d8d-405e-a0fb-57fa4c31b4d9` in both files) | KICS cannot evaluate a `jsonencode()` policy whose `Condition` values are Terraform references (the same policy with literal values passes). Each policy has `DenyInsecureTransport`: Deny `s3:*` on the bucket and its objects when `aws:SecureTransport` is `"false"`. Asserted by `tests/test_terraform_private_mode_static.py` (kb, kb_logs) and `tests/test_terraform_modules_static.py` (state_logs) |
| K5 | VPC FlowLogs Disabled (`f83121ea`) | MEDIUM x1 | `modules/private-network/main.tf` (file-header `disable=f83121ea-03da-434f-9277-9cd247ab3047`) | The flow log exists (`flow_logs.tf`, `aws_flow_log.this`, `vpc_id = aws_vpc.this[0].id`); the query matches only `aws_vpc.<name>.id` in the same file |
| K6 | VPC Without Network Firewall (`fd632aaf`) | MEDIUM x1 | `modules/private-network/main.tf` (file-header `disable=fd632aaf-b8a1-424d-a4d1-0de22fd3247a`) | **Design choice (not a false positive): approved as not applicable, 2026-11-02.** The VPC has no internet gateway, no NAT gateway and no route other than local and the S3 gateway endpoint, so there is no internet-bound traffic for a Network Firewall to inspect; egress is to VPC endpoints only. A firewall would add per-AZ cost for no inspected traffic. A fork that adds internet egress must add network inspection (for example AWS Network Firewall) and remove this query id |
| K7 | Cognito UserPool Without MFA (`ec28bf61`) | LOW x2 | `cognito.tf` (`aws_cognito_user_pool.this`), `modules/shared-identity/main.tf` (`aws_cognito_user_pool.shared`) (file-header `disable=ec28bf61-a474-4dbe-b414-6dd3a067d6f0`) | KICS does not evaluate the `dynamic "software_token_mfa_configuration"` block. `mfa_configuration` defaults to `OPTIONAL` and TOTP is enabled whenever MFA is `OPTIONAL` or `ON` |
| K8 | IAM Access Analyzer Not Enabled (`e592a0c5-5bdb-414c-9066-5dba7cdea370`) | LOW x13 | file-level `disable=` in `gateway.tf`, `iam.tf`, `guardrail.tf`, `s3_kb.tf`, `runtimes.tf`, `ecr.tf`, `modules/private-network/{main,flow_logs,endpoints,security_groups,resolver}.tf`, `modules/shared-identity/main.tf`, `modules/backend-bootstrap/main.tf` | The query fires once per file that has any `aws_` resource and no analyzer. The analyzer is an account or organization baseline control a landing zone normally owns; the kit makes it opt-in (`enable_access_analyzer`, `access_analyzer.tf`; SD-8). **Design choice (not a false positive): approved as out of scope, 2026-11-02; deployers must make sure Access Analyzer is enabled at the account or Organization level.** File-scoped because the query reports the file, not a line or block, so a line or block marker cannot match it |
| K9 | S3 bucket notifications disabled (`e39f87f5`) | LOW x4 | `interceptor.tf` `aws_lambda_function.identity_interceptor`, `aws_sqs_queue.interceptor_dlq`; `probe.tf` `aws_sqs_queue.probe_dlq`, `aws_lambda_function.private_access_probe` (file-header `disable=e39f87f5-0abf-488b-864c-63ee1f588140` in both files) | The query flags any Lambda function or SQS queue in a file without an `aws_s3_bucket_notification`. None of these is an S3 notification target; the kit's buckets enable EventBridge notifications |
| K10 | Security Group Not Used (`4849211b`) | INFO x1 | `modules/private-network/security_groups.tf` `aws_security_group.vpce_runtime` (file-header `disable=4849211b-ac39-479e-ae78-5694d506cb24`) | Both endpoint groups are attached to the interface endpoints through the role-keyed `local.sg_ids` map in `endpoints.tf`, which the query does not follow (same as Checkov C4/C5) |

All K1-K10 markers are comments: no Terraform resource or argument changed, and the private-mode
access behaviour validated in the sandbox (gateway, orchestrator and Memory reachable only through
VPC endpoints; the MCP runtime only from the gateway role) is unchanged.

### Bandit

| # | Test | Where | Justification |
|---|---|---|---|
| B1 | B104 bind all interfaces | `agentcore/app/mcp_runtime/runtime_mcp_entrypoint.py` lines 130 and 150 | The AgentCore Runtime container must listen on `0.0.0.0:8000` for the platform health check and traffic; exposure is set by the runtime's network mode, security groups and resource policy, not the bind |
| B2 | B105 hard-coded password | `frontdoor/gateway_token.py` line 32 | `TOKEN_VAR` is the name of an environment variable, not a value |
| B3 | B104 | `tests/test_mcp_runtime_stateless.py` line 151 | A test asserting the bind contract above |

### detect-secrets

| # | Lines | Why it is not a secret |
|---|---|---|
| D1 | `agentcore/terraform/cognito.tf` (9 lines) | Attribute names and values such as `generate_secret = true`, `enable_token_revocation = true`, `access_token = "minutes"` <!-- pragma: allowlist secret --> |
| D2 | `agentcore/terraform/secrets.tf` line 23, `terraform.tfvars.example` line 123 | A variable reference and a commented ARN placeholder |
| D3 | `agents/data_protection.py` (2 lines) | The guard's own detector vocabulary (a split keyword and a comment describing the URL-password pattern) |
| D4 | `frontdoor/gateway_token.py` line 32 | An environment variable name |
| D5 | `tests/` (9 lines) | Synthetic fixtures built to exercise the detectors: a split fake key, a fake access-key shape, a `user:pw` URL, placeholder tokens and the synthetic JWT above |
| D6 | `docs/security/scan-results.md` (the D1 row) | The D1 row quotes the `cognito.tf` attribute `access_token = "minutes"` <!-- pragma: allowlist secret --> |
| D8 | `scripts/diagram_fixture.py` line 19, `tests/test_diagram_manifest.py` (2 lines), `tests/test_diagram_labels.py`, `tests/test_governance_gate_refactor.py` | The AWS Secrets Manager icon path in the synthetic icon fixture, a 12-hex short-hash fixture, and fake secrets built by concatenation to exercise the label policy and the gate |
| D9 | `docs/security/scan-results.md` (tools table, command lines, base-image table) | Public digests of the pinned scanner images (KICS, Trivy, Grype) and of the Chainguard base images |
| D10 | `agentcore/app/orchestrator/Dockerfile`, `agentcore/app/mcp_runtime/Dockerfile` (3 lines each) | The public digests of the Chainguard base images on the `FROM` lines (`pragma: allowlist nextline secret` above each) |
| D11 | `scripts/security_gate.sh` (3 lines) | The public digests of the pinned KICS, Trivy and Grype images. The same KICS and actionlint digests in `.github/workflows/ci.yml` are not flagged by detect-secrets, so that file needs no pragma |
| D7 | `governance/integrity.yaml` (excluded by path) | SHA-256 digests of `PRINCIPLES.md`, public by design. `scripts/rebaseline_integrity.py` rewrites this file and drops comments, so an inline pragma would not survive |

## Out of scope for these tools

- The Trivy and Grype image scans need local Docker builds, so CI runs the gate with `--static`
  (no image stages) plus a separate pinned KICS job. Run `./scripts/security_gate.sh
  --require-icons` locally before a release.
- `.github/workflows/ci.yml` actions and the pinned actionlint image are CI tooling, pinned by
  commit SHA or digest, not shipped images, and are not scanned by Trivy here.
- `plugin/.mcp.json` uses `@latest` package references by design (SD-13); pip-audit does not read it.
- Lambda and AgentCore Runtime log groups that the services create on first write use the
  account-default retention (SD-4). Only the log groups the kit creates (probe, VPC flow logs)
  set retention and a CMK.
