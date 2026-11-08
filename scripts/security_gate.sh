#!/usr/bin/env bash
# The kit's standard security gate (docs/security/scan-results.md). Runs every scanner with a
# pinned version over a copy of the TRACKED files, prints one line per stage and exits 1 if any
# stage fails. A stage fails on ANY finding: there is no severity threshold. The only accepted
# findings are inline, justified suppressions (listed in scan-results.md) and, for the image
# scanners, the version-scoped entries in security/accepted-residuals.yaml.
#
#   ./scripts/security_gate.sh                 # everything; icons images when one zip is staged
#   ./scripts/security_gate.sh --require-icons # fail instead of skipping when no zip is staged
#   ./scripts/security_gate.sh --skip-images   # no Docker image builds or image scans
#   ./scripts/security_gate.sh --static        # no Docker at all (CI): implies --skip-images
#                                              # and skips KICS (CI runs KICS as its own job)
#
# Read-only by design (Tenets 1/3): it builds images LOCALLY only (never pushes, never logs in to
# a registry), never runs terraform, and never uses the kit's own .venv. Scanner tools live in a
# throwaway virtual environment (GAC_SCAN_VENV, default /tmp/gac-scan-venv) created on demand.
# Works with the macOS system bash (3.2).
set -euo pipefail

# ---- Pinned tools --------------------------------------------------------------------------
CHECKOV_VERSION=3.4.0
BANDIT_VERSION=1.9.4
PIP_AUDIT_VERSION=2.10.1
DETECT_SECRETS_VERSION=1.5.0
# checkmarx/kics:v2.2.0
# pragma: allowlist nextline secret
KICS_IMAGE=checkmarx/kics@sha256:ef29dbac14b046b1ef65f3280646112e927a61781c5f01bca1aeb723e0b96003
# aquasec/trivy:0.76.1
# pragma: allowlist nextline secret
TRIVY_IMAGE=aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c
# anchore/grype:v0.121.0
# pragma: allowlist nextline secret
GRYPE_IMAGE=anchore/grype@sha256:621d56d1d68106b787022c81546f7e0a36202a25d1239f0c20195fab8c16f7ea

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The resvg-py SBOM check scans the version the images install: read it from both runtime
# requirements files, and stop if they disagree (they would ship different wheels).
resvg_pin() { sed -nE 's/^resvg-py==([^[:space:]#]+).*/\1/p' "$ROOT/agentcore/app/$1/requirements.txt"; }
RESVG_PY_VERSION="$(resvg_pin orchestrator)"
if [ -z "$RESVG_PY_VERSION" ] || [ "$RESVG_PY_VERSION" != "$(resvg_pin mcp_runtime)" ]; then
  echo "resvg-py pin missing or different in the two runtime requirements files" >&2; exit 2
fi
SCAN_VENV="${GAC_SCAN_VENV:-/tmp/gac-scan-venv}"
TRIVY_CACHE="${GAC_TRIVY_CACHE:-/tmp/gac-trivy-cache}"
GRYPE_CACHE="${GAC_GRYPE_CACHE:-/tmp/gac-grype-cache}"
ALLOWLIST="$ROOT/security/accepted-residuals.yaml"
STATIC=0 SKIP_IMAGES=0 REQUIRE_ICONS=0
for arg in "$@"; do
  case "$arg" in
    --static) STATIC=1; SKIP_IMAGES=1 ;;
    --skip-images) SKIP_IMAGES=1 ;;
    --require-icons) REQUIRE_ICONS=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
