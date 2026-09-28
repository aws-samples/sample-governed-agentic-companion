"""Generic REST integration client — the TLS-always-on base for talking to an external system.

This is the EXAMPLE integration for the starter kit. A fork subclasses it (or copies its shape)
to talk to a specific system — an issue tracker, a wiki, a VCS host, an internal API. It exists
to demonstrate the non-negotiable security posture, not to integrate any particular product.

SECURITY POSTURE (do not weaken when you fork this):
  - TLS certificate verification is ALWAYS ON. `verify` is either True (system trust store) or a
    path to a CA bundle to TRUST — it is never False. There is NO env var or flag that disables
    verification. Behind a TLS-intercepting corporate proxy, install/trust the corporate CA via
    REQUESTS_CA_BUNDLE (or pass ca_bundle=) so verification still passes. Disabling verification
    would expose every call to man-in-the-middle interception.
  - The bearer token is read from an environment variable (or a fork's secret manager), never
    hard-coded. It is never logged.
  - Governance (Tenet 3): being ABLE to POST does not mean the governed agents write to a system
    of record. The agents only ever write to human-reviewed surfaces; systems of record are
    read-only. This client is a transport, not a licence to mutate.

`requests` is imported lazily so the base kit stays importable/unit-testable without it (it is
listed as an optional integration dependency in requirements.txt).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class RestClient:
    """A minimal, secure-by-construction REST client. Subclass/extend per integration."""

    #: default request timeout (seconds); a fork may override.
    timeout: int = 30

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        token_env: str = "",
        ca_bundle: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        """
        base_url    : the API root, e.g. "https://api.example.com".
        token       : bearer token; if omitted, read from `token_env`.
        token_env   : name of the env var holding the token (a fork can swap in a secret
                      manager by overriding _resolve_token).
        ca_bundle   : optional path to a CA bundle to TRUST (else REQUESTS_CA_BUNDLE, else the
                      system trust store). Verification is never disabled.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token or self._resolve_token(token_env)
        self.verify = self._resolve_verify(ca_bundle)
        self._extra_headers = dict(extra_headers or {})
        if not self.token:
            logger.warning("%s: no token configured (env %r) — integration disabled",
                           type(self).__name__, token_env or "(none)")

    # -- security-critical resolution -------------------------------------------

    @staticmethod
    def _resolve_verify(ca_bundle: Optional[str] = None):
        """Return the requests `verify` value: a CA-bundle path if provided (arg or
        REQUESTS_CA_BUNDLE), else True. NEVER returns False — TLS verification is not
        disableable in this kit. To trust a corporate CA, point REQUESTS_CA_BUNDLE at its PEM
        bundle."""
        bundle = (ca_bundle or os.getenv("REQUESTS_CA_BUNDLE") or "").strip()
        return bundle if bundle else True

    @staticmethod
    def _resolve_token(token_env: str) -> str:
        """Read the bearer token from the environment. Override in a fork to source it from a
        secret manager instead (return the resolved value)."""
        return (os.getenv(token_env) or "").strip() if token_env else ""

    # -- request path -----------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        headers.update(self._extra_headers)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, method: str, endpoint: str, **kwargs) -> Optional[Any]:
        """Execute an HTTP request and return the parsed JSON (or None on error).

        Always sets verify=self.verify (TLS on) and a bounded timeout. `requests` is imported
        lazily so importing this module never requires it."""
        if not self.token:
            logger.error("%s not configured (no token)", type(self).__name__)
            return None
        try:
            import requests  # lazy: optional integration dependency
        except ImportError:
            logger.error("the 'requests' package is required for REST integrations "
                         "(pip install requests)")
            return None

        url = urljoin(f"{self.base_url}/", endpoint.lstrip("/"))
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("verify", self.verify)   # TLS verification, always on
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            if resp.content and "application/json" in resp.headers.get("Content-Type", ""):
                return resp.json()
            return resp.text
        except Exception as e:  # requests.RequestException + JSON errors
            logger.error("%s %s failed: %s", method, url, type(e).__name__)
            return None

    def get(self, endpoint: str, **kwargs) -> Optional[Any]:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, **kwargs) -> Optional[Any]:
        return self.request("POST", endpoint, **kwargs)

    def health_check(self) -> Dict[str, str]:
        """Report configuration/connectivity without leaking the token."""
        return {
            "client": type(self).__name__,
            "base_url": self.base_url,
            "configured": "yes" if self.token else "no",
            "tls_verify": "on (system trust)" if self.verify is True else f"on (CA bundle: {self.verify})",
        }
