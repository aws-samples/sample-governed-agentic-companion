"""Gateway JWT refresh helper for the HTTP-MCP front door (Kiro / Claude Code).

The gateway HTTP path carries a STATIC `Authorization: Bearer ${GAC_GATEWAY_TOKEN}` that the IDE
captures once at launch and cannot refresh, so it 401s after the ~1h token expiry. This keeps it
fresh, reusing CognitoTokenProvider (one token code path). Three modes:

  print       mint a fresh token to stdout (for `export GAC_GATEWAY_TOKEN=$(...)` or `-H`)
  write-env   upsert GAC_GATEWAY_TOKEN (+ GAC_GATEWAY_URL) into a gitignored env file (0600)
  daemon      keep the env file fresh in a loop before each expiry (human-run)

Logs go to stderr so `print` is safe to capture in $(...).
"""
from __future__ import annotations

import argparse
import logging
import os
import stat
import sys
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cognito_token import CognitoTokenProvider  # noqa: E402

logger = logging.getLogger("companion.frontdoor.gateway_token")
DEFAULT_ENV_FILE = "~/.companion-frontdoor.env"
# These are the NAMES of environment variables, not secret values. GAC_GATEWAY_TOKEN is the
# env var the front door reads/writes the (externally-provided) gateway JWT from; no credential
# is embedded here. (Bandit B105 false positive on the all-caps "*_TOKEN" identifier.)
TOKEN_VAR = "GAC_GATEWAY_TOKEN"  # nosec B105
URL_VAR = "GAC_GATEWAY_URL"


def _env_path(explicit: Optional[str]) -> Path:
    return Path(explicit or os.getenv("GAC_FRONTDOOR_ENV") or DEFAULT_ENV_FILE).expanduser()


def _upsert(text: str, var: str, value: str) -> str:
    if "'" in value:
        raise ValueError(f"Refusing to write a value containing a single quote for {var}.")
    line = f"export {var}='{value}'"
    out, replaced = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(f"export {var}=") or s.startswith(f"{var}="):
            if not replaced:
                out.append(line); replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(line)
    return "\n".join(out) + "\n"


def write_env_token(token: str, env_path: Path, url: Optional[str] = None) -> None:
    existing = env_path.read_text() if env_path.exists() else ""
    updated = _upsert(existing, TOKEN_VAR, token)
    if url:
        updated = _upsert(updated, URL_VAR, url)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(updated)
    try:
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        logger.warning("Could not chmod 0600 on %s", env_path)


def _next_sleep(expires_in: int, skew: int, floor: int = 30) -> int:
    return max(floor, int(expires_in) - int(skew))


def run(mode: str, *, env_file=None, skew: int = 120, provider=None, out=None,
        _sleep: Callable[[float], None] = time.sleep, _max_iterations: Optional[int] = None) -> int:
    out = out if out is not None else sys.stdout
    provider = provider or CognitoTokenProvider(skew_seconds=skew)
    env_path = _env_path(env_file)
    url = os.getenv(URL_VAR)

    if mode == "print":
        out.write(provider.get_token()); out.flush(); return 0
    if mode == "write-env":
        write_env_token(provider.get_token(), env_path, url=url); return 0
    if mode == "daemon":
        i = 0
        while True:
            provider.invalidate()
            write_env_token(provider.get_token(), env_path, url=url)
            expires_in = provider.last_ttl or 3600
            i += 1
            if _max_iterations is not None and i >= _max_iterations:
                return 0
            _sleep(_next_sleep(expires_in, skew))
    raise ValueError(f"Unknown mode: {mode}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gateway_token.py")
    ap.add_argument("mode", choices=["print", "write-env", "daemon"])
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--skew", type=int, default=120)
    args = ap.parse_args(argv)
    logging.basicConfig(level=os.getenv("GAC_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        return run(args.mode, env_file=args.env_file, skew=args.skew)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