case "$SCAN_VENV" in "$ROOT"/.venv|"$ROOT"/.venv/*) echo "refusing to use the kit's .venv for scanners" >&2; exit 2 ;; esac

if [ -n "${GAC_SCAN_WORK:-}" ]; then WORK="$GAC_SCAN_WORK"; mkdir -p "$WORK"; KEEP_WORK=1
else WORK="$(mktemp -d "${TMPDIR:-/tmp}/gac-gate.XXXXXX")"; KEEP_WORK=0; fi
cleanup() { [ "$KEEP_WORK" = 1 ] || rm -rf "$WORK"; }
trap cleanup EXIT
SRC="$WORK/src"
LOG="$WORK/logs"
mkdir -p "$LOG"

RESULTS=""
FAILED=0
record() {  # record <stage> <PASS|FAIL|SKIP> <summary>
  RESULTS="${RESULTS}$(printf '%-48s %-4s %s' "$1" "$2" "$3")"$'\n'
  [ "$2" = FAIL ] && FAILED=1
  printf '%-48s %-4s %s\n' "$1" "$2" "$3"
}

# ---- Scanner venv (exact pins; created or repaired on demand) ------------------------------
ensure_venv() {
  local want="checkov==$CHECKOV_VERSION bandit==$BANDIT_VERSION pip-audit==$PIP_AUDIT_VERSION detect-secrets==$DETECT_SECRETS_VERSION"
  if [ ! -x "$SCAN_VENV/bin/python" ]; then python3 -m venv "$SCAN_VENV"; fi
  local have
  have="$("$SCAN_VENV/bin/python" -m pip freeze 2>/dev/null | tr 'A-Z_' 'a-z-' || true)"
  local missing=0 pkg
  for pkg in $want; do
    printf '%s\n' "$have" | grep -qx "$(printf '%s' "$pkg" | tr 'A-Z_' 'a-z-')" || missing=1
  done
  if [ "$missing" = 1 ]; then
    "$SCAN_VENV/bin/python" -m pip install --quiet $want
  fi
}
PY() { "$SCAN_VENV/bin/python" "$@"; }

# ---- 1. Copy of the tracked files ---------------------------------------------------------
copy_tracked() {
  rm -rf "$SRC"; mkdir -p "$SRC"
  (cd "$ROOT" && git ls-files -z) | PY -c '
import os, shutil, sys
root, dst = sys.argv[1], sys.argv[2]
for rel in sys.stdin.buffer.read().decode().split("\0"):
    if not rel or not os.path.lexists(os.path.join(root, rel)):
        continue
    out = os.path.join(dst, rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(os.path.join(root, rel), out, follow_symlinks=False)
' "$ROOT" "$SRC"
}

# ---- 2. Tracked junk ----------------------------------------------------------------------
stage_junk() {
  local pat='\.terraform\.lock\.hcl$|\.tfstate|\.tfplan$|(^|/)tf\.plan$|\.zip$|(^|/)\.terraform/|diagram-icons/.+\.(svg|png|zip)$|^\.diagram-assets/|^\.audit/|^\.feedback/|^\.plans/|^\.evidence/|^outputs/|(^|/)\.env($|\.)|(^|/)terraform\.tfvars$|(^|/)backend\.tf$|\.pem$|\.key$'
  local hits
  hits="$(cd "$ROOT" && git ls-files | grep -E "$pat" | grep -vx 'agentcore/diagram-icons/README.md' || true)"
  if [ -n "$hits" ]; then record "tracked junk" FAIL "$(printf '%s' "$hits" | tr '\n' ' ')"
  else record "tracked junk" PASS "0 tracked lock, state, plan, zip, icon, .audit, outputs or .env files"; fi
}

# ---- 3. Relative links in tracked Markdown -------------------------------------------------
stage_links() {
  local out
  if out="$(cd "$SRC" && PY - <<'PYEOF'
import os, re, subprocess, sys
link = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
bad, n = [], 0
for dirpath, _, files in os.walk("."):
    for f in files:
        if not f.endswith(".md"):
            continue
        p = os.path.join(dirpath, f)
        text = open(p, encoding="utf-8", errors="replace").read()
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in link.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            n += 1
            full = os.path.normpath(os.path.join(dirpath, path))
            if not os.path.exists(full):
                bad.append(f"{p[2:]} -> {target}")
print(n)
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
PYEOF
)"; then record "relative links" PASS "$(printf '%s' "$out" | head -1) links, 0 broken"
  else record "relative links" FAIL "$(printf '%s' "$out" | sed 1d | tr '\n' ';')"; fi
}

# ---- 4. Checkov ---------------------------------------------------------------------------
checkov_run() {  # checkov_run <label> <args...>
  local label="$1"; shift
  local out rc=0
  out="$(cd "$SRC" && "$SCAN_VENV/bin/checkov" "$@" --compact --skip-download 2>&1)" || rc=$?
  printf '%s\n' "$out" > "$LOG/checkov-$(printf '%s' "$label" | tr '/ ' '__').log"
  local summary
  summary="$(printf '%s\n' "$out" | grep -E 'Passed checks' | sed -E 's/.*Passed checks: ([0-9]+), Failed checks: ([0-9]+), Skipped checks: ([0-9]+).*/\1\/\2\/\3/' | paste -sd' ' - || true)"
  if [ "$rc" = 0 ] && ! printf '%s\n' "$out" | grep -Eq 'Failed checks: [1-9]|Parsing errors'; then
    record "checkov $label" PASS "passed/failed/skipped ${summary:-no checks}"
  else
    record "checkov $label" FAIL "exit $rc; passed/failed/skipped ${summary:-?}"
  fi
}
stage_checkov() {
  local d
  for d in agentcore/terraform agentcore/terraform/platform agentcore/terraform/modules/private-network \
           agentcore/terraform/modules/shared-identity agentcore/terraform/modules/backend-bootstrap; do
    checkov_run "$d" -d "$d" --framework terraform --skip-path '\.tftest\.hcl$'
  done
  checkov_run "dockerfile,secrets,ci" -d . --framework dockerfile,secrets,github_actions
}

