#!/usr/bin/env bash
# =============================================================================
# Governed Agentic Companion front door installer (idempotent) — macOS/Linux/WSL
# =============================================================================
# Run this from inside the cloned governed-agentic-companion repo:
#     bash onboarding/install-companion-frontdoor.sh
#
# What it does (all safe to re-run):
#   1. Locates the repo root (this script's own location — no absolute paths).
#   2. Creates/refreshes the engine Python venv (.venv) and installs requirements.
#   3. Runs `main.py status` to confirm governance integrity + knowledge load.
#   4. Optionally installs routing pointers into the PARENT workspace root so the
#      base assistant routes companion requests here, for BOTH front doors:
#        • Kiro        → <parent>/.kiro/steering/companion-pointer.md (inclusion: auto)
#        • Claude Code → <parent>/CLAUDE.md   (an appended, clearly-marked block)
#
# It NEVER overwrites your project's own steering or CLAUDE.md content, and NEVER
# deploys anything. The engine produces review-ready artifacts; a human deploys.
# =============================================================================
set -uo pipefail

# --- 1. Self-locate the repo root (portable) --------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # onboarding/ -> repo root

echo "==> Governed Agentic Companion front door installer"
echo "    repo root : $REPO_ROOT"

if [ ! -f "$REPO_ROOT/main.py" ]; then
  echo "ERROR: main.py not found under $REPO_ROOT. Run this from the governed-agentic-companion repo." >&2
  exit 1
fi

# --- 2. Python venv + requirements (idempotent) -----------------------------
cd "$REPO_ROOT" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "==> Creating Python venv (.venv)..."
  python3 -m venv .venv
else
  echo "==> venv already present — reusing."
fi
echo "==> Installing/updating requirements..."
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt pytest

# --- 3. Governance integrity + knowledge load check -------------------------
echo "==> Verifying engine (main.py status)..."
if ./.venv/bin/python main.py status; then
  echo "    engine OK."
else
  echo "WARNING: 'main.py status' returned non-zero. Review the output above." >&2
fi

# --- 4. Optional: install routing pointers into the parent workspace --------
PARENT="$(cd "$REPO_ROOT/.." && pwd)"
REPO_NAME="$(basename "$REPO_ROOT")"
KIRO_POINTER_SRC="$SCRIPT_DIR/companion-pointer.md"
CLAUDE_SNIPPET_SRC="$SCRIPT_DIR/companion-claude-pointer.md"
KIRO_POINTER_DST="$PARENT/.kiro/steering/companion-pointer.md"
CLAUDE_DST="$PARENT/CLAUDE.md"
CLAUDE_MARK_BEGIN="<!-- COMPANION-FRONT-DOOR:BEGIN -->"
CLAUDE_MARK_END="<!-- COMPANION-FRONT-DOOR:END -->"

# install_kiro_pointer: copy the auto-inclusion steering pointer (never overwrite).
install_kiro_pointer() {
  mkdir -p "$(dirname "$KIRO_POINTER_DST")"
  if [ -f "$KIRO_POINTER_DST" ]; then
    echo "    Kiro pointer already present at $KIRO_POINTER_DST (leaving as-is)."
  else
    sed "s/__COMPANION_REPO__/$REPO_NAME/g" "$KIRO_POINTER_SRC" > "$KIRO_POINTER_DST"
    echo "    Installed Kiro pointer: $KIRO_POINTER_DST"
  fi
}

# install_claude_pointer: append a clearly-marked block to the parent CLAUDE.md,
# creating the file if absent. Idempotent: if our marked block already exists,
# leave it. We NEVER modify content outside our BEGIN/END markers.
install_claude_pointer() {
  local block
  block="$(sed "s/__COMPANION_REPO__/$REPO_NAME/g" "$CLAUDE_SNIPPET_SRC")"
  if [ -f "$CLAUDE_DST" ] && grep -qF "$CLAUDE_MARK_BEGIN" "$CLAUDE_DST"; then
    echo "    Claude Code pointer already present in $CLAUDE_DST (leaving as-is)."
    return
  fi
  # Decide the leading newline BEFORE opening the append redirect, so we never read and
  # write "$CLAUDE_DST" in the same block (ShellCheck SC2094).
  local prefix=""
  [ -f "$CLAUDE_DST" ] && prefix=$'\n'
  {
    [ -n "$prefix" ] && printf '%s' "$prefix"
    printf '%s\n' "$CLAUDE_MARK_BEGIN"
    printf '%s\n' "$block"
    printf '%s\n' "$CLAUDE_MARK_END"
  } >> "$CLAUDE_DST"
  echo "    Installed Claude Code pointer block into: $CLAUDE_DST"
}

echo ""
if [ "$PARENT" = "$REPO_ROOT" ]; then
  echo "==> Repo appears to be the workspace root itself; no parent pointer needed."
else
  echo "A parent workspace was detected at: $PARENT"
  echo "Install companion routing pointers for BOTH front doors (Kiro + Claude Code)?"
  echo "  • Kiro:        $KIRO_POINTER_DST"
  echo "  • Claude Code: $CLAUDE_DST (appended block; your content untouched)"
  echo "[y/N]"
  read -r ans
  if [ "${ans:-N}" = "y" ] || [ "${ans:-N}" = "Y" ]; then
    install_kiro_pointer
    install_claude_pointer
  else
    echo "==> Skipped pointer install. You can add them later by copying:"
    echo "    $KIRO_POINTER_SRC  ->  $KIRO_POINTER_DST"
    echo "    (and appending $CLAUDE_SNIPPET_SRC into $CLAUDE_DST)"
  fi
fi

echo ""
echo "==> Done. Next steps:"
echo "    • Kiro: add the $REPO_NAME folder to your workspace, then pick @companion-orchestrator"
echo "      (or just ask a question the specialists cover)."
echo "    • Claude Code: the CLAUDE.md block routes requests to this front door; install the"
echo "      plugin from plugin/ for the packaged agent set."
echo "    • Cloud: to reach a deployed AgentCore runtime as an MCP tool, use"
echo "      onboarding/companion-kiro-mcp.example.json (local bridge) or"
echo "      companion-kiro-gateway.example.json (gateway). See frontdoor/README.md."
