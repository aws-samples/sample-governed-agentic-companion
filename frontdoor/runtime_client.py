"""Client that invokes the deployed Governed Agentic Companion AgentCore Runtime.

The companion runtime's inbound auth is CUSTOM_JWT, so the caller must present the Cognito
access token as `Authorization: Bearer <token>` on the runtime's HTTPS `/invocations`
endpoint. boto3's `bedrock-agentcore.invoke_agent_runtime` is the SigV4 (AWS-credentials)
path and has NO bearer-token parameter — it cannot satisfy a CUSTOM_JWT authorizer. So this
client POSTs directly to the invocations URL over HTTPS with the bearer header, exactly
mirroring what `agentcore invoke --bearer-token` does.

Endpoint shape (from `agentcore status`):
    https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-ARN>/invocations

Config (env):
    GAC_RUNTIME_ARN         the deployed runtime ARN
    GAC_RUNTIME_REGION      region (defaults to GAC_COGNITO_REGION or us-east-1)
    GAC_AGENTCORE_ENDPOINT  optional base URL override for networks that cannot resolve the
                            private DNS name of the AgentCore data-plane endpoint (private
                            network mode). Accepted ONLY as https://bedrock-agentcore.<region>.amazonaws.com
                            or the endpoint-specific vpce-...bedrock-agentcore.<region>.vpce.amazonaws.com
                            name (regional or zonal) for the configured Region, so the bearer
                            token can only go to an AWS-owned AgentCore data-plane host.

Private network mode (the deployment default): the runtime accepts invocations only through the
customer's AgentCore data-plane VPC endpoint. From outside the approved networks the call is
refused (HTTP 403) or cannot connect; connect over the VPN / Direct Connect path or from an
in-VPC desktop (docs/FRONT-DOORS.md).

Payload contract (matches BOTH deployed runtime faces — the /invocations orchestrator app
and the /mcp runtime): the runtime reads `prompt` + `environment`. The companion exposes two
tools, `ask_companion` and `companion_kb`; both route the text as a PROMPT through the
governed orchestrator (there is no separate "kb" action envelope). Governance is preserved in
the cloud: the runtime's response has already passed the always-on Governance Gate.

Rules: on a 401/403 (expired token), invalidate the token and retry ONCE; never log the
token/secret. A denial, another HTTP error or a connection failure raises
upstream_errors.UpstreamError, whose text is one safe line (status + the server's short
message, no URL, ARN or token) plus a private-mode hint.
"""

import json
import logging
import os
import re
import sys
import urllib.parse
import uuid
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from upstream_errors import REQUESTS_MISSING, UpstreamError, message_from_body  # noqa: E402

logger = logging.getLogger("companion.frontdoor.runtime")

# AWS Region names (commercial and GovCloud), validated before use in a host pattern.
_REGION_RE = re.compile(r"[a-z]{2}(-gov)?-[a-z]+-[0-9]", re.ASCII)
_ENDPOINT_ERROR = ("GAC_AGENTCORE_ENDPOINT must be https://bedrock-agentcore.<region>.amazonaws.com "
                   "or an AgentCore vpce DNS name for <region>")
_PRIVATE_MODE_HINT = ("endpoint not reachable from this network; in private mode, run from a network "
                      "that reaches the VPC endpoints.")


#: The bridge never parses an upstream diagram body larger than this (max payload + 1 MiB).
UPSTREAM_CAP = 5 * 1024 * 1024


class RuntimeConfigError(RuntimeError):
    pass


class UpstreamTooLarge(RuntimeError):
    """The upstream body exceeded UPSTREAM_CAP; nothing was parsed."""


