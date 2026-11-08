"""Cognito token provider — mints + caches + auto-refreshes the enterprise token.

Reads config from env only (never argv/disk): GAC_COGNITO_REGION, GAC_COGNITO_CLIENT_ID,
GAC_COGNITO_CLIENT_SECRET, GAC_COGNITO_USERNAME, GAC_COGNITO_PASSWORD, GAC_COGNITO_USER_POOL_ID.
boto3 is imported lazily so local/dev/tests don't need it. Never logs the token or secret.

CognitoTokenProvider uses the ADMIN_USER_PASSWORD_AUTH flow (a service/test user): it is the
test-harness and automation flow (GAC_AUTH_FLOW=password; scripts/e2e_deployed.py,
gateway_token.py, probe_fetch.py). Developers sign in through the browser instead:
pkce_token.PkceTokenProvider (authorization code + PKCE against the public PKCE client) is the
default for the stdio bridge and `./run.sh diagram-fetch`. build_token_provider() picks one from
GAC_AUTH_FLOW (pkce | password, default pkce); LazyTokenProvider defers that choice to the first
get_token(), so a config error surfaces in the first tool call, not when the IDE spawns the bridge.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from upstream_errors import BOTO3_MISSING, UpstreamError  # noqa: E402

logger = logging.getLogger("companion.frontdoor.cognito")

# botocore exception names (matched by name, so this module never imports botocore at load).
_CONFIG_ERRORS = {"NoCredentialsError", "PartialCredentialsError", "ProfileNotFound",
                  "NoRegionError", "CredentialRetrievalError"}
_NETWORK_ERRORS = {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
                   "ConnectionClosedError"}


class CognitoConfigError(RuntimeError):
    pass


def _secret_hash(username: str, client_id: str, client_secret: str) -> str:
    return base64.b64encode(
        hmac.new(client_secret.encode(), (username + client_id).encode(), hashlib.sha256).digest()
    ).decode()


def _mapped(e: Exception) -> Exception:
    """A Cognito call failure as a safe error: the AWS error code or exception type only, never
    the message (it can name the user)."""
    resp = getattr(e, "response", None)
    code = resp.get("Error", {}).get("Code") if isinstance(resp, dict) else None
    if isinstance(code, str) and code:
        return UpstreamError("Cognito", "auth", None, code)
    names = {k.__name__ for k in type(e).__mro__}
    if names & _CONFIG_ERRORS:
        return CognitoConfigError("AWS credentials or Region for the Cognito call are not "
                                  f"available ({type(e).__name__})")
    if names & _NETWORK_ERRORS:
        return UpstreamError("Cognito", "network", None, type(e).__name__)
    return UpstreamError("Cognito", "error", None, type(e).__name__)


class CognitoTokenProvider:
    """Thread-safe token provider with in-memory caching + pre-expiry refresh."""

    def __init__(self, skew_seconds: int = 120, _minter=None):
        self._skew = skew_seconds
        self._minter = _minter          # test seam: () -> (token, expires_in)
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expiry = 0.0
        self.last_ttl = 0

    @staticmethod
    def _cfg():
        req = {k: os.getenv("GAC_COGNITO_" + k.upper()) for k in
               ("region", "client_id", "client_secret", "username", "password", "user_pool_id")}
        missing = [k for k, v in req.items() if not v]
        if missing:
            raise CognitoConfigError("Missing env: " + ", ".join("GAC_COGNITO_" + m.upper()
                                                                 for m in missing))
        return req

    def _mint(self):
        if self._minter is not None:
            return self._minter()
        c = self._cfg()
        try:
            import boto3  # guarded: only the real sign-in needs it
        except ImportError:
            raise CognitoConfigError(BOTO3_MISSING) from None
        try:
            client = boto3.client("cognito-idp", region_name=c["region"])
            resp = client.admin_initiate_auth(
                UserPoolId=c["user_pool_id"], ClientId=c["client_id"],
                AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": c["username"], "PASSWORD": c["password"],
                                "SECRET_HASH": _secret_hash(c["username"], c["client_id"],
                                                            c["client_secret"])},
            )
        except Exception as e:  # noqa: BLE001 - mapped to a safe, message-free error below
            raise _mapped(e) from None
        r = resp["AuthenticationResult"]
        return r["AccessToken"], int(r.get("ExpiresIn", 3600))

    def get_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < (self._expiry - self._skew):
                return self._token
            token, expires_in = self._mint()
            self._token = token
            self.last_ttl = max(60, int(expires_in))
            self._expiry = now + self.last_ttl
            # Log only non-sensitive metadata (character count + TTL). The value itself is
            # never logged, and the message contains no secret-adjacent wording.
            logger.info("Auth response received: length=%d chars, ttl=%ss", len(token), expires_in)
            return token

    def invalidate(self):
        with self._lock:
            self._token = None
            self._expiry = 0.0


AUTH_FLOWS = ("pkce", "password")


def auth_flow() -> str:
    """GAC_AUTH_FLOW: pkce (default, browser sign-in) or password (test harness, automation)."""
    raw = os.getenv("GAC_AUTH_FLOW")
    flow = (raw or "pkce").strip().lower()
    if flow not in AUTH_FLOWS:
        raise CognitoConfigError("GAC_AUTH_FLOW must be pkce or password")
    return flow


def build_token_provider():
    """A new token provider for GAC_AUTH_FLOW. Raises CognitoConfigError at once on a bad
    config (for pkce: a set client secret, a bad domain, client id, scopes, ports or timeout),
    before any listener is bound or any network call is made."""
    if auth_flow() == "password":
        return CognitoTokenProvider()
    from pkce_token import PkceTokenProvider
    return PkceTokenProvider()


class LazyTokenProvider:
    """Builds its provider on the first get_token() and keeps it for the object's life (one
    cache, one sign-in per bridge process). A factory failure is not cached: the next call
    tries again. invalidate() before the first build is a no-op."""

    def __init__(self, factory=build_token_provider):
        self._factory = factory
        self._lock = threading.Lock()
        self._provider = None

    def _get(self):
        with self._lock:
            if self._provider is None:
                self._provider = self._factory()
            return self._provider

    def get_token(self) -> str:
        return self._get().get_token()

    def invalidate(self):
        provider = self._provider
        if provider is not None:
            provider.invalidate()

    @property
    def last_ttl(self) -> int:
        provider = self._provider
        return getattr(provider, "last_ttl", 0) if provider is not None else 0
