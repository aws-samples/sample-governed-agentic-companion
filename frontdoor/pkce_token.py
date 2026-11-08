"""Browser sign-in for the developer front doors: OAuth 2.0 authorization code + PKCE (S256).

PkceTokenProvider has the same interface as cognito_token.CognitoTokenProvider (get_token(),
invalidate(), last_ttl, a lock, a 120 s pre-expiry skew). It is the default provider for the
stdio bridge and `./run.sh diagram-fetch` (GAC_AUTH_FLOW=pkce); the password flow stays for the
test harness and automation (GAC_AUTH_FLOW=password).

Flow (Cognito's hosted OAuth 2.0 endpoints, the stack's PUBLIC PKCE app client, no secret):
1. Bind a one-shot listener on 127.0.0.1 at the first free port of GAC_COGNITO_REDIRECT_PORTS
   (default 8080-8084). Never 0.0.0.0, never localhost. It serves only GET /callback.
2. Open the system browser to <domain>/oauth2/authorize (response_type=code, S256 challenge,
   fresh state and nonce) and print the same URL to stderr for headless use. stdout is the MCP
   channel and is never written.
3. On the callback, check state (constant time) first; a bad or missing state gets 400 and the
   wait goes on, so a stray local request cannot abort a sign-in. The first state-matching
   request ends the wait, and the listener is closed before the token exchange.
4. Exchange the code at <domain>/oauth2/token with the code_verifier (client_id in the body, no
   Authorization header, no client_secret). Check the id_token's nonce, aud and token_use, then
   discard it. Keep the access and refresh tokens in process memory only (no file, no keyring).
5. Before expiry, use the refresh_token grant; an invalid_grant falls back to one browser login.

Env (all non-secret): GAC_COGNITO_DOMAIN (https://<prefix>.auth.<region>.amazoncognito.com),
GAC_COGNITO_CLIENT_ID (the cognito_oauth_frontdoor_client_id output), GAC_COGNITO_SCOPES
(default "openid gac-engine/invoke": exactly openid plus one <id>/invoke scope),
GAC_COGNITO_REDIRECT_PORTS (default 8080-8084), GAC_COGNITO_LOGIN_TIMEOUT (seconds, 30-600,
default 180), GAC_COGNITO_NO_BROWSER=1 (print the URL only). A set GAC_COGNITO_CLIENT_SECRET is
a config error: the PKCE client is public.

Nothing logged or printed carries a token, the code or the verifier. Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import logging
import os
import re
import secrets
import socketserver
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cognito_token import CognitoConfigError  # noqa: E402
from upstream_errors import UpstreamError  # noqa: E402

logger = logging.getLogger("companion.frontdoor.pkce")

DEFAULT_SCOPES = "openid gac-engine/invoke"
DEFAULT_PORTS = "8080-8084"
DEFAULT_TIMEOUT_S = 180
MAX_PORTS = 10
TOKEN_TIMEOUT_S = 15
RESPONSE_CAP = 65536
CODE_MAX = 2048
HANDLER_TIMEOUT_S = 10

_COGNITO_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.auth(?:-fips)?\.[a-z]{2}(?:-gov)?-[a-z]+-[0-9]"
    r"\.amazoncognito\.com", re.ASCII)
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9]{1,128}", re.ASCII)
_SCOPE_RE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,256}", re.ASCII)  # RFC 6749 scope-token
_ERROR_CODE_RE = re.compile(r"[a-z_]{1,64}", re.ASCII)
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

DOMAIN_ERROR = ("GAC_COGNITO_DOMAIN must be the Cognito hosted UI base URL "
                "https://<prefix>.auth.<region>.amazoncognito.com")
CLIENT_ID_ERROR = ("GAC_COGNITO_CLIENT_ID must be the PKCE app client id "
                   "(cognito_oauth_frontdoor_client_id)")
SCOPES_ERROR = ("GAC_COGNITO_SCOPES must be exactly 'openid <resource-server>/invoke' "
                "(the cognito_oauth_scope output)")
PORTS_ERROR = ("GAC_COGNITO_REDIRECT_PORTS must be a range a-b or a comma list of at most "
               f"{MAX_PORTS} ports, each 1024-65535")
TIMEOUT_ERROR = "GAC_COGNITO_LOGIN_TIMEOUT must be a whole number of seconds from 30 to 600"
PUBLIC_CLIENT_ERROR = ("GAC_AUTH_FLOW=pkce uses the public PKCE client and no client secret: unset "
                "GAC_COGNITO_CLIENT_SECRET, or set GAC_AUTH_FLOW=password for the test-harness "
                "flow")

# Fixed callback pages: they reflect nothing from the request (no code-injection surface).
PAGE_OK = (b"<!doctype html><html><head><meta charset=\"utf-8\"><title>Signed in</title></head>"
           b"<body><p>Signed in to the Governed Agentic Companion. You can close this tab and "
           b"return to your IDE.</p></body></html>")
PAGE_FAILED = (b"<!doctype html><html><head><meta charset=\"utf-8\"><title>Sign-in failed"
               b"</title></head><body><p>Sign-in did not complete. Close this tab and retry the "
               b"call from your IDE.</p></body></html>")
PAGE_BAD = (b"<!doctype html><html><head><meta charset=\"utf-8\"><title>Bad request</title>"
            b"</head><body><p>Bad request.</p></body></html>")
PAGE_NOT_FOUND = (b"<!doctype html><html><head><meta charset=\"utf-8\"><title>Not found</title>"
                  b"</head><body><p>Not found.</p></body></html>")
PAGE_HEADERS = (("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
                ("Content-Security-Policy", "default-src 'none'"))


# ---- PKCE values and config validation --------------------------------------------------------

def make_verifier() -> str:
    """A fresh code_verifier: 86 characters from the RFC 7636 unreserved set."""
    return secrets.token_urlsafe(64)


def s256_challenge(verifier: str) -> str:
    """BASE64URL(SHA256(ASCII(verifier))) without padding (RFC 7636 section 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def parse_ports(raw: Optional[str]) -> List[int]:
    """Ports from "a-b" or "a,b,c": each 1024-65535, at most MAX_PORTS, no duplicates."""
    raw = (raw or DEFAULT_PORTS).strip()
    try:
        if re.fullmatch(r"[0-9]{1,5}-[0-9]{1,5}", raw):
            lo, hi = (int(p) for p in raw.split("-"))
            ports = list(range(lo, hi + 1)) if lo <= hi else []
        elif re.fullmatch(r"[0-9]{1,5}(?:\s*,\s*[0-9]{1,5})*", raw):
            ports = [int(p) for p in raw.split(",")]
        else:
            ports = []
    except ValueError:
        ports = []
    if (not ports or len(ports) > MAX_PORTS or len(set(ports)) != len(ports)
            or any(p < 1024 or p > 65535 for p in ports)):
        raise CognitoConfigError(PORTS_ERROR)
    return ports


def validate_domain(raw: Optional[str]) -> str:
    """The hosted UI base URL, only as https://<prefix>.auth[-fips].<region>.amazoncognito.com
    (no userinfo, port, path, query or fragment). The code, the verifier and the refresh token
    can only go to an AWS-owned Cognito host. The rejected value is never echoed."""
    try:
        parts = urllib.parse.urlsplit(raw or "")
        port = parts.port
    except ValueError:
        raise CognitoConfigError(DOMAIN_ERROR) from None
    if (parts.scheme != "https" or parts.username is not None or parts.password is not None
            or port is not None or parts.path not in ("", "/") or parts.query
            or parts.fragment or "?" in (raw or "") or "#" in (raw or "")
            or not _COGNITO_HOST_RE.fullmatch(parts.netloc)):
        raise CognitoConfigError(DOMAIN_ERROR)
    return "https://" + parts.netloc


def validate_client_id(raw: Optional[str]) -> str:
    if not raw or not _CLIENT_ID_RE.fullmatch(raw):
        raise CognitoConfigError(CLIENT_ID_ERROR)
    return raw


def validate_scopes(raw: Optional[str]) -> str:
    """Exactly openid plus one <resource-server>/invoke scope: the token never carries
    self-service user-API rights (aws.cognito.signin.user.admin) or profile data scopes."""
    scopes = (raw if raw is not None and raw.strip() else DEFAULT_SCOPES).split()
    if (len(scopes) != 2 or len(set(scopes)) != 2 or "openid" not in scopes
            or not all(_SCOPE_RE.fullmatch(s) for s in scopes)):
        raise CognitoConfigError(SCOPES_ERROR)
    invoke = [s for s in scopes if s != "openid"][0]
    if not invoke.endswith("/invoke") or invoke == "/invoke":
        raise CognitoConfigError(SCOPES_ERROR)
    return "openid " + invoke


def parse_timeout(raw: Optional[str]) -> int:
    if raw is None or raw.strip() == "":
        return DEFAULT_TIMEOUT_S
    if not re.fullmatch(r"[0-9]{1,4}", raw.strip()):
        raise CognitoConfigError(TIMEOUT_ERROR)
    value = int(raw.strip())
    if value < 30 or value > 600:
        raise CognitoConfigError(TIMEOUT_ERROR)
    return value


def _same(a: str, b: str) -> bool:
    """Constant-time equality on UTF-8 bytes (compare_digest refuses non-ASCII str)."""
    return hmac.compare_digest(a.encode("utf-8", "surrogatepass"),
                               b.encode("utf-8", "surrogatepass"))


def _b64url_json(segment: str) -> dict:
    pad = "=" * (-len(segment) % 4)
    doc = json.loads(base64.urlsafe_b64decode(segment + pad).decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("not an object")
    return doc


# ---- the one-shot loopback listener -----------------------------------------------------------

class _CallbackServer(http.server.HTTPServer):
    # False on purpose: SO_REUSEADDR lets another local socket take over the port on some
    # platforms. Cost: a quick repeat sign-in may use the next port while the last one sits in
    # TIME_WAIT; every port in the range is a registered callback, so that is expected.
    allow_reuse_address = False

    def server_bind(self):
        # TCPServer.server_bind only: HTTPServer's version does a reverse DNS lookup (getfqdn).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        # Never print a traceback (it could carry the request line, which holds the code).
        logger.debug("callback request failed")


class LoopbackCallback:
    """The single-callback listener. Construct with the candidate ports; wait() returns the
    authorization code or raises. The listener is closed when wait() returns or raises."""

    def __init__(self, ports: List[int], state: str):
        self._state = state
        self._result: Optional[Tuple[str, str]] = None
        self.server: Optional[_CallbackServer] = None
        for port in ports:
            try:
                self.server = _CallbackServer(("127.0.0.1", port), self._handler_class())
                break
            except OSError:
                continue
        if self.server is None:
            raise CognitoConfigError("no free loopback port in "
                                     + ",".join(str(p) for p in ports))
        self.port = self.server.server_address[1]
        self.redirect_uri = f"http://127.0.0.1:{self.port}/callback"

    def _handler_class(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = HANDLER_TIMEOUT_S  # a silent client cannot hold the listener
            server_version = "gac-callback"
            sys_version = ""

            def log_message(self, format, *args):  # noqa: A002 - the request line holds the code
                return

            def _send(self, status: int, body: bytes):
                self.send_response(status)
                for k, v in PAGE_HEADERS:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _not_found(self):
                self._send(404, PAGE_NOT_FOUND)

            do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_found

            def do_GET(self):
                if self.headers.get("Host", "") != f"127.0.0.1:{outer.port}":
                    return self._send(400, PAGE_BAD)  # DNS-rebinding guard
                parts = urllib.parse.urlsplit(self.path)
                if parts.path != "/callback":
                    return self._not_found()
                q = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
                state = q.get("state", [])
                if len(state) != 1 or not _same(state[0], outer._state):
                    return self._send(400, PAGE_BAD)  # the wait goes on
                if "error" in q:
                    err = q["error"][0] if len(q["error"]) == 1 else ""
                    outer._result = ("error", err if _ERROR_CODE_RE.fullmatch(err) else "error")
                    return self._send(200, PAGE_FAILED)
                code = q.get("code", [])
                if len(code) == 1 and 0 < len(code[0]) <= CODE_MAX:
                    outer._result = ("code", code[0])
                    return self._send(200, PAGE_OK)
                outer._result = ("nocode", "")
                return self._send(400, PAGE_FAILED)

        return Handler

    def wait(self, timeout_s: int) -> str:
        """Serve requests one at a time until the first state-matching callback or the
        deadline, then close the listener (before any token exchange)."""
        server = self.server
        deadline = time.monotonic() + timeout_s
        try:
            while self._result is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise UpstreamError("Cognito", "auth", None,
                                        f"browser sign-in not completed within {timeout_s} s; "
                                        "retry the call")
                server.timeout = min(0.5, left)
                server.handle_request()
        finally:
            server.server_close()
        kind, value = self._result
        if kind == "code":
            return value
        if kind == "error":
            raise UpstreamError("Cognito", "auth", None, value)
        raise UpstreamError("Cognito", "auth", None, "sign-in rejected: no authorization code")


# ---- token endpoint ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpstreamError("Cognito", "error", code, "redirect refused")


class PkceTokenProvider:
    """Thread-safe browser sign-in with in-memory caching + pre-expiry refresh.

    Test seams (arguments only, never env): open_browser(url) replaces the browser;
    login_timeout_s bypasses the 30-600 bound; token_url replaces <domain>/oauth2/token (a
    http://127.0.0.1 stub)."""

    def __init__(self, skew_seconds: int = 120, *,
                 open_browser: Optional[Callable[[str], None]] = None,
                 login_timeout_s: Optional[int] = None, token_url: Optional[str] = None):
        if os.getenv("GAC_COGNITO_CLIENT_SECRET"):
            raise CognitoConfigError(PUBLIC_CLIENT_ERROR)
        missing = [k for k in ("GAC_COGNITO_DOMAIN", "GAC_COGNITO_CLIENT_ID") if not os.getenv(k)]
        if missing:
            raise CognitoConfigError("Missing env: " + ", ".join(missing))
        self._domain = validate_domain(os.getenv("GAC_COGNITO_DOMAIN"))
        self._client_id = validate_client_id(os.getenv("GAC_COGNITO_CLIENT_ID"))
        self._scopes = validate_scopes(os.getenv("GAC_COGNITO_SCOPES"))
        self._ports = parse_ports(os.getenv("GAC_COGNITO_REDIRECT_PORTS"))
        self._login_timeout = (login_timeout_s if login_timeout_s is not None
                               else parse_timeout(os.getenv("GAC_COGNITO_LOGIN_TIMEOUT")))
        self._token_url = token_url or (self._domain + "/oauth2/token")
        self._authorize_url = self._domain + "/oauth2/authorize"
        self._open_browser = open_browser
        self._skew = skew_seconds
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._refresh: Optional[str] = None
        self._expiry = 0.0
        self.last_ttl = 0
        handlers = [_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
        if token_url is not None:
            handlers.append(urllib.request.ProxyHandler({}))  # the local stub, never a proxy
        self._opener = urllib.request.build_opener(*handlers)

    # -- HTTP -------------------------------------------------------------------------------
    def _post(self, form: dict) -> dict:
        data = urllib.parse.urlencode(form).encode("ascii")
        req = urllib.request.Request(self._token_url, data=data, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=TOKEN_TIMEOUT_S) as resp:
                body = resp.read(RESPONSE_CAP + 1)
        except UpstreamError:
            raise
        except urllib.error.HTTPError as e:
            code = "error"
            try:
                doc = json.loads(e.read(RESPONSE_CAP + 1)[:RESPONSE_CAP].decode("utf-8"))
                if isinstance(doc, dict) and isinstance(doc.get("error"), str) \
                        and _ERROR_CODE_RE.fullmatch(doc["error"]):
                    code = doc["error"]
            except Exception:  # noqa: BLE001 - the status is enough
                logger.debug("token error body not parsed")
            finally:
                e.close()
            if 400 <= e.code < 500:
                raise UpstreamError("Cognito", "auth", None, code) from None
            raise UpstreamError("Cognito", "http", e.code, "") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise UpstreamError("Cognito", "network", None, type(reason).__name__) from None
        if len(body) > RESPONSE_CAP:
            raise UpstreamError("Cognito", "error", None, "token response over 64 KiB")
        try:
            doc = json.loads(body.decode("utf-8"))
        except ValueError:
            doc = None
        if (not isinstance(doc, dict) or not isinstance(doc.get("access_token"), str)
                or not doc["access_token"] or not isinstance(doc.get("token_type"), str)
                or doc["token_type"].lower() != "bearer"
                or not isinstance(doc.get("expires_in"), int) or isinstance(doc["expires_in"], bool)):
            raise UpstreamError("Cognito", "error", None, "unexpected token response")
        return doc

    def _check_id_token(self, doc: dict, nonce: str) -> None:
        try:
            idt = doc.get("id_token")
            if not isinstance(idt, str):
                raise ValueError("missing")
            claims = _b64url_json(idt.split(".")[1])
            aud = claims.get("aud")
            ok = (isinstance(claims.get("nonce"), str) and _same(claims["nonce"], nonce)
                  and isinstance(aud, str) and _same(aud, self._client_id)
                  and claims.get("token_use") == "id")
        except Exception:  # noqa: BLE001 - any malformed id_token fails the same way
            ok = False
        if not ok:
            raise UpstreamError("Cognito", "auth", None, "id_token check failed")

    # -- flows ------------------------------------------------------------------------------
    def _login(self) -> dict:
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = make_verifier()
        listener = LoopbackCallback(self._ports, state)
        try:
            url = self._authorize_url + "?" + urllib.parse.urlencode({
                "response_type": "code", "client_id": self._client_id,
                "redirect_uri": listener.redirect_uri, "scope": self._scopes, "state": state,
                "nonce": nonce, "code_challenge": s256_challenge(verifier),
                "code_challenge_method": "S256"})
            logger.info("sign-in via browser on 127.0.0.1:%d", listener.port)
            print("GAC sign-in: if no browser opened, open this URL to continue:\n" + url,
                  file=sys.stderr, flush=True)
            if self._open_browser is not None:
                self._open_browser(url)
            elif os.getenv("GAC_COGNITO_NO_BROWSER") != "1":
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass  # the printed URL is the fallback
            code = listener.wait(self._login_timeout)
        finally:
            if listener.server is not None:
                listener.server.server_close()
        doc = self._post({"grant_type": "authorization_code", "client_id": self._client_id,
                          "code": code, "redirect_uri": listener.redirect_uri,
                          "code_verifier": verifier})
        self._check_id_token(doc, nonce)
        return doc

    def _refresh_grant(self) -> dict:
        return self._post({"grant_type": "refresh_token", "client_id": self._client_id,
                           "refresh_token": self._refresh})

    def get_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < (self._expiry - self._skew):
                return self._token
            doc = None
            if self._refresh:
                try:
                    doc = self._refresh_grant()
                except UpstreamError as e:
                    if not (e.kind == "auth" and e.detail == "invalid_grant"):
                        raise
                    self._refresh = None  # expired or revoked: one browser login instead
            if doc is None:
                doc = self._login()
                self._refresh = None
            if isinstance(doc.get("refresh_token"), str) and doc["refresh_token"]:
                self._refresh = doc["refresh_token"]  # adopt a rotated refresh token
            token, expires_in = doc["access_token"], int(doc["expires_in"])
            self._token = token
            self.last_ttl = max(60, expires_in)
            self._expiry = now + self.last_ttl
            logger.info("Auth response received: length=%d chars, ttl=%ss", len(token), expires_in)
            return token

    def invalidate(self):
        """Drop the access token only, so the next call refreshes silently."""
        with self._lock:
            self._token = None
            self._expiry = 0.0


__all__ = ["PkceTokenProvider", "LoopbackCallback", "make_verifier", "s256_challenge",
           "parse_ports", "validate_domain", "validate_client_id", "validate_scopes",
           "parse_timeout"]
