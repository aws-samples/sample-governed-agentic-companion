"""Cognito token provider — mints + caches + auto-refreshes the enterprise token.

Reads config from env only (never argv/disk): GAC_COGNITO_REGION, GAC_COGNITO_CLIENT_ID,
GAC_COGNITO_CLIENT_SECRET, GAC_COGNITO_USERNAME, GAC_COGNITO_PASSWORD, GAC_COGNITO_USER_POOL_ID.
boto3 is imported lazily so local/dev/tests don't need it. Never logs the token or secret.

This reference uses the ADMIN_USER_PASSWORD_AUTH flow (a service/test user). For per-builder
identity, swap mint() for an authorization-code/PKCE exchange — the cache/refresh logic is unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("companion.frontdoor.cognito")


class CognitoConfigError(RuntimeError):
    pass


def _secret_hash(username: str, client_id: str, client_secret: str) -> str:
    return base64.b64encode(
        hmac.new(client_secret.encode(), (username + client_id).encode(), hashlib.sha256).digest()
    ).decode()


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
        import boto3  # guarded
        client = boto3.client("cognito-idp", region_name=c["region"])
        resp = client.admin_initiate_auth(
            UserPoolId=c["user_pool_id"], ClientId=c["client_id"],
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": c["username"], "PASSWORD": c["password"],
                            "SECRET_HASH": _secret_hash(c["username"], c["client_id"],
                                                        c["client_secret"])},
        )
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
            logger.info("Minted token (len=%d, ttl=%ss)", len(token), expires_in)
            return token

    def invalidate(self):
        with self._lock:
            self._token = None
            self._expiry = 0.0