# ---- 5. Bandit (every severity and confidence level) --------------------------------------
stage_bandit() {
  local rc=0
  (cd "$SRC" && "$SCAN_VENV/bin/bandit" -q -r agents frontdoor agentcore scripts integrations plugin \
     -f json -o "$LOG/bandit.json") || rc=$?
  local n
  n="$(PY -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["results"]))' "$LOG/bandit.json" 2>/dev/null || echo '?')"
  if [ "$rc" = 0 ] && [ "$n" = 0 ]; then record "bandit (all levels)" PASS "0 issues"
  else record "bandit (all levels)" FAIL "exit $rc; $n issues"; fi
}

# ---- 6. pip-audit (every tracked requirements file) ---------------------------------------
stage_pip_audit() {
  local r rc
  for r in $(cd "$SRC" && git -C "$ROOT" ls-files '*requirements*.txt'); do
    rc=0
    (cd "$SRC" && "$SCAN_VENV/bin/pip-audit" --progress-spinner off -r "$r" > "$LOG/pip-audit-$(printf '%s' "$r" | tr '/' '_').log" 2>&1) || rc=$?
    if [ "$rc" = 0 ]; then record "pip-audit $r" PASS "0 known vulnerabilities"
    else record "pip-audit $r" FAIL "exit $rc (see log)"; tail -5 "$LOG/pip-audit-$(printf '%s' "$r" | tr '/' '_').log" >&2; fi
  done
}

# ---- 7. detect-secrets (tracked files; the integrity ledger is excluded, see scan-results.md) -
stage_detect_secrets() {
  local n
  # The copy holds the tracked files only, so --all-files scans exactly the tracked tree.
  n="$(cd "$SRC" && "$SCAN_VENV/bin/detect-secrets" scan --all-files \
        --exclude-files '^governance/integrity\.yaml$' \
        | PY -c 'import json,sys; r=json.load(sys.stdin)["results"]; [print(k, file=sys.stderr) for k in r]; print(sum(len(v) for v in r.values()))')"
  if [ "$n" = 0 ]; then record "detect-secrets" PASS "0 unallowlisted secrets"
  else record "detect-secrets" FAIL "$n potential secrets"; fi
}

# ---- 8. KICS (whole tracked tree) ----------------------------------------------------------
stage_kics() {
  local rc=0
  mkdir -p "$WORK/kics"
  docker run --rm -v "$SRC":/path:ro -v "$WORK/kics":/out "$KICS_IMAGE" \
    scan -p /path -o /out --report-formats json --no-progress \
    > "$LOG/kics.log" 2>&1 || rc=$?
  local summary
  summary="$(PY -c 'import json,sys; d=json.load(open(sys.argv[1])); c=d["severity_counters"]; print(d["total_counter"], " ".join(f"{k}={v}" for k,v in sorted(c.items())))' "$WORK/kics/results.json" 2>/dev/null || echo '? (no results.json)')"
  if [ "$rc" = 0 ] && [ "${summary%% *}" = 0 ]; then record "kics" PASS "0 unsuppressed ($summary)"
  else record "kics" FAIL "exit $rc; total $summary"; fi
}

