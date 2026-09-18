#!/usr/bin/env bash
# Governed Agentic Companion — thin CLI wrapper. Prefers the local venv if present.
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" main.py "$@"
