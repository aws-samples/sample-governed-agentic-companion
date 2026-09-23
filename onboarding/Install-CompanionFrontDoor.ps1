<#
=============================================================================
 Governed Agentic Companion front door installer (idempotent) — Windows PowerShell
=============================================================================
 Run this from inside the cloned governed-agentic-companion repo:
     powershell -ExecutionPolicy Bypass -File onboarding\Install-CompanionFrontDoor.ps1

 The engine runs under a POSIX venv, so on Windows this script drives the venv
 setup + status check through WSL (you need WSL with Python 3 available), then
 installs the routing pointers on the Windows side.

 What it does (all safe to re-run):
   1. Locates the repo root from this script's own path (no absolute paths).
   2. Creates/refreshes the engine venv + installs requirements (via WSL bash).
   3. Runs `main.py status` to confirm governance integrity + knowledge load.
   4. Optionally installs routing pointers into the PARENT workspace root for
      BOTH front doors:
        - Kiro        -> <parent>\.kiro\steering\companion-pointer.md (inclusion: auto)
        - Claude Code -> <parent>\CLAUDE.md (an appended, clearly-marked block)

 It NEVER overwrites your project's own steering or CLAUDE.md content, and NEVER
 deploys anything.
=============================================================================
#>
$ErrorActionPreference = 'Stop'

# Write-Info is discouraged by PSScriptAnalyzer (PSAvoidUsingWriteHost): it can't be
# captured/redirected and fails where there is no host. Use the information stream via a
# thin helper so progress is still visible by default but is a proper, redirectable stream.
function Write-Info { param([string]$Message)
  Write-Information -MessageData $Message -InformationAction Continue }


# --- 1. Self-locate the repo root -------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$RepoName  = Split-Path -Leaf $RepoRoot

Write-Info "==> Governed Agentic Companion front door installer"
Write-Info "    repo root : $RepoRoot"

if (-not (Test-Path (Join-Path $RepoRoot 'main.py'))) {
  Write-Error "main.py not found under $RepoRoot. Run this from the governed-agentic-companion repo."
}

# --- 2 + 3. venv + requirements + status (via WSL) --------------------------
# Pass the path with FORWARD slashes: PowerShell/native-arg handling strips
# backslashes, so a raw "C:\Users\..." reaches wslpath as "C:Users..." and fails.
$RepoRootFwd = $RepoRoot -replace '\\', '/'
$RepoRootWsl = (& wsl wslpath -a "$RepoRootFwd" 2>$null)
if ($RepoRootWsl) { $RepoRootWsl = ("$RepoRootWsl").Trim() }
if (-not $RepoRootWsl) {
  Write-Error "Could not translate '$RepoRoot' to a WSL path via wslpath. Is WSL installed and on PATH? Try: wsl wslpath -a '$RepoRootFwd'"
}
Write-Info "==> Delegating venv setup + status check to WSL bash (repo: $RepoRootWsl)..."
& wsl bash "$RepoRootWsl/onboarding/install-companion-frontdoor.sh"

# --- 4. Optional routing pointers into the parent workspace -----------------
$Parent           = (Resolve-Path (Join-Path $RepoRoot '..')).Path
$KiroPointerSrc   = Join-Path $ScriptDir 'companion-pointer.md'
$ClaudeSnippetSrc = Join-Path $ScriptDir 'companion-claude-pointer.md'
$KiroSteering     = Join-Path $Parent '.kiro\steering'
$KiroPointerDst   = Join-Path $KiroSteering 'companion-pointer.md'
$ClaudeDst        = Join-Path $Parent 'CLAUDE.md'
$ClaudeBegin      = '<!-- COMPANION-FRONT-DOOR:BEGIN -->'
$ClaudeEnd        = '<!-- COMPANION-FRONT-DOOR:END -->'

function Install-KiroPointer {
  New-Item -ItemType Directory -Force -Path $KiroSteering | Out-Null
  if (Test-Path $KiroPointerDst) {
    Write-Info "    Kiro pointer already present at $KiroPointerDst (leaving as-is)."
  } else {
    (Get-Content -Raw $KiroPointerSrc) -replace '__COMPANION_REPO__', $RepoName |
      Set-Content -NoNewline $KiroPointerDst
    Write-Info "    Installed Kiro pointer: $KiroPointerDst"
  }
}

function Install-ClaudePointer {
  $block = (Get-Content -Raw $ClaudeSnippetSrc) -replace '__COMPANION_REPO__', $RepoName
  if ((Test-Path $ClaudeDst) -and (Select-String -Path $ClaudeDst -SimpleMatch $ClaudeBegin -Quiet)) {
    Write-Info "    Claude Code pointer already present in $ClaudeDst (leaving as-is)."
    return
  }
  $prefix = ''
  if (Test-Path $ClaudeDst) { $prefix = "`n" }
  Add-Content -Path $ClaudeDst -Value ($prefix + $ClaudeBegin + "`n" + $block + "`n" + $ClaudeEnd)
  Write-Info "    Installed Claude Code pointer block into: $ClaudeDst"
}

Write-Info ""
if ($Parent -eq $RepoRoot) {
  Write-Info "==> Repo appears to be the workspace root itself; no parent pointer needed."
} else {
  $ans = Read-Host "Parent workspace found at $Parent. Install companion routing pointers for BOTH front doors (Kiro + Claude Code)? [y/N]"
  if ($ans -eq 'y' -or $ans -eq 'Y') {
    Install-KiroPointer
    Install-ClaudePointer
  } else {
    Write-Info "==> Skipped. Copy $KiroPointerSrc -> $KiroPointerDst and append $ClaudeSnippetSrc into $ClaudeDst later."
  }
}

Write-Info ""
Write-Info "==> Done. Add the $RepoName folder to your Kiro workspace and pick @companion-orchestrator,"
Write-Info "    or in Claude Code install the plugin from plugin\ (the CLAUDE.md block routes requests)."
