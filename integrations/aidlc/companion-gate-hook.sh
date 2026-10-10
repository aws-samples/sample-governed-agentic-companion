#!/usr/bin/env bash
# Governed Agentic Companion — governance gate as a Stop-style hook for an agentic SDLC
# (AI-DLC or any harness with a Stop / post-response hook model).
#
# This is Track 2 of the GAC v2 uplift: GAC is the UNBYPASSABLE governance substrate that an
# agentic SDLC runs on top of. AI-DLC's own rigor is per-profile and its sensors are advisory;
# this hook adds the always-on floor it does not have — it blocks a response/artifact that
# claims a deploy/mutation (Tenet 1), claims a write to a read-only system of record (Tenet 3),
# or leaks secret material (Tenet 6), no matter which SDLC stage produced it.
#
# It mirrors the existing Claude-plugin backstop (plugin/hooks/scripts/companion-governance-gate.sh)
# so the two front doors behave identically: run the deterministic GovernanceGate over the
# completed response, block on an absolute violation, otherwise surface the governance footer.
#
# Contract (matches AI-DLC's command-hook convention and our plugin hook):
#   • stdin  : the hook event JSON (the harness passes response/context on stdin), OR raw text.
#   • exit 0 : allow (clean, OR text not locatable / engine unavailable — FAIL-OPEN).
#   • exit 2 : BLOCK — an absolute Tenet 1/3/6 violation; block notice on stderr.
# On the pass path the governance-outcome footer is written to stderr so every gated interaction
# shows the transparency block.
#
# Engine location: set GAC_ENGINE_REPO to the governed-agentic-companion checkout. If unset, we
# resolve it as two levels up from this script (…/integrations/aidlc/ -> repo root). If the
# engine/venv cannot be found, the hook FAILS OPEN (never blocks on infrastructure absence) —
# the gate is a floor, not a tripwire that breaks the user's workflow when GAC isn't installed.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${GAC_ENGINE_REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

if [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
else
  PY="$(command -v python3 || true)"
fi

# No interpreter or no engine entrypoint -> fail open (do not disrupt the SDLC workflow).
[ -n "$PY" ] || exit 0
[ -f "$REPO/main.py" ] || exit 0

PAYLOAD="$(cat)"

# Extract the artifact/response text from the hook event JSON (robust multi-shape parser);
# if the payload is not JSON, treat the whole stdin as the text to gate.
TEXT="$(HOOK_PAYLOAD="$PAYLOAD" "$PY" - <<'PY'
import os, json
raw = os.environ.get("HOOK_PAYLOAD", "")
try:
    data = json.loads(raw)
except Exception:
    print(raw)
    raise SystemExit(0)

def pick(d):
    # Common response/artifact keys across harnesses (AI-DLC, Claude Code, generic).
    for k in ("response", "output", "message", "assistant_message", "last_message",
              "text", "artifact", "diff", "content"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    msgs = d.get("messages")
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict):
                c = m.get("content") or m.get("text")
                if isinstance(c, str) and c.strip():
                    return c
                if isinstance(c, list):
                    parts = [b.get("text", "") for b in c if isinstance(b, dict)]
                    joined = "\n".join(p for p in parts if p)
                    if joined.strip():
                        return joined
    return ""

print(pick(data) if isinstance(data, dict) else "")
PY
)"

# Nothing to check -> allow (fail-open).
[ -z "${TEXT//[$'\t\r\n ']/}" ] && exit 0

# Run the deterministic gate. `main.py gate --footer` exits 2 on an absolute Tenet 1/3/6 block
# and withholds the text; it tags the audit trail with this front door via GAC_GATE_FRONT_DOOR.
GATED="$(printf '%s' "$TEXT" | (cd "$REPO" && GAC_GATE_FRONT_DOOR="aidlc-hook" "$PY" main.py gate --footer 2>/dev/null))"
STATUS=$?

if [ "$STATUS" -eq 2 ]; then
  {
    echo "GovernanceGate BLOCKED this artifact (agentic-SDLC governance substrate):"
    echo "$GATED"
  } >&2
  exit 2
fi

# Pass: surface the governance-outcome footer on stderr for transparency.
FOOTER_BLOCK="$(printf '%s\n' "$GATED" | awk '/^── Governance Outcome ──$/{f=1} f{print}')"
[ -n "$FOOTER_BLOCK" ] && printf '%s\n' "$FOOTER_BLOCK" >&2

exit 0
