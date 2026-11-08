"""Laptop-side helper for the private access probe's diagram mode (TEST STAND-IN).

`./run.sh diagram-fetch --via-probe <function> "<request>"` mints a front-door token with
cognito_token.CognitoTokenProvider (env only; the password comes from GAC_COGNITO_PASSWORD or a
no-echo prompt and is never printed), invokes the probe Lambda function with
{token, diagram: <request>} through the AWS Lambda API (your AWS credentials, for example
AWS_PROFILE), and saves the returned gac.diagram/1 envelope through the SAME writer the bridge
uses (diagram_writer.write_artifacts): sha256 verification, path-traversal refusal and
no-overwrite behave exactly as on the bridge path. Files land in outputs/diagrams/<name>/, one
folder per view of a multi-view answer. An honest decline (the gated text, no artefacts, every
step HTTP 2xx) is printed and exits 4; any other answer without artefacts exits 3.

The probe reaches the gateway from inside the VPC, so this end-to-end diagram test needs no
workstation network path to the private endpoints. Disable the probe after testing
(docs/DEPLOY.md). Read-only: it relays a request and writes local files; it never deploys or
changes anything (Tenet 1). The token is sent only in the Lambda invocation payload; it is never
printed or logged.

Env: GAC_PROBE_REGION (else GAC_COGNITO_REGION) plus the Cognito env of cognito_token.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diagram_writer import front_door_lines, write_artifacts  # noqa: E402
from upstream_errors import (BOTO3_MISSING, UpstreamError, message_from_body,  # noqa: E402
                             short_message)

FUNCTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-[0-9]$")
MAX_REQUEST_CHARS = 500
#: Lambda's synchronous response limit is 6 MB; nothing larger is parsed.
RESPONSE_CAP = 6 * 1024 * 1024 + 1024
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DENIED_CODES = {"AccessDeniedException", "AccessDenied", "UnauthorizedOperation",
                 "UnrecognizedClientException", "InvalidSignatureException",
                 "ExpiredTokenException", "KMSAccessDeniedException"}
_CONFIG_NAMES = {"NoCredentialsError", "PartialCredentialsError", "ProfileNotFound",
                 "NoRegionError", "CredentialRetrievalError"}


class ProbeConfigError(RuntimeError):
    """A local setting is missing or invalid (exit 2). The text is safe to show."""


def _region() -> str:
    region = os.getenv("GAC_PROBE_REGION") or os.getenv("GAC_COGNITO_REGION") or ""
    if not REGION_RE.match(region):
        raise ProbeConfigError("set GAC_PROBE_REGION (or GAC_COGNITO_REGION) to the probe's Region")
    return region


def validate(function: str, request: str) -> str:
    if not isinstance(function, str) or not FUNCTION_RE.match(function):
        raise ProbeConfigError("the probe function name must match ^[A-Za-z0-9_-]{1,64}$ "
                               "(terraform output private_access_probe_function_name)")
    text = (request or "").strip()
    if not 1 <= len(text) <= MAX_REQUEST_CHARS or _CTRL_RE.search(text):
        raise ProbeConfigError(f"the diagram request must be 1 to {MAX_REQUEST_CHARS} characters "
                               "with no control characters")
    return text


def require_boto3() -> None:
    """Fail with the install line before anything is asked of the user (no password prompt
    when the Lambda call could not run anyway)."""
    try:
        import boto3  # noqa: F401 - availability check only
    except ImportError:
        raise ProbeConfigError(BOTO3_MISSING) from None


def ensure_password(prompt: Callable[[str], str] = getpass.getpass) -> None:
    """Prompt (no echo) for the Cognito password when it is not in the environment. It is kept
    in this process's environment only, for cognito_token, and never printed."""
    if os.getenv("GAC_COGNITO_PASSWORD"):
        return
    if not sys.stdin.isatty():
        return  # cognito_token reports the missing variable by name
    value = prompt("Cognito password (not echoed): ")
    if value:
        os.environ["GAC_COGNITO_PASSWORD"] = value


#: The probe function's timeout (agentcore/terraform/probe.tf). The client waits longer than it,
#: so a slow or cold render is never cut off by botocore's 60 s default read timeout.
PROBE_FUNCTION_TIMEOUT_S = 120
LAMBDA_READ_TIMEOUT_S = PROBE_FUNCTION_TIMEOUT_S + 10
LAMBDA_CONNECT_TIMEOUT_S = 10


