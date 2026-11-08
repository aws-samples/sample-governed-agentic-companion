"""Deployed-stack end-to-end harness (design D23, D24). HUMAN-RUN ONLY.

A human runs this against a deployed stack after the apply. No agent, CI job or test runs it
against AWS: the pytest suite drives it with stub transports only. It is read-only (Tenets 1/3):
it sends authenticated and unauthenticated requests, mints Cognito tokens for the configured
test users, and writes local result files. It never deploys, changes or deletes anything.

Order (deny first; owner decision 2):

1. Deny tests. Public stack: D1 to D4 go to the gateway /mcp (tools/list) AND to the
   orchestrator /invocations; D5 sends the entitled user's token straight to the MCP runtime.
   Private stack: from outside the VPC T2 (gateway), T4a (orchestrator), T5b (SigV4 to the MCP
   runtime) and T9 (SigV4 to Memory, when enabled); from inside the VPC, through the probe, D1
   (anonymous mode), D2, D3a and D3b. D4a is not run on the private stack (no M2M client in
   private mode). Any allowed deny test is STOP-SHIP: exit 5, and nothing else runs.
2. Allowed tests (skipped with --deny-only): ask_companion and companion_kb, then every
   example-KB golden prompt (tests/golden/diagram_prompts.yaml, fixture "") through every door.
3. Parity (D23): within this run every door must give the same artefact per prompt; with
   --compare, against another stack's run (equal catalogue: drawio and SVG sha256 per view
   equal; a different catalogue is reported as "KB differs", not a failure).

Token table (D24.2): D1 no Authorization header; D2 the fixed 32-character string below (passes
the probe's token-shape check, is not a JWT); D3a the access token of U0 (GAC_E2E_UNENTITLED_
USERNAME, a user in no group) minted with this stack's front-door client; D3b the same for U1
(GAC_E2E_OTHERGROUP_USERNAME, optional, a test-only group); D4a a client-credentials token of
this stack's M2M client (GAC_E2E_M2M_CLIENT_SECRET); D4b the entitled user's token minted with
another flavor's front-door client (--other-client-id, GAC_E2E_OTHER_CLIENT_SECRET, optional);
D5 the entitled user's token sent directly to the MCP runtime.

Inputs: --stack public|private, --outputs <terraform output -json file>, --probe <function>
(private), --deny-only, --prompts, --doors, --out-dir (default outputs/e2e/<stack>-<utc>/,
gitignored), --compare <e2e-<stack>.json of another run>, --other-client-id. Users and secrets
come from the environment (GAC_COGNITO_USERNAME, GAC_E2E_UNENTITLED_USERNAME,
GAC_E2E_OTHERGROUP_USERNAME, GAC_COGNITO_CLIENT_SECRET, GAC_E2E_M2M_CLIENT_SECRET,
GAC_E2E_OTHER_CLIENT_SECRET); each password is read with one no-echo prompt per user
(GAC_COGNITO_PASSWORD is honoured for the entitled user). Tokens, passwords and secrets are
never printed and never written: result bodies are reduced to a short message with every known
secret value redacted.

Exit codes: 0 every test passed; 1 a test failed or parity broke; 2 configuration error
(before any request); 5 stop-ship (a deny test was allowed).

Prerequisites: pip install -r requirements-frontdoor.txt, AWS credentials for the Cognito admin
sign-in and the Lambda / SigV4 calls (for example AWS_PROFILE). See docs/DEPLOY.md.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
FRONTDOOR = ROOT / "frontdoor"
for _p in (str(ROOT), str(FRONTDOOR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_STOP_SHIP = 0, 1, 2, 5

#: D2: 32 characters, three dot-separated parts of ten "a", "b" and "c" (assembled here, so no
#: credential-shaped literal is stored); matches the probe's _TOKEN_RE, is not a JWT.
MALFORMED_TOKEN = ".".join(c * 10 for c in "abc")
#: The interceptor's pinned 403 body message (D26.3).
NOT_ENTITLED = "GAC: not entitled"
#: Denial markers observed in AgentCore authorizer and resource-policy answers (docs/DEPLOY.md
#: sandbox record). The authorizer's claim-rule denial markers are UNVERIFIED until the first
#: deny run: record them from that run's JSON and pin them here.
DENIAL_MARKERS = ("Authorization error", "Insufficient permissions", "explicit deny",
                  "Authorization method mismatch", "Unauthorized", "AccessDenied",
                  "not authorized", "Invalid token", "invalid_token")
ASK_PROMPT = "what is the deployment policy?"
DOORS = {"public": ("bridge-gateway", "bridge-runtime", "fetch-gateway", "fetch-runtime"),
         "private": ("bridge-gateway", "bridge-runtime", "fetch-gateway", "fetch-runtime",
                     "probe")}
#: Doors that force planner "deterministic" (D23.1). diagram-fetch and the probe use the stack's
#: configured planner; their artefacts join the parity check only when the report says
#: "deterministic".
FORCED_DOORS = ("bridge-gateway", "bridge-runtime")
BODY_CAP = 65536
DETAIL_CAP = 200

_REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-[0-9]$")
_CLIENT_RE = re.compile(r"^[A-Za-z0-9]{1,128}$")
_FUNCTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MEMORY_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_USER_RE = re.compile(r"^[\w.@+-]{1,128}$")


class ConfigError(RuntimeError):
    """A missing or invalid input (exit 2, before any request). The text is safe to show."""


class StopShip(RuntimeError):
    """A deny test was allowed."""


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------

@dataclass
class Config:
    stack: str
    region: str
    gateway_url: str
    orchestrator_arn: str
    mcp_runtime_arn: str
    pool_id: str
    client_id: str
    m2m_client_id: str
    hosted_domain: str
    probe: str
    memory_id: str
    user: str
    unentitled_user: str
    othergroup_user: str
    other_client_id: str
    client_secret: str
    m2m_secret: str
    other_client_secret: str


def _output_values(doc: dict) -> Dict[str, object]:
    """`terraform output -json` ({name: {value, ...}}) as {name: value}."""
    if not isinstance(doc, dict):
        raise ConfigError("--outputs is not a `terraform output -json` document")
    return {k: (v.get("value") if isinstance(v, dict) and "value" in v else v)
            for k, v in doc.items()}


def _check(name: str, value: object, pattern: re.Pattern, *, required: bool = True) -> str:
    text = value if isinstance(value, str) else ""
    if not text and not required:
        return ""
    if not pattern.match(text):
        raise ConfigError(f"terraform output {name} is missing or has an unexpected shape")
    return text


def load_config(args, env=None) -> Config:
    env = os.environ if env is None else env
    stack = args.stack
    user = env.get("GAC_COGNITO_USERNAME", "")
    unentitled = env.get("GAC_E2E_UNENTITLED_USERNAME", "")
    if not unentitled:
        raise ConfigError("GAC_E2E_UNENTITLED_USERNAME is required (U0: a test user in no group)")
    if not user:
        raise ConfigError("GAC_COGNITO_USERNAME is required (the entitled user)")
    if unentitled == user:
        raise ConfigError("GAC_E2E_UNENTITLED_USERNAME must differ from GAC_COGNITO_USERNAME")
    othergroup = env.get("GAC_E2E_OTHERGROUP_USERNAME", "")
    for label, u in (("GAC_COGNITO_USERNAME", user), ("GAC_E2E_UNENTITLED_USERNAME", unentitled),
                     ("GAC_E2E_OTHERGROUP_USERNAME", othergroup)):
        if u and not _USER_RE.match(u):
            raise ConfigError(f"{label} has an unexpected shape")
    if othergroup and othergroup in (user, unentitled):
        raise ConfigError("GAC_E2E_OTHERGROUP_USERNAME must differ from the other two users")
    try:
        doc = json.loads(Path(args.outputs).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError("--outputs must be a readable `terraform output -json` file") from None
    out = _output_values(doc)
    region = _check("aws_region", out.get("aws_region"), _REGION_RE)
    network = out.get("network_access")
    if network in ("public", "private") and network != stack:
        raise ConfigError(f"--stack {stack} but terraform output network_access is {network}")
    r = re.escape(region)
    runtime_re = re.compile(rf"^arn:aws(-[a-z]+)*:bedrock-agentcore:{r}:[0-9]{{12}}:"
                            r"runtime/[A-Za-z0-9_-]{1,100}$")
    gateway_re = re.compile(rf"^https://[a-z0-9][a-z0-9-]{{0,99}}\.gateway\.bedrock-agentcore\."
                            rf"{r}\.amazonaws\.com/mcp$")
    pool_re = re.compile(rf"^{r}_[A-Za-z0-9]{{1,55}}$")
    domain_re = re.compile(rf"^https://[a-z0-9-]{{1,63}}\.auth\.{r}\.amazoncognito\.com$")
    cfg = Config(
        stack=stack, region=region,
        gateway_url=_check("gateway_url", out.get("gateway_url"), gateway_re),
        orchestrator_arn=_check("orchestrator_runtime_arn", out.get("orchestrator_runtime_arn"),
                                runtime_re),
        mcp_runtime_arn=_check("mcp_runtime_arn", out.get("mcp_runtime_arn"), runtime_re),
        pool_id=_check("cognito_user_pool_id", out.get("cognito_user_pool_id"), pool_re),
        client_id=_check("cognito_frontdoor_client_id", out.get("cognito_frontdoor_client_id"),
                         _CLIENT_RE),
        # N11: the private stack has no M2M client; an empty value is not a config error.
        m2m_client_id=_check("cognito_m2m_client_id", out.get("cognito_m2m_client_id"),
                             _CLIENT_RE, required=False),
        hosted_domain=_check("cognito_hosted_ui_domain", out.get("cognito_hosted_ui_domain"),
                             domain_re, required=False),
        probe="", memory_id=_check("memory_id", out.get("memory_id"), _MEMORY_RE, required=False),
        user=user, unentitled_user=unentitled, othergroup_user=othergroup,
        other_client_id=args.other_client_id or "",
        client_secret=env.get("GAC_COGNITO_CLIENT_SECRET", ""),
        m2m_secret=env.get("GAC_E2E_M2M_CLIENT_SECRET", ""),
        other_client_secret=env.get("GAC_E2E_OTHER_CLIENT_SECRET", ""),
    )
    if not cfg.client_secret:
        raise ConfigError("GAC_COGNITO_CLIENT_SECRET is required (the front-door client secret)")
    if cfg.other_client_id and (not _CLIENT_RE.match(cfg.other_client_id)
                                or not cfg.other_client_secret):
        raise ConfigError("--other-client-id needs a client id and GAC_E2E_OTHER_CLIENT_SECRET")
    if stack == "public" and cfg.m2m_client_id:
        if not cfg.m2m_secret:
            raise ConfigError("GAC_E2E_M2M_CLIENT_SECRET is required for D4a on the public stack")
        if not cfg.hosted_domain:
            raise ConfigError("terraform output cognito_hosted_ui_domain is needed for D4a")
    if stack == "private":
        probe = args.probe or out.get("private_access_probe_function_name") or ""
        if not isinstance(probe, str) or not _FUNCTION_RE.match(probe):
            raise ConfigError("the private stack needs --probe <function> (or the "
                              "private_access_probe_function_name output)")
        cfg.probe = probe
    return cfg


# ---------------------------------------------------------------------------------------------
# Transport (live). Tests replace it with a stub that has the same methods.
# ---------------------------------------------------------------------------------------------

@dataclass
class DoorResult:
    exit_code: int
    text: str
    received_bytes: Optional[int] = None


class StaticTokens:
    """A token provider holding one already-minted token (never printed)."""

    def __init__(self, token: str):
        self._token = token

    def get_token(self) -> str:
        return self._token

    def invalidate(self) -> None:
        pass


def _bridge_errors():
    from cognito_token import CognitoConfigError
    from diagram_relay import RelayConfigError
    from runtime_client import RuntimeConfigError
    return (RelayConfigError, RuntimeConfigError, CognitoConfigError)


class LiveTransport:
    """HTTPS, Cognito, Lambda and SigV4 calls through the front-door helpers. Read-only."""

    def post(self, url: str, headers: dict, body: bytes) -> Tuple[int, dict, str]:
        try:
            import requests
        except ImportError:
            from upstream_errors import REQUESTS_MISSING
            raise ConfigError(REQUESTS_MISSING) from None
        with requests.post(url, headers=headers, data=body, timeout=30, allow_redirects=False,
                           stream=True) as r:
            buf = bytearray()
            for chunk in r.iter_content(8192):
                buf.extend(chunk)
                if len(buf) > BODY_CAP:
                    break
            return (r.status_code, {k.lower(): v for k, v in r.headers.items()},
                    bytes(buf[:BODY_CAP]).decode("utf-8", "replace"))

    def mint_user(self, cfg: Config, client_id: str, client_secret: str, username: str,
                  password: str) -> str:
        from cognito_token import _mapped, _secret_hash
        try:
            import boto3
        except ImportError:
            from upstream_errors import BOTO3_MISSING
            raise ConfigError(BOTO3_MISSING) from None
        try:
            resp = boto3.client("cognito-idp", region_name=cfg.region).admin_initiate_auth(
                UserPoolId=cfg.pool_id, ClientId=client_id, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": username, "PASSWORD": password,
                                "SECRET_HASH": _secret_hash(username, client_id, client_secret)})
        except Exception as e:  # noqa: BLE001 - mapped to a message-free error
            raise _mapped(e) from None
        return resp["AuthenticationResult"]["AccessToken"]

    def mint_client(self, cfg: Config, client_id: str, client_secret: str) -> str:
        from upstream_errors import UpstreamError
        status, _, body = self._form(f"{cfg.hosted_domain}/oauth2/token", client_id,
                                     client_secret)
        try:
            token = json.loads(body).get("access_token") if status == 200 else None
        except ValueError:
            token = None
        if not isinstance(token, str) or not token:
            raise UpstreamError("Cognito", "auth", status, "client-credentials token not issued")
        return token

    def _form(self, url, client_id, secret):
        import requests
        r = requests.post(url, data={"grant_type": "client_credentials"},
                          auth=(client_id, secret), timeout=30, allow_redirects=False)
        return r.status_code, {}, r.text[:BODY_CAP]

    def probe(self, cfg: Config, payload: dict) -> dict:
        import probe_fetch
        return probe_fetch._invoke_doc(cfg.probe, probe_fetch._boto_invoker(cfg.region),
                                       json.dumps(payload).encode("utf-8"))

    def sigv4(self, cfg: Config, check: str) -> Tuple[bool, str, bool]:
        """(allowed, error code, explicit deny named) for a SigV4 read call from here."""
        import boto3
        client = boto3.client("bedrock-agentcore", region_name=cfg.region)
        session = "gac-e2e-" + uuid.uuid4().hex
        try:
            if check == "mcp-runtime":
                client.invoke_agent_runtime(
                    agentRuntimeArn=cfg.mcp_runtime_arn, qualifier="DEFAULT",
                    runtimeSessionId=session, contentType="application/json",
                    accept="application/json, text/event-stream",
                    payload=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
            else:
                client.list_events(memoryId=cfg.memory_id, sessionId=session,
                                   actorId="gac-e2e", maxResults=1)
        except Exception as e:  # noqa: BLE001 - only the code and the marker are recorded
            resp = getattr(e, "response", None) or {}
            err = resp.get("Error", {}) if isinstance(resp, dict) else {}
            return False, str(err.get("Code") or type(e).__name__), \
                "explicit deny" in str(err.get("Message", ""))
        return True, "", False

    def ask(self, cfg: Config, door: str, tool: str, prompt: str, token: str) -> str:
        if door == "probe":
            doc = self.probe(cfg, {"token": token})
            return "probe ok" if doc.get("ok") is True else ""
        if door == "bridge-gateway":
            import anyio
            from diagram_relay import call_gateway_tool
            key = "query" if tool == "companion_kb" else "prompt"
            text = anyio.run(lambda: call_gateway_tool(tool, {key: prompt, "environment": "dev"},
                                                       token_provider=StaticTokens(token)))
            try:
                return str(json.loads(text).get("response", ""))
            except (ValueError, AttributeError):
                return text
        from runtime_client import CompanionRuntimeClient
        return str(CompanionRuntimeClient(StaticTokens(token)).invoke(prompt).get("response", ""))

    def draw(self, cfg: Config, door: str, prompt: str, out_dir: Path, token: str,
             password: str) -> DoorResult:
        from upstream_errors import classify
        os.environ["GAC_DIAGRAM_WORKSPACE_ROOT"] = str(out_dir.parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        if door.startswith("fetch-"):
            return self._fetch(door.split("-", 1)[1], prompt, out_dir, password)
        if door == "probe":
            import probe_fetch
            try:
                text, status = probe_fetch.fetch_via_probe(
                    cfg.probe, prompt, out_dir=str(out_dir), token_provider=StaticTokens(token),
                    invoker=probe_fetch._boto_invoker(cfg.region), workspace_root=out_dir.parent)
            except probe_fetch.ProbeConfigError as e:
                return DoorResult(EXIT_CONFIG, f"Not fetched: {e}")
            except Exception as e:  # noqa: BLE001 - one clean line
                return DoorResult(3, str(classify(e, "probe")))
            return DoorResult({"saved": 0, "refused": 1, "declined": 4}[status], text)
        import anyio
        import mcp_bridge
        from diagram_relay import call_gateway_tool
        from runtime_client import CompanionRuntimeClient, UpstreamTooLarge
        upstream = door.split("-", 1)[1]
        args = mcp_bridge._draw_args(prompt, "dev", "", "", "deterministic")
        try:
            if upstream == "gateway":
                raw = anyio.run(lambda: call_gateway_tool("draw_diagram", args,
                                                          token_provider=StaticTokens(token)))
                size = len(raw.encode("utf-8"))
            else:
                client = CompanionRuntimeClient(StaticTokens(token))
                raw = client.invoke(prompt, "dev", action="diagram", include_artifacts=True,
                                    planner="deterministic")
                size = len(json.dumps(raw).encode("utf-8"))
        except UpstreamTooLarge:
            return DoorResult(1, "upstream body over the cap; nothing parsed")
        except _bridge_errors() as e:
            return DoorResult(EXIT_CONFIG, f"Not fetched: {e}")
        except Exception as e:  # noqa: BLE001 - one clean line
            return DoorResult(3, str(classify(e, upstream)))
        res = mcp_bridge.build_draw_result(raw, out_dir=str(out_dir),
                                           workspace_root=out_dir.parent)
        return DoorResult(4 if res.declined else (1 if res.refused else 0), res.text, size)

    def _fetch(self, upstream: str, prompt: str, out_dir: Path, password: str) -> DoorResult:
        """`./run.sh diagram-fetch --upstream <u>` in this process (its own token mint from the
        GAC_COGNITO_* env; the password is set in this process's env only for the call)."""
        import main as cli
        ns = argparse.Namespace(request=prompt, upstream=upstream, via_probe=None,
                                out=str(out_dir), overwrite=False, environment="dev")
        had = "GAC_COGNITO_PASSWORD" in os.environ
        os.environ["GAC_COGNITO_PASSWORD"] = password
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = cli.cmd_diagram_fetch(ns)
        finally:
            if not had:
                os.environ.pop("GAC_COGNITO_PASSWORD", None)
        return DoorResult(code, buf.getvalue().rstrip("\n"))


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def _redact(text: str, secrets: List[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text


def _short(text: str, secrets: List[str]) -> str:
    from upstream_errors import message_from_body
    return _redact(str(message_from_body(text or "") or ""), secrets)[:DETAIL_CAP]


def _marker(headers: dict, body: str) -> str:
    if headers.get("www-authenticate"):
        return "www-authenticate"
    if NOT_ENTITLED in (body or ""):
        return NOT_ENTITLED
    for m in DENIAL_MARKERS:
        if m in (body or ""):
            return m
    return ""


def classify_http(status: int, headers: dict, body: str) -> Tuple[str, str]:
    """(verdict, marker): allowed (2xx: stop-ship for a deny test), denied (401/403 with an
    identifiable marker), denied-unidentified (401/403 without one) or error."""
    if 200 <= status < 300:
        return "allowed", ""
    if status in (401, 403):
        m = _marker(headers, body)
        return ("denied", m) if m else ("denied-unidentified", "")
    return "error", ""


def _rpc_list() -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode("utf-8")


def _runtime_url(cfg: Config, arn: str, qualifier: bool = False) -> str:
    url = (f"https://bedrock-agentcore.{cfg.region}.amazonaws.com/runtimes/"
           f"{urllib.parse.quote(arn, safe='')}/invocations")
    return url + ("?qualifier=DEFAULT" if qualifier else "")


def _headers(token: Optional[str], *, mcp: bool, session: bool) -> dict:
    h = {"Content-Type": "application/json"}
    if mcp:
        h["Accept"] = "application/json, text/event-stream"
    if session:
        h["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] = "gac-e2e-" + uuid.uuid4().hex
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    return h


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artefact(prompt_id, door_dir: Path, result: DoorResult) -> dict:
    """The D23.1 comparison artefact of one prompt at one door, read from the files the door's
    writer saved (each sha256-verified by diagram_writer)."""
    art = {"prompt_id": prompt_id, "status": "declined" if result.exit_code == 4 else
           ("rendered" if result.exit_code == 0 else "failed"),
           "catalogue_sha256": "", "selected": [], "planner": "", "views": []}
    if result.exit_code == 4:
        art["decline_sha256"] = hashlib.sha256(result.text.strip().encode("utf-8")).hexdigest()
        return art
    reports = sorted(door_dir.glob("*/*.report.json")) if door_dir.is_dir() else []
    loaded = []
    for rp in reports:
        try:
            loaded.append((rp, json.loads(rp.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    if not loaded:
        return art
    plan = loaded[0][1].get("plan") or {}
    order = [v.get("name") for v in plan.get("views") or [] if isinstance(v, dict)]
    loaded.sort(key=lambda x: order.index(x[0].parent.name) if x[0].parent.name in order
                else len(order))
    art["catalogue_sha256"] = str(plan.get("catalogue_sha256", ""))
    art["selected"] = sorted(str(q) for q in plan.get("selected") or [])
    art["planner"] = str(plan.get("planner", ""))
    for rp, rep in loaded:
        q = rep.get("quality") or {}
        shas = {}
        for ext in ("drawio", "svg", "png", "report.json"):
            f = rp.parent / f"{rp.parent.name}.{ext}"
            shas["report" if ext == "report.json" else ext] = _sha(f) if f.is_file() else ""
        art["views"].append({
            "id": str(rep.get("topic", "")),
            "quality": {"score": q.get("score"), "pass": q.get("pass") is True,
                        "hard": sum(1 for f in q.get("findings") or []
                                    if isinstance(f, dict)
                                    and f.get("severity") in ("fail", "fix"))},
            "sha256": shas})
    return art


def _parity_key(art: dict, *, png: bool = True) -> dict:
    views = [{"id": v["id"], "quality": v["quality"],
              "sha256": {k: s for k, s in v["sha256"].items() if png or k in ("drawio", "svg")}}
             for v in art.get("views", [])]
    return {"status": art["status"], "catalogue_sha256": art.get("catalogue_sha256", ""),
            "selected": art.get("selected", []), "views": views,
            "decline_sha256": art.get("decline_sha256", "")}


def door_parity(prompt_id, arts: Dict[str, dict]) -> dict:
    """D23.2 within one stack: every door equal (PNG included). A door that does not force the
    deterministic planner joins only when its report says deterministic."""
    used, skipped = {}, []
    for door, art in arts.items():
        if door not in FORCED_DOORS and art["status"] == "rendered" \
                and art.get("planner") != "deterministic":
            skipped.append(door)
            continue
        used[door] = _parity_key(art)
    keys = list(used.values())
    ok = all(k == keys[0] for k in keys[1:]) if keys else True
    return {"prompt_id": prompt_id, "ok": ok, "doors": sorted(used),
            "skipped_planner_not_forced": skipped,
            "differs": sorted(d for d, k in used.items() if keys and k != keys[0])}


def compare_runs(mine: dict, other: dict) -> List[dict]:
    """D23.2 across stacks: same catalogue -> drawio and SVG per view equal; a different
    catalogue -> 'KB differs' (not a failure). PNG is never compared across stacks."""
    def first(run):
        out = {}
        for rec in run.get("prompts") or []:
            arts = rec.get("artefacts") or {}
            for door in sorted(arts):
                out.setdefault(rec.get("prompt_id"), arts[door])
        return out
    a, b = first(mine), first(other)
    rows = []
    for pid in sorted(set(a) & set(b), key=str):
        x, y = a[pid], b[pid]
        cx, cy = x.get("catalogue_sha256", ""), y.get("catalogue_sha256", "")
        if cx and cy and cx != cy:
            rows.append({"prompt_id": pid, "ok": True, "note": "KB differs"})
            continue
        same = _parity_key(x, png=False) == _parity_key(y, png=False)
        rows.append({"prompt_id": pid, "ok": same, "note": "" if same else "artefacts differ"})
    return rows


def load_prompts(path: Path) -> List[dict]:
    import yaml
    rows = yaml.safe_load(path.read_text(encoding="utf-8")).get("rows") or []
    access_key = ("AK" + "IA") + ("Q" * 12) + "7RST"  # assembled at run time, as in the tests
    return [dict(r, prompt=str(r["prompt"]).replace("__ACCESS_KEY__", access_key))
            for r in rows if isinstance(r, dict) and not r.get("fixture")]


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------

class Harness:
    def __init__(self, cfg: Config, transport, *, out_dir: Path, deny_only: bool,
                 doors: List[str], prompts: List[dict],
                 prompt: Callable[[str], str] = getpass.getpass, echo=print):
        self.cfg, self.t, self.out_dir = cfg, transport, out_dir
        self.deny_only, self.doors, self.prompts = deny_only, doors, prompts
        self._prompt, self._echo = prompt, echo
        self.secrets: List[str] = [cfg.client_secret, cfg.m2m_secret, cfg.other_client_secret]
        self.deny: List[dict] = []
        self.ask: List[dict] = []
        self.records: List[dict] = []
        self.parity: List[dict] = []
        self.compare: List[dict] = []
        self._passwords: Dict[str, str] = {}
        self._tokens: Dict[str, str] = {}

    # -- credentials (never printed) --
    def _password(self, user: str) -> str:
        if user not in self._passwords:
            value = os.environ.get("GAC_COGNITO_PASSWORD", "") if user == self.cfg.user else ""
            if not value:
                value = self._prompt(f"Cognito password for {user} (not echoed): ")
            if not value:
                raise ConfigError(f"no password given for {user}")
            self._passwords[user] = value
            self.secrets.append(value)
        return self._passwords[user]

    def _keep(self, key: str, token: str) -> str:
        self._tokens[key] = token
        self.secrets.append(token)
        return token

    def _user_token(self, user: str, client: Optional[Tuple[str, str]] = None) -> str:
        """The user's access token, minted once per (user, client); client = (id, secret),
        default this stack's front-door client."""
        client_id, secret = client or (self.cfg.client_id, self.cfg.client_secret)
        key = f"{user}@{client_id}"
        if key not in self._tokens:
            self._keep(key, self.t.mint_user(self.cfg, client_id, secret, user,
                                             self._password(user)))
        return self._tokens[key]

    # -- recording --
    def _record(self, test: str, target: str, verdict: str, *, status=None, marker="",
                detail="", reason="") -> dict:
        rec = {"test": test, "target": target, "verdict": verdict, "status": status,
               "marker": marker, "detail": _redact(detail, self.secrets)[:DETAIL_CAP],
               "reason": reason}
        self.deny.append(rec)
        bits = [f"HTTP {status}"] if status is not None else []
        bits += [b for b in (marker, reason, rec["detail"] if verdict != "denied" else "") if b]
        self._echo(f"{test} -> {target}: {verdict}" + (f" ({'; '.join(bits)})" if bits else ""))
        if verdict == "allowed":
            raise StopShip(f"{test} -> {target}")
        return rec

    def _not_run(self, test: str, target: str, reason: str) -> None:
        self._record(test, target, "not-run", reason=reason)

    def _http(self, test: str, target: str, url: str, token: Optional[str], *, mcp: bool,
              session: bool, body: bytes, want_403: bool = False) -> None:
        try:
            status, headers, text = self.t.post(url, _headers(token, mcp=mcp, session=session),
                                                body)
        except ConfigError:
            raise
        except Exception as e:  # noqa: BLE001 - the type name only
            self._record(test, target, "error", detail=type(e).__name__)
            return
        verdict, marker = classify_http(status, headers, text)
        if want_403 and verdict == "denied" and status != 403:
            # A 401 means the JWT was rejected first: the resource policy is untested.
            verdict = "inconclusive"
        self._record(test, target, verdict, status=status, marker=marker,
                     detail="" if verdict == "allowed" else _short(text, self.secrets))

    def _mint(self, test: str, target: str, fn: Callable[[], str]) -> Optional[str]:
        try:
            return fn()
        except ConfigError:
            raise
        except Exception as e:  # noqa: BLE001 - a safe one-line reason
            self._record(test, target, "error", detail=_redact(str(e), self.secrets))
            return None

    # -- deny tests --
    def _public_deny(self) -> None:
        c = self.cfg
        targets = (("gateway", c.gateway_url, True, False, _rpc_list()),
                   ("orchestrator", _runtime_url(c, c.orchestrator_arn), False, True,
                    json.dumps({"prompt": "e2e deny check", "environment": "dev"}).encode()))
        tokens: List[Tuple[str, Optional[Callable[[], str]], str]] = [
            ("D1 anonymous", None, ""), ("D2 malformed", lambda: MALFORMED_TOKEN, ""),
            ("D3a no group", lambda: self._user_token(c.unentitled_user), ""),
            ("D3b other group", (lambda: self._user_token(c.othergroup_user))
             if c.othergroup_user else None,
             "" if c.othergroup_user else "GAC_E2E_OTHERGROUP_USERNAME (U1) not configured"),
            ("D4a M2M", (lambda: self._keep("m2m", self.t.mint_client(c, c.m2m_client_id,
                                                                      c.m2m_secret)))
             if c.m2m_client_id else None,
             "" if c.m2m_client_id else "no M2M client in this stack"),
            ("D4b other flavor", (lambda: self._user_token(c.user, (c.other_client_id,
                                                                   c.other_client_secret)))
             if c.other_client_id else None,
             "" if c.other_client_id else "--other-client-id not configured"),
        ]
        for test, mint, reason in tokens:
            if reason:
                for name, *_ in targets:
                    self._not_run(test, name, reason)
                continue
            token = None if mint is None else self._mint(test, "both", mint)
            if mint is not None and token is None:
                continue
            for name, url, mcp, session, body in targets:
                self._http(test, name, url, token, mcp=mcp, session=session, body=body)
        token = self._mint("D5 direct MCP", "mcp-runtime", lambda: self._user_token(c.user))
        if token is not None:
            self._http("D5 direct MCP", "mcp-runtime", _runtime_url(c, c.mcp_runtime_arn, True),
                       token, mcp=True, session=True, body=_rpc_list())

    def _probe_deny(self, test: str, payload_fn: Callable[[], dict]) -> None:
        try:
            payload = payload_fn()
            doc = self.t.probe(self.cfg, payload)
        except ConfigError:
            raise
        except Exception as e:  # noqa: BLE001 - a safe one-line reason
            self._record(test, "gateway (probe)", "error", detail=_redact(str(e), self.secrets))
            return
        steps = [s for s in doc.get("steps") or [] if isinstance(s, dict)]
        listed = [s for s in steps if s.get("step") == "tools/list"]
        if any(isinstance(s.get("status"), int) and 200 <= s["status"] < 300 for s in listed):
            self._record(test, "gateway (probe)", "allowed", status=listed[0]["status"])
        denied = [s for s in steps if s.get("status") in (401, 403)]
        if denied:
            body = str(denied[0].get("body", ""))
            marker = _marker({}, body)
            self._record(test, "gateway (probe)", "denied" if marker else "denied-unidentified",
                         status=denied[0]["status"], marker=marker,
                         detail=_short(body, self.secrets))
        else:
            self._record(test, "gateway (probe)", "error",
                         detail=_redact(str(doc.get("error") or "no 401/403 step"), self.secrets))

    def _sigv4_deny(self, test: str, check: str) -> None:
        try:
            allowed, code, explicit = self.t.sigv4(self.cfg, check)
        except Exception as e:  # noqa: BLE001 - the type name only
            self._record(test, check, "error", detail=type(e).__name__)
            return
        if allowed:
            self._record(test, check, "allowed")
        denied = code.startswith("AccessDenied")
        self._record(test, check, "denied" if denied and explicit else
                     ("denied-unidentified" if denied else "error"),
                     marker="explicit deny" if explicit else "", detail=code)

    def _private_deny(self) -> None:
        c = self.cfg
        token = self._mint("T2", "gateway", lambda: self._user_token(c.user))
        if token is not None:
            self._http("T2 outside", "gateway", c.gateway_url, token, mcp=True, session=False,
                       body=_rpc_list(), want_403=True)
            self._http("T4a outside", "orchestrator", _runtime_url(c, c.orchestrator_arn), token,
                       mcp=False, session=True, want_403=True,
                       body=json.dumps({"prompt": "e2e deny check",
                                        "environment": "dev"}).encode())
        self._sigv4_deny("T5b outside", "mcp-runtime")
        if c.memory_id:
            self._sigv4_deny("T9 outside", "memory")
        else:
            self._not_run("T9 outside", "memory", "memory not enabled")
        self._probe_deny("D1 anonymous", lambda: {"anonymous": True})
        self._probe_deny("D2 malformed", lambda: {"token": MALFORMED_TOKEN})
        self._probe_deny("D3a no group", lambda: {"token": self._user_token(c.unentitled_user)})
        if c.othergroup_user:
            self._probe_deny("D3b other group",
                             lambda: {"token": self._user_token(c.othergroup_user)})
        else:
            self._not_run("D3b other group", "gateway (probe)",
                          "GAC_E2E_OTHERGROUP_USERNAME (U1) not configured")
        self._not_run("D4a M2M", "orchestrator", "no M2M client in private mode")
        self._not_run("D4b other flavor", "both", "public stack only")
        self._not_run("D5 direct MCP", "mcp-runtime", "public stack only (T5b covers it)")

    # -- allowed tests --
    def _ask(self) -> None:
        token = self._user_token(self.cfg.user)
        doors = [d for d in ("bridge-gateway", "bridge-runtime", "probe") if d in self.doors]
        for door in doors:
            for tool in (("ask_companion",) if door == "probe" else
                         ("ask_companion", "companion_kb")):
                try:
                    text = self.t.ask(self.cfg, door, tool, ASK_PROMPT, token)
                except Exception as e:  # noqa: BLE001 - one safe line
                    text, err = "", _redact(str(e), self.secrets)[:DETAIL_CAP]
                else:
                    err = ""
                ok = bool(text) and ("Response blocked by the governance gate" not in text) \
                    and (door == "probe" or "Gate status" in text)
                self.ask.append({"door": door, "tool": tool, "ok": ok, "detail": err})
                self._echo(f"ask {tool} via {door}: {'pass' if ok else 'FAIL'}")

    def _prompts(self) -> None:
        token = self._user_token(self.cfg.user)
        password = self._password(self.cfg.user)
        for row in self.prompts:
            pid, arts, doors = row.get("id"), {}, {}
            for door in self.doors:
                d = self.out_dir / door / str(pid)
                t0 = time.monotonic()
                res = self.t.draw(self.cfg, door, row["prompt"], d, token, password)
                elapsed = round(time.monotonic() - t0, 3)
                art = artefact(pid, d, res)
                want = 4 if row.get("expect") == "decline" else 0
                views = [v["id"] for v in art["views"]]
                ok = res.exit_code == want and (want == 4 or not row.get("views")
                                               or views == list(row["views"]))
                files = sorted(str(p.relative_to(self.out_dir)) for p in d.rglob("*")
                               if p.is_file()) if d.is_dir() else []
                doors[door] = {"exit": res.exit_code, "ok": ok, "elapsed_s": elapsed,
                               "received_bytes": res.received_bytes, "files": files,
                               "views": views}
                arts[door] = art
                self._echo(f"prompt {pid} via {door}: exit {res.exit_code}, "
                           f"{'pass' if ok else 'FAIL'}")
            par = door_parity(pid, arts)
            self.parity.append(par)
            self.records.append({"prompt_id": pid, "expect": row.get("expect"),
                                 "doors": doors, "artefacts": arts, "parity_ok": par["ok"]})

    # -- the run --
    def run(self, compare: Optional[dict] = None) -> int:
        code = EXIT_OK
        try:
            if self.cfg.stack == "public":
                self._public_deny()
            else:
                self._private_deny()
            if not self.deny_only:
                self._ask()
                self._prompts()
                if compare is not None:
                    self.compare = compare_runs({"prompts": self.records}, compare)
        except StopShip as e:
            self._echo(f"STOP-SHIP: a deny test was allowed ({e}). Nothing else was run.")
            code = EXIT_STOP_SHIP
        if code == EXIT_OK:
            failed = any(r["verdict"] not in ("denied", "not-run") for r in self.deny) \
                or any(not a["ok"] for a in self.ask) \
                or any(not d["ok"] for r in self.records for d in r["doors"].values()) \
                or any(not p["ok"] for p in self.parity) or any(not c["ok"] for c in self.compare)
            code = EXIT_FAIL if failed else EXIT_OK
        self.write(code)
        return code

    def summary(self, code: int) -> dict:
        return {"schema": "gac.e2e/1", "stack": self.cfg.stack, "exit": code,
                "deny_only": self.deny_only, "doors": self.doors, "deny": self.deny,
                "not_run": [f"{r['test']} -> {r['target']}: {r['reason']}" for r in self.deny
                            if r["verdict"] == "not-run"],
                "ask": self.ask, "prompts": self.records, "parity": self.parity,
                "compare": self.compare}

    def write(self, code: int) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        doc = self.summary(code)
        text = _redact(json.dumps(doc, indent=2, sort_keys=True), self.secrets)
        (self.out_dir / f"e2e-{self.cfg.stack}.json").write_text(text + "\n", encoding="utf-8")
        (self.out_dir / f"e2e-{self.cfg.stack}.md").write_text(
            _redact(markdown(doc), self.secrets), encoding="utf-8")
        self._echo(f"Results: {self.out_dir / ('e2e-' + self.cfg.stack + '.json')} (exit {code})")


def markdown(doc: dict) -> str:
    lines = [f"# E2E results: {doc['stack']} stack (exit {doc['exit']})", "",
             "## Deny tests (run first)", "", "| Test | Target | Verdict | HTTP | Marker |",
             "|---|---|---|---|---|"]
    for r in doc["deny"]:
        lines.append(f"| {r['test']} | {r['target']} | {r['verdict']} | "
                     f"{r['status'] if r['status'] is not None else ''} | "
                     f"{r['marker'] or r['reason']} |")
    if doc["not_run"]:
        lines += ["", "Not run:", ""] + [f"- {n}" for n in doc["not_run"]]
    if doc["ask"]:
        lines += ["", "## Q&A", "", "| Door | Tool | Result |", "|---|---|---|"]
        lines += [f"| {a['door']} | {a['tool']} | {'pass' if a['ok'] else 'FAIL'} |"
                  for a in doc["ask"]]
    if doc["prompts"]:
        lines += ["", "## Golden prompts", "", "| Prompt | Door | Exit | Views | Scores | Files "
                  "| Parity |", "|---|---|---|---|---|---|---|"]
        for rec in doc["prompts"]:
            for door, d in rec["doors"].items():
                art = rec["artefacts"][door]
                scores = ", ".join(str(v["quality"]["score"]) for v in art["views"])
                lines.append(f"| {rec['prompt_id']} | {door} | {d['exit']} | "
                             f"{', '.join(d['views'])} | {scores} | {len(d['files'])} | "
                             f"{'ok' if rec['parity_ok'] else 'DIFFERS'} |")
    if doc["compare"]:
        lines += ["", "## Cross-stack parity", "", "| Prompt | Result |", "|---|---|"]
        lines += [f"| {c['prompt_id']} | {'ok' if c['ok'] else 'DIFFERS'} {c['note']} |"
                  for c in doc["compare"]]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deployed-stack E2E harness (human-run, read-only)")
    p.add_argument("--stack", choices=("public", "private"), required=True)
    p.add_argument("--outputs", required=True, help="terraform output -json > <file>")
    p.add_argument("--probe", default="", help="private stack: the probe function name")
    p.add_argument("--deny-only", action="store_true")
    p.add_argument("--prompts", default=str(ROOT / "tests" / "golden" / "diagram_prompts.yaml"))
    p.add_argument("--doors", default="", help="comma-separated subset of the stack's doors")
    p.add_argument("--out-dir", default="")
    p.add_argument("--compare", default="", help="e2e-<stack>.json of another stack's run")
    p.add_argument("--other-client-id", default="",
                   help="another flavor's front-door client id (D4b)")
    return p


def main(argv=None, *, transport=None, prompt=getpass.getpass, env=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args, env)
        allowed = DOORS[cfg.stack]
        doors = [d.strip() for d in args.doors.split(",") if d.strip()] or list(allowed)
        bad = [d for d in doors if d not in allowed]
        if bad:
            raise ConfigError(f"unknown door(s) for the {cfg.stack} stack: {', '.join(bad)} "
                              f"(choose from {', '.join(allowed)})")
        prompts = [] if args.deny_only else load_prompts(Path(args.prompts))
        compare = None
        if args.compare:
            try:
                compare = json.loads(Path(args.compare).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ConfigError("--compare must be a readable e2e-<stack>.json file") from None
    except ConfigError as e:
        print(f"Not run: {e}")
        return EXIT_CONFIG
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "outputs" / "e2e" / \
        f"{cfg.stack}-{stamp}"
    if transport is None:
        # The harness keeps the password flow explicitly (the developer default is pkce).
        os.environ.update({"GAC_AUTH_FLOW": "password",
                           "GAC_COGNITO_REGION": cfg.region, "GAC_GATEWAY_REGION": cfg.region,
                           "GAC_RUNTIME_REGION": cfg.region, "GAC_PROBE_REGION": cfg.region,
                           "GAC_GATEWAY_URL": cfg.gateway_url,
                           "GAC_RUNTIME_ARN": cfg.orchestrator_arn,
                           "GAC_COGNITO_USER_POOL_ID": cfg.pool_id,
                           "GAC_COGNITO_CLIENT_ID": cfg.client_id,
                           "GAC_COGNITO_USERNAME": cfg.user})
        transport = LiveTransport()
    harness = Harness(cfg, transport, out_dir=out_dir, deny_only=args.deny_only, doors=doors,
                      prompts=prompts, prompt=prompt)
    try:
        return harness.run(compare)
    except ConfigError as e:
        print(f"Not run: {e}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
