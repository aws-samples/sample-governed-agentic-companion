#!/usr/bin/env bash
# YAML validation — parses every tracked *.yaml/*.yml file and fails on any that
# does not parse. Used by CI (.gitlab-ci.yml) and runnable by hand:
#   ./scripts/validate_yaml.sh
#
# Uses the project's Python + PyYAML. Exit 0 = all valid, Exit 1 = a parse error.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR" || exit 1

# Prefer the venv python if present (local dev); fall back to python3 (CI image).
if [ -x "$DIR/.venv/bin/python" ]; then
  PY="$DIR/.venv/bin/python"
else
  PY="python3"
fi

FAIL=0
while IFS= read -r f; do
  if ! "$PY" -c "import sys, yaml; yaml.safe_load(open(sys.argv[1]))" "$f" 2>/tmp/yamlerr; then
    echo "INVALID: $f"
    sed 's/^/    /' /tmp/yamlerr
    FAIL=1
  fi
done < <(git ls-files '*.yaml' '*.yml')

if [ "$FAIL" -ne 0 ]; then
  echo "YAML VALIDATION: FAIL — one or more files did not parse."
  exit 1
fi

echo "YAML VALIDATION: PASS — all tracked YAML files parse."
exit 0