# ---- 9. Images: local builds, Trivy, Grype, resvg-py wheel SBOM ----------------------------
filter_findings() {  # filter_findings <trivy|grype> <json> ; prints "<n> <details>" for unaccepted findings
  PY - "$1" "$2" "$ALLOWLIST" <<'PYEOF'
import datetime, json, sys, yaml
kind, path, allow = sys.argv[1], sys.argv[2], sys.argv[3]
entries = (yaml.safe_load(open(allow)) or {}).get("residuals") or []
ok = {(e["id"], e["package"], str(e["version"])) for e in entries}
found = []
d = json.load(open(path))
if kind == "trivy":
    for r in d.get("Results") or []:
        for v in r.get("Vulnerabilities") or []:
            found.append((v["VulnerabilityID"], v["PkgName"], v["InstalledVersion"], v.get("Severity", "?")))
else:
    for m in d.get("matches") or []:
        v, a = m["vulnerability"], m["artifact"]
        found.append((v["id"], a["name"], a["version"], v.get("severity", "?")))
bad = sorted({f for f in found if f[:3] not in ok})
accepted = sorted({f for f in found if f[:3] in ok})
print(len(bad), "unaccepted;", len(accepted), "allowlisted", " ".join("/".join(b) for b in bad))
PYEOF
}
stage_allowlist_dates() {
  local out
  if out="$(PY - "$ALLOWLIST" <<'PYEOF'
import datetime, sys, yaml
entries = (yaml.safe_load(open(sys.argv[1])) or {}).get("residuals") or []
today = datetime.date.today()
need = ("id", "package", "version", "status", "reason", "evidence", "approved_by", "approved_on", "review_by")
bad = []
for e in entries:
    miss = [k for k in need if not e.get(k)]
    if miss:
        bad.append(f"{e.get('id', '?')} missing {','.join(miss)}")
        continue
    rb = e["review_by"] if isinstance(e["review_by"], datetime.date) else datetime.date.fromisoformat(str(e["review_by"]))
    if today > rb:
        bad.append(f"{e['id']} review_by {rb} has passed")
print(f"{len(entries)} entries" + ("; " + "; ".join(bad) if bad else ""))
sys.exit(1 if bad else 0)
PYEOF
)"; then record "accepted-residuals.yaml" PASS "$out, all within review_by"
  else record "accepted-residuals.yaml" FAIL "$out"; fi
}
scan_image() {  # scan_image <tag>
  local img="$1" rc=0 res
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$TRIVY_CACHE":/root/.cache/trivy \
    "$TRIVY_IMAGE" image --quiet --scanners vuln --format json \
    --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL "$img" > "$LOG/trivy-${img//[:\/]/_}.json" 2>"$LOG/trivy-${img//[:\/]/_}.err" || rc=$?
  if [ "$rc" = 0 ] && res="$(filter_findings trivy "$LOG/trivy-${img//[:\/]/_}.json")" && [ "${res%% *}" = 0 ]; then
    record "trivy $img" PASS "$res"
  else record "trivy $img" FAIL "exit $rc; ${res:-no report}"; fi
  rc=0; res=""
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$GRYPE_CACHE":/cache -e GRYPE_DB_CACHE_DIR=/cache \
    "$GRYPE_IMAGE" "$img" -o json > "$LOG/grype-${img//[:\/]/_}.json" 2>"$LOG/grype-${img//[:\/]/_}.err" || rc=$?
  if [ "$rc" = 0 ] && res="$(filter_findings grype "$LOG/grype-${img//[:\/]/_}.json")" && [ "${res%% *}" = 0 ]; then
    record "grype $img" PASS "$res"
  else record "grype $img" FAIL "exit $rc; ${res:-no report}"; fi
}
stage_images() {
  mkdir -p "$TRIVY_CACHE" "$GRYPE_CACHE"
  local zips flavours face fl
  zips="$(find "$ROOT/agentcore/diagram-icons" -maxdepth 1 -type f -name '*.zip' | wc -l | tr -d ' ')"
  flavours="noicons"
  if [ "$zips" = 1 ]; then flavours="noicons icons"
  elif [ "$REQUIRE_ICONS" = 1 ]; then record "icons images" FAIL "--require-icons: $zips zips staged in agentcore/diagram-icons/ (need exactly 1)"
  else record "icons images" SKIP "$zips zips staged; only the no-icons images are built"; fi
  for fl in $flavours; do
    for face in orchestrator mcp_runtime; do
      local tag="gac-$face:gate-$fl" ctx="$ROOT" rc=0
      # The no-icons build uses the tracked copy (no zip in it); the icons build reads the
      # deployer-staged, gitignored zip, so it builds from the repository root.
      [ "$fl" = noicons ] && ctx="$SRC"
      docker build --pull --platform linux/arm64 -f "$ctx/agentcore/app/$face/Dockerfile" -t "$tag" "$ctx" \
        > "$LOG/build-$face-$fl.log" 2>&1 || rc=$?
      if [ "$rc" = 0 ]; then record "build $tag" PASS "linux/arm64"; scan_image "$tag"
      else record "build $tag" FAIL "exit $rc (see log)"; tail -5 "$LOG/build-$face-$fl.log" >&2; fi
    done
  done
  # resvg-py ships a CycloneDX SBOM of its vendored Rust crates inside the wheel.
  local rc=0 wheel res
  mkdir -p "$WORK/resvg"
  "$SCAN_VENV/bin/python" -m pip download --quiet "resvg-py==$RESVG_PY_VERSION" --no-deps --only-binary=:all: \
    --platform manylinux_2_17_aarch64 -d "$WORK/resvg" > "$LOG/resvg-download.log" 2>&1 || rc=$?
  wheel="$(find "$WORK/resvg" -name '*.whl' | head -1)"
  if [ "$rc" = 0 ] && [ -n "$wheel" ] && PY -c '
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
name = [n for n in z.namelist() if n.endswith(".cyclonedx.json")][0]
open(sys.argv[2], "wb").write(z.read(name))' "$wheel" "$WORK/resvg/sbom.cdx.json"; then
    rc=0
    docker run --rm -v "$WORK/resvg":/s:ro -v "$TRIVY_CACHE":/root/.cache/trivy "$TRIVY_IMAGE" \
      sbom --quiet --format json /s/sbom.cdx.json > "$LOG/trivy-resvg-sbom.json" 2>"$LOG/trivy-resvg-sbom.err" || rc=$?
    if [ "$rc" = 0 ] && res="$(filter_findings trivy "$LOG/trivy-resvg-sbom.json")" && [ "${res%% *}" = 0 ]; then
      record "trivy sbom resvg-py $RESVG_PY_VERSION" PASS "$res"
    else record "trivy sbom resvg-py $RESVG_PY_VERSION" FAIL "exit $rc; ${res:-no report}"; fi
  else record "trivy sbom resvg-py $RESVG_PY_VERSION" FAIL "could not download or read the wheel SBOM"; fi
}

# ---- Run ----------------------------------------------------------------------------------
echo "GAC security gate: $(cd "$ROOT" && git rev-parse --short HEAD) ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
ensure_venv
copy_tracked
stage_junk
stage_links
stage_checkov
stage_bandit
stage_pip_audit
stage_detect_secrets
stage_allowlist_dates
if [ "$STATIC" = 1 ]; then record "kics" SKIP "--static (CI runs KICS as its own job)"; else stage_kics; fi
if [ "$SKIP_IMAGES" = 1 ]; then record "images (build, trivy, grype)" SKIP "--static/--skip-images"; else stage_images; fi
echo
echo "Summary"
printf '%s' "$RESULTS"
[ "$KEEP_WORK" = 1 ] && echo "Logs: $LOG"
if [ "$FAILED" = 1 ]; then echo "GATE: FAIL"; exit 1; fi
echo "GATE: PASS"
