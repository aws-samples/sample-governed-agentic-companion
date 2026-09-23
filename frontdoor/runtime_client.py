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
    GAC_RUNTIME_ARN     the deployed runtime ARN
    GAC_RUNTIME_REGION  region (defaults to GAC_COGNITO_REGION or us-east-1)

Payload contract (matches BOTH deployed runtime faces — the /invocations orchestrator app
and the /mcp runtime): the runtime reads `prompt` + `environment`. The companion exposes two
tools, `ask_companion` and `companion_kb`; both route the text as a PROMPT through the
governed orchestrator (there is no separate "kb" action envelope). Governance is preserved in
the cloud: the runtime's response has already passed the always-on Governance Gate.

Rules: on a 401/403 (expired token), invalidate the token and retry ONCE; never log the
token/secret; a non-auth HTTP error surfaces the status + short body for diagnosis.
"""

import json
import logging
import os
import urllib.parse
import uuid
from typing import Optional

logger = logging.getLogger("companion.frontdoor.runtime")


class RuntimeConfigError(RuntimeError):
    pass


class CompanionRuntimeClient:
    def __init__(self, token_provider, session_id: Optional[str] = None, _post=None):
        self._tokens = token_provider
        # A stable per-bridge-process session id (AgentCore wants a 33+ char runtimeSessionId).
        self._session_id = session_id or ("companion-frontdoor-" + uuid.uuid4().hex)
        self._post = _post  # test seam: (url, headers, data) -> (status_code, body_text)

    @staticmethod
    def _region() -> str:
        return os.getenv("GAC_RUNTIME_REGION") or os.getenv("GAC_COGNITO_REGION") or "us-east-1"

    @classmethod
    def _url(cls) -> str:
        arn = os.getenv("GAC_RUNTIME_ARN")
        if not arn:
            raise RuntimeConfigError("GAC_RUNTIME_ARN is not set (the deployed runtime ARN).")
        enc = urllib.parse.quote(arn, safe="")
        return f"https://bedrock-agentcore.{cls._region()}.amazonaws.com/runtimes/{enc}/invocations"

    def _do_post(self, url: str, headers: dict, data: bytes) -> tuple:
        if self._post is not None:
            return self._post(url, headers, data)
        import requests  # guarded
        r = requests.post(url, headers=headers, data=data, timeout=120)
        return r.status_code, r.text

    def _invoke_once(self, payload: dict) -> dict:
        token = self._tokens.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # AgentCore data-plane expects a session id (>=33 chars) to bind the session.
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": self._session_id,
        }
        status, body = self._do_post(self._url(), headers, json.dumps(payload).encode("utf-8"))
        if status in (401, 403):
            raise PermissionError(f"auth rejected (HTTP {status})")
        if status >= 400:
            raise RuntimeError(f"runtime returned HTTP {status}: {str(body)[:300]}")
        try:
            return json.loads(body) if body else {}
        except (ValueError, TypeError):
            return {"response": str(body)}

    def invoke(self, prompt: str, environment: str = "dev") -> dict:
        """Invoke the runtime with a prompt. Retries ONCE on an auth rejection after
        invalidating the cached token (handles the ~1h Cognito token expiry transparently)."""
        payload = {"prompt": prompt or "", "environment": environment}
        try:
            return self._invoke_once(payload)
        except PermissionError as e:
            logger.warning("Invoke auth rejected (%s); refreshing token and retrying once.", e)
            self._tokens.invalidate()
            return self._invoke_once(payload)
