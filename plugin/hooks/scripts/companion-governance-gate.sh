#!/usr/bin/env bash
# Governed Agentic Companion — Claude Code plugin Stop-hook enforcement backstop.
#
# The plugin's sub-agents (plugin/agents/*.md) ship `disallowedTools: Bash` and never call the
# Python gate directly — their governance is prompt-level self-gating plus the read-only MCP
# boundary. This Stop hook is the CODE-LEVEL backstop for the plugin front door: it runs the
# always-on GovernanceGate over the sub-agent's completed response, mirroring the CLI engine's
# deterministic seam (orchestrator -> gate_output).
#
# Contract:
#   • stdin  : the Stop-hook event JSON (Claude Code passes event context on stdin).
#   • exit 0 : allow (clean, OR text not locatable / engine unavailable — fail-open).
#   • exit 2 : BLOCK — an absolute Tenet 1/3/6 violation; block notice on stderr.
# On the pass path it surfaces the governance-outcome footer on stderr so every plugin
# interaction shows the transparency block, not just blocked ones.
#
# Engine location: the Python engine (main.py + agents/) is NOT bundled inside the plugin. By
# default we resolve it as the parent of ${CLAUDE_PLUGIN_ROOT} (the repo the plugin lives in).
# Override with GAC_ENGINE_REPO when the plugin is installed away from the engine checkout. If
# the engine/venv cannot be found, the hook fails open (never blocks on infrastructure absence).

set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
REPO="${GAC_ENGINE_REPO:-$(cd "$PLUGIN_ROOT/.." && pwd)}"

# Prefer the engine venv; fall back to python3 on PATH.
if [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
else
  PY="$(command -v python3 || true)"
fi

# No usable interpreter, or no engine entrypoint -> fail open (do not block the user).
[ -n "$PY" ] || exit 0
[ -f "$REPO/main.py" ] || exit 0

PAYLOAD="$(cat)"

# Extract the assistant's final response text from the Stop event JSON. Robust multi-shape
# parser. The payload is passed via env (not stdin) because the heredoc owns python's stdin.
RESPONSE="$(HOOK_PAYLOAD="$PAYLOAD" "$PY" - <<'PY'
import os, json
raw = os.environ.get("HOOK_PAYLOAD", "")
try:
    data = json.loads(raw)
except Exception:
    print(raw)
    raise SystemExit(0)

def pick(d):
    for k in ("response", "output", "message", "assistant_message", "last_message", "text"):
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

# Nothing to check -> allow (fail-open backstop).
[ -z "${RESPONSE//[$'\t\r\n ']/}" ] && exit 0

# Run the deterministic gate over the response. The companion CLI gate accepts stdin + --footer;
# it exits 2 when the response hits an absolute Tenet 1/3/6 block and withholds the text.
GATED="$(printf '%s' "$RESPONSE" | (cd "$REPO" && "$PY" main.py gate --footer 2>/dev/null))"
STATUS=$?

if [ "$STATUS" -eq 2 ]; then
  {
    echo "GovernanceGate BLOCKED the response (plugin front-door enforcement backstop):"
    echo "$GATED"
  } >&2
  exit 2
fi

# Pass: surface just the governance-outcome footer on stderr (the response itself was already
# shown to the user by Claude Code).
FOOTER_BLOCK="$(printf '%s\n' "$GATED" | awk '/^── Governance Outcome ──$/{f=1} f{print}')"
[ -n "$FOOTER_BLOCK" ] && printf '%s\n' "$FOOTER_BLOCK" >&2

exit 0