def _client_config():
    """botocore Config for the probe invoke: read timeout above the function timeout and NO
    automatic retries, so one request is one probe run (each run carries the token) and a timeout
    is reported as a timeout, not as a network failure."""
    from botocore.config import Config
    return Config(read_timeout=LAMBDA_READ_TIMEOUT_S, connect_timeout=LAMBDA_CONNECT_TIMEOUT_S,
                  retries={"total_max_attempts": 1})


def _boto_invoker(region: str):
    def invoke(function: str, payload: bytes) -> tuple:
        try:
            import boto3  # guarded: only this path needs it
        except ImportError:
            raise ProbeConfigError(BOTO3_MISSING) from None
        try:
            client = boto3.client("lambda", region_name=region, config=_client_config())
            resp = client.invoke(FunctionName=function, InvocationType="RequestResponse",
                                 Payload=payload)
            body = resp["Payload"].read(RESPONSE_CAP + 1)
        except Exception as e:  # noqa: BLE001 - mapped to a safe error (code or type only)
            raise _mapped(e) from None
        return int(resp.get("StatusCode", 0)), resp.get("FunctionError"), body
    return invoke


def _mapped(e: Exception) -> Exception:
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        code = str(resp.get("Error", {}).get("Code", "") or "")
        status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == "ResourceNotFoundException":
            return ProbeConfigError("the probe function was not found in this account and Region "
                                    "(is enable_private_access_probe = true?)")
        kind = "denied" if code in _DENIED_CODES or status in (401, 403) else "http"
        return UpstreamError("probe", kind, status if isinstance(status, int) else None, code)
    names = {k.__name__ for k in type(e).__mro__}
    if names & _CONFIG_NAMES:
        return ProbeConfigError(f"AWS credentials or Region for the Lambda call are not available "
                                f"({type(e).__name__})")
    if "ReadTimeoutError" in names:
        return UpstreamError("probe", "timeout", None,
                             f"no answer within {LAMBDA_READ_TIMEOUT_S} s; check the probe's logs")
    if names & {"EndpointConnectionError", "ConnectTimeoutError"}:
        return UpstreamError("probe", "network", None, type(e).__name__)
    return UpstreamError("probe", "error", None, type(e).__name__)


def _probe_failure(doc: dict) -> UpstreamError:
    """The probe answered but did not return artefacts: name the first failing step."""
    for s in doc.get("steps") or []:
        status = s.get("status") if isinstance(s, dict) else None
        if isinstance(status, int) and status >= 400:
            kind = "denied" if status in (401, 403) else "http"
            return UpstreamError("gateway (via the probe)", kind, status,
                                 message_from_body(s.get("body", "")))
        if isinstance(status, str) and status not in ("skipped_deadline",):
            return UpstreamError("gateway (via the probe)", "error", None, status)
    return UpstreamError("probe", "error", None, short_message(doc.get("error") or "no artefacts"))


#: The probe's error when the gated tool result carried no artefacts (an honest decline).
NO_ARTEFACTS = "no artefacts returned (see result.response)"


@dataclass
class ProbeOutcome:
    """The probe's answer: the gated response text and every delivered view's bundle (view 1
    first; [] for a decline). views_key: the probe returned a `diagram_views` key (this probe
    version); False means an older probe that delivers view 1 only."""
    response: str
    bundles: List[dict] = field(default_factory=list)
    views_key: bool = False


def _is_decline(doc: dict) -> bool:
    """D7.2: a decline only when the probe reported no artefacts, every step answered HTTP 2xx,
    and the result is a gated response with no diagram. Anything else is a failure (exit 3)."""
    steps = doc.get("steps")
    result = doc.get("result")
    return (doc.get("error") == NO_ARTEFACTS
            and isinstance(steps, list)
            and all(isinstance(s, dict) and isinstance(s.get("status"), int)
                    and not isinstance(s.get("status"), bool) and 200 <= s["status"] < 300
                    for s in steps)
            and isinstance(result, dict) and isinstance(result.get("response"), str)
            and "diagram" not in result)


def _invoke_doc(function: str, invoker: Callable, payload: bytes) -> dict:
    """Invoke the probe function and return its parsed result document (no envelope checks)."""
    status, function_error, body = invoker(function, payload)
    if isinstance(body, str):
        body = body.encode("utf-8")
    if len(body) > RESPONSE_CAP:
        raise UpstreamError("probe", "error", None, "response over the 6 MB limit; nothing parsed")
    if function_error:
        raise UpstreamError("probe", "error", None, f"function error {short_message(function_error)}")
    if not 200 <= status < 300:
        raise UpstreamError("probe", "http", status, "")
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise UpstreamError("probe", "error", None, "the response is not JSON") from None
    if not isinstance(doc, dict):
        raise UpstreamError("probe", "error", None, "the response is not a probe result")
    return doc