class CompanionRuntimeClient:
    def __init__(self, token_provider, session_id: Optional[str] = None, _post=None):
        self._tokens = token_provider
        # A stable per-bridge-process session id (AgentCore wants a 33+ char runtimeSessionId).
        self._session_id = session_id or ("companion-frontdoor-" + uuid.uuid4().hex)
        self._post = _post  # test seam: (url, headers, data) -> (status_code, body_text)

    @staticmethod
    def _region() -> str:
        return os.getenv("GAC_RUNTIME_REGION") or os.getenv("GAC_COGNITO_REGION") or "us-east-1"

    @staticmethod
    def _validated_endpoint(raw: str, region: str) -> str:
        """Return the scheme+host base for a GAC_AGENTCORE_ENDPOINT override, or raise.

        The bearer token may go only to an AWS-owned AgentCore data-plane host for the
        configured Region. The rejected value is never echoed (it may contain userinfo)."""
        if not _REGION_RE.fullmatch(region):
            raise RuntimeConfigError(_ENDPOINT_ERROR)
        try:
            parts = urllib.parse.urlsplit(raw)
            port = parts.port  # raises ValueError on a malformed port
        except ValueError:
            raise RuntimeConfigError(_ENDPOINT_ERROR) from None
        if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                or port not in (None, 443) or parts.path not in ("", "/")
                or parts.query != "" or parts.fragment != ""):
            raise RuntimeConfigError(_ENDPOINT_ERROR)
        # Compare the raw host (urlsplit's .hostname lowercases); strip an explicit :443.
        host = parts.netloc[:-len(":443")] if parts.netloc.endswith(":443") else parts.netloc
        r = re.escape(region)
        allowed = (
            rf"bedrock-agentcore\.{r}\.amazonaws\.com",
            rf"vpce-[0-9a-f]{{8,17}}-[0-9a-z]{{8}}(-{r}[a-z])?\.bedrock-agentcore\.{r}\.vpce\.amazonaws\.com",
        )
        if not any(re.fullmatch(p, host, re.ASCII) for p in allowed):
            raise RuntimeConfigError(_ENDPOINT_ERROR)
        return f"https://{host}"

    @classmethod
    def _url(cls) -> str:
        arn = os.getenv("GAC_RUNTIME_ARN")
        if not arn:
            raise RuntimeConfigError("GAC_RUNTIME_ARN is not set (the deployed runtime ARN).")
        enc = urllib.parse.quote(arn, safe="")
        override = os.getenv("GAC_AGENTCORE_ENDPOINT")
        if override:
            base = cls._validated_endpoint(override, cls._region())
        else:
            base = f"https://bedrock-agentcore.{cls._region()}.amazonaws.com"
        return f"{base}/runtimes/{enc}/invocations"

    def _do_post(self, url: str, headers: dict, data: bytes, capped: bool = False) -> tuple:
        if self._post is not None:
            status, body = self._post(url, headers, data)
            if capped and body is not None and len(str(body).encode("utf-8")) > UPSTREAM_CAP:
                raise UpstreamTooLarge()
            return status, body
        try:
            import requests  # guarded: only the real HTTPS call needs it
        except ImportError:
            raise RuntimeConfigError(REQUESTS_MISSING) from None
        try:
            if not capped:
                r = requests.post(url, headers=headers, data=data, timeout=120)
                return r.status_code, r.text
            # Artefact requests: refuse a declared or streamed body over the cap before parsing.
            with requests.post(url, headers=headers, data=data, timeout=120, stream=True) as r:
                declared = r.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > UPSTREAM_CAP:
                    raise UpstreamTooLarge()
                buf = bytearray()
                for chunk in r.iter_content(65536):
                    buf.extend(chunk)
                    if len(buf) > UPSTREAM_CAP:
                        raise UpstreamTooLarge()
                return r.status_code, buf.decode("utf-8", "replace")
        except (requests.ConnectionError, requests.Timeout) as e:
            # Never log the token or headers; the exception type is enough to diagnose.
            logger.warning("Runtime %s (%s)", _PRIVATE_MODE_HINT, type(e).__name__)
            raise UpstreamError("runtime", "network", None, type(e).__name__) from None

    def _invoke_once(self, payload: dict, capped: bool = False) -> dict:
        # Build (and validate) the URL BEFORE fetching a token, so a rejected endpoint override
        # never causes a token to be minted or sent.
        url = self._url()
        token = self._tokens.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # AgentCore data-plane expects a session id (>=33 chars) to bind the session.
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": self._session_id,
        }
        status, body = self._do_post(url, headers, json.dumps(payload).encode("utf-8"), capped)
        if status in (401, 403):
            raise UpstreamError("runtime", "denied", status, message_from_body(body))
        if status >= 400:
            raise UpstreamError("runtime", "http", status, message_from_body(body))
        try:
            return json.loads(body) if body else {}
        except (ValueError, TypeError):
            return {"response": str(body)}

    def invoke(self, prompt: str, environment: str = "dev", *, action: Optional[str] = None,
               title: Optional[str] = None, name: Optional[str] = None,
               include_artifacts: Optional[bool] = None,
               planner: Optional[str] = None) -> dict:
        """Invoke the runtime with a prompt. Retries ONCE on an auth rejection after
        invalidating the cached token (handles the ~1h Cognito token expiry transparently).

        The keyword-only diagram fields are added to the payload only when given, so a call
        without them sends exactly the payload it always did. When artefacts are requested the
        body is read with the 5 MiB cap (UpstreamTooLarge, nothing parsed)."""
        payload = {"prompt": prompt or "", "environment": environment}
        for key, value in (("action", action), ("title", title), ("name", name),
                           ("include_artifacts", include_artifacts), ("planner", planner)):
            if value is not None:
                payload[key] = value
        capped = include_artifacts is True
        try:
            return self._invoke_once(payload, capped)
        except UpstreamError as e:
            if e.kind != "denied":
                raise
            logger.warning("Invoke auth rejected (HTTP %s); refreshing token and retrying once.",
                           e.status)
            self._tokens.invalidate()
            return self._invoke_once(payload, capped)