def invoke_probe(function: str, request: str, *, token_provider=None,
                 invoker: Optional[Callable] = None) -> ProbeOutcome:
    """Return the probe's answer as a ProbeOutcome (bundles [] for an honest decline); raise
    ProbeConfigError or UpstreamError. The token is placed only in the invocation payload."""
    text = validate(function, request)
    if invoker is None:
        invoker = _boto_invoker(_region())
    if token_provider is None:
        from cognito_token import CognitoTokenProvider
        token_provider = CognitoTokenProvider()
    payload = json.dumps({"token": token_provider.get_token(), "diagram": text}).encode("utf-8")
    doc = _invoke_doc(function, invoker, payload)
    del payload
    envelope = doc.get("result")
    if doc.get("ok") is not True or not isinstance(envelope, dict) \
            or not isinstance(envelope.get("diagram"), dict):
        if _is_decline(doc):
            return ProbeOutcome(envelope["response"], [])
        raise _probe_failure(doc)
    views = envelope.get("diagram_views")
    extra = [v for v in views if isinstance(v, dict)] if isinstance(views, list) else []
    return ProbeOutcome(str(envelope.get("response", "")), [envelope["diagram"]] + extra,
                        views_key="diagram_views" in envelope)


def invoke_probe_anonymous(function: str, *, invoker: Optional[Callable] = None) -> dict:
    """Run the probe's anonymous mode ({"anonymous": true}: initialize and tools/list with no
    Authorization header, never the runtime) and return its result document {ok, steps, ...}.
    For the deployed-stack harness's deny-first check; no token is minted or sent."""
    if not isinstance(function, str) or not FUNCTION_RE.match(function):
        raise ProbeConfigError("the probe function name must match ^[A-Za-z0-9_-]{1,64}$ "
                               "(terraform output private_access_probe_function_name)")
    if invoker is None:
        invoker = _boto_invoker(_region())
    return _invoke_doc(function, invoker, json.dumps({"anonymous": True}).encode("utf-8"))


def _quality_line(diagram: dict) -> str:
    """The quality score from the (sha256-verified) report artefact."""
    for a in diagram.get("artifacts") or []:
        if isinstance(a, dict) and str(a.get("file", "")).endswith(".report.json"):
            try:
                q = json.loads(a["content"]).get("quality") or {}
            except (ValueError, TypeError, KeyError, AttributeError):
                return ""
            score, threshold = q.get("score"), q.get("threshold")
            verdict = "pass" if q.get("pass") is True else "below the threshold"
            return f"Quality score: {score} (threshold {threshold}, {verdict})"
    return ""


def _listed_views(diagram: dict) -> int:
    """How many views the engine planned, from view 1's report (0 when not recorded)."""
    for a in diagram.get("artifacts") or []:
        if isinstance(a, dict) and str(a.get("file", "")).endswith(".report.json"):
            try:
                views = (json.loads(a["content"]).get("plan") or {}).get("views")
            except (ValueError, TypeError, KeyError, AttributeError):
                return 0
            return len(views) if isinstance(views, list) else 0
    return 0


def fetch_via_probe(function: str, request: str, *, out_dir: str = "", overwrite: bool = False,
                    token_provider=None, invoker=None, workspace_root=None) -> tuple:
    """Invoke the probe and save every delivered view with diagram_writer (each view
    all-or-nothing; a refused view is reported and the others still land). Returns
    (text, status): "saved" (every delivered view saved), "refused" (the writer refused a view)
    or "declined" (an honest decline; no file)."""
    outcome = invoke_probe(function, request, token_provider=token_provider, invoker=invoker)
    if not outcome.bundles:
        return outcome.response, "declined"
    lines = [outcome.response]
    refused = False
    for bundle in outcome.bundles:
        written = write_artifacts(bundle, out_dir=out_dir, overwrite=overwrite,
                                  workspace_root=workspace_root)
        lines += ["", front_door_lines(written)]
        if written.get("written"):
            q = _quality_line(bundle)
            if q:
                lines.append(q)
        else:
            refused = True
    if not outcome.views_key:
        more = _listed_views(outcome.bundles[0]) - 1
        if more > 0:
            lines += ["", f"{more} more views were listed but not delivered by this probe version"]
    return "\n".join(lines), ("refused" if refused else "saved")
