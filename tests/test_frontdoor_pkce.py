"""PKCE browser sign-in for the stdio bridge and diagram-fetch (release 3.4.1).

No network and no real Cognito: the token endpoint is a local http.server stub on 127.0.0.1 at
an ephemeral port, and the authorize endpoint is the provider's open_browser seam, which checks
the authorize URL and then calls the loopback redirect URI from a thread, as a browser would.
Redirect ports are found dynamically (never a real 8080).
"""
import asyncio
import base64
import functools
import hashlib
import http.client
import http.server
import importlib.util
import inspect
import json
import logging
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "frontdoor"))

import cognito_token  # noqa: E402
import pkce_token  # noqa: E402
from cognito_token import CognitoConfigError, CognitoTokenProvider, LazyTokenProvider  # noqa: E402
from pkce_token import PkceTokenProvider  # noqa: E402
from upstream_errors import UpstreamError  # noqa: E402

DOMAIN = "https://gactest-auth.auth.ap-southeast-2.amazoncognito.com"
CLIENT_ID = "pkcetestclient0123456789ab"
REGION = "ap-southeast-2"
GW_URL = f"https://gactest-gw-abc.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp"
ARN = f"arn:aws:bedrock-agentcore:{REGION}:000000000000:runtime/gactest_orch-abc"
CODE = "stub-authorization-code-5c1f"


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _jwt(claims) -> str:
    return ".".join([_b64({"alg": "none"}), _b64(claims), "sig"])


def _stub(kind, n, tail):
    """A stub credential value, built at run time (no literal for the secret scanners)."""
    return "-".join(("stub", kind, "token", str(n), tail))


BEARER = "Bearer"
ACCESS_1, ACCESS_2 = _stub("access", 1, "aa11"), _stub("access", 2, "aa11")
REFRESH_1, REFRESH_2 = _stub("refresh", 1, "bb22"), _stub("refresh", 2, "bb22")


def _free_ports(n):
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


def _refused(port) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return False
    except OSError:
        return True


class TokenStub:
    """The Cognito /oauth2/token stub: records each request and answers from `respond`."""

    def __init__(self):
        self.requests = []
        self.n = 0
        self.nonce = None          # set by the authorize stub
        self.respond = self.default
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                form = dict(urllib.parse.parse_qsl(body, keep_blank_values=True))
                stub.requests.append({"path": self.path, "headers": dict(self.headers),
                                      "form": form})
                status, payload, extra = stub.respond(form)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/oauth2/token"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def default(self, form):
        self.n += 1
        access, refresh = _stub("access", self.n, "aa11"), _stub("refresh", self.n, "bb22")
        doc = {"access_token": access, "token_type": BEARER, "expires_in": 3600}
        if form.get("grant_type") == "authorization_code":
            doc["refresh_token"] = refresh
            doc["id_token"] = _jwt({"nonce": self.nonce, "aud": CLIENT_ID, "token_use": "id"})
        return 200, doc, {}

    def grants(self, kind):
        return [r for r in self.requests if r["form"].get("grant_type") == kind]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Browser:
    """The authorize-endpoint stub (open_browser seam)."""

    def __init__(self, tokens, query=None, before=None):
        self.tokens = tokens
        self.urls = []
        self.params = []
        self.query = query          # (params) -> list of query dicts to send, in order
        self.before = before        # (redirect_uri) -> None, runs first in the thread
        self.statuses = []

    def __call__(self, url):
        self.urls.append(url)
        p = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.params.append(p)
        self.tokens.nonce = p["nonce"]
        queries = (self.query(p) if self.query else [{"code": CODE, "state": p["state"]}])
        self.thread = threading.Thread(target=self._go, args=(p["redirect_uri"], queries),
                                       daemon=True)
        self.thread.start()

    def join(self):
        self.thread.join(10)
        return self.statuses

    def _go(self, redirect_uri, queries):
        if self.before:
            self.before(redirect_uri)
        for q in queries:
            try:
                with urllib.request.urlopen(redirect_uri + "?" + urllib.parse.urlencode(q),
                                            timeout=5) as r:
                    self.statuses.append((r.status, r.read(), dict(r.headers)))
            except urllib.error.HTTPError as e:
                self.statuses.append((e.code, e.read(), dict(e.headers)))
            except OSError:
                self.statuses.append((None, b"", {}))


@pytest.fixture
def ports():
    return _free_ports(3)


@pytest.fixture
def pkce_env(monkeypatch, ports):
    monkeypatch.setenv("GAC_AUTH_FLOW", "pkce")
    monkeypatch.setenv("GAC_COGNITO_DOMAIN", DOMAIN)
    monkeypatch.setenv("GAC_COGNITO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GAC_COGNITO_REDIRECT_PORTS", ",".join(str(p) for p in ports))
    for k in ("GAC_COGNITO_CLIENT_SECRET", "GAC_COGNITO_SCOPES", "GAC_COGNITO_LOGIN_TIMEOUT",
              "GAC_COGNITO_USERNAME", "GAC_COGNITO_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    return ports


@pytest.fixture
def tokens():
    t = TokenStub()
    yield t
    t.close()


def _provider(tokens, browser=None, timeout=5, **kw):
    return PkceTokenProvider(open_browser=browser or Browser(tokens), login_timeout_s=timeout,
                             token_url=tokens.url, **kw)


# ---- 1, 2: PKCE values and the authorize URL ---------------------------------------------------

def test_rfc7636_vector_and_verifier_shape():
    # RFC 7636 Appendix B, as its octet sequences: the verifier is BASE64URL of the first, and
    # its S256 challenge is BASE64URL of the second.
    verifier_octets = bytes([116, 24, 223, 180, 151, 153, 224, 37, 79, 250, 96, 125, 216, 173,
                             187, 186, 22, 212, 37, 77, 105, 214, 191, 240, 91, 88, 5, 88, 83,
                             132, 141, 121])
    challenge_octets = bytes([19, 211, 30, 150, 26, 26, 216, 236, 47, 22, 177, 12, 76, 152, 46,
                              8, 118, 168, 120, 173, 109, 241, 68, 86, 110, 225, 137, 74, 203,
                              112, 249, 195])

    def b64url(b):
        return base64.urlsafe_b64encode(b).decode().rstrip("=")
    verifier = b64url(verifier_octets)
    assert len(verifier) == 43 and verifier.startswith("dBjftJeZ4CVP")
    assert pkce_token.s256_challenge(verifier) == b64url(challenge_octets)
    assert pkce_token.s256_challenge(verifier).startswith("E9Melhoa2Ow")
    for _ in range(20):
        v = pkce_token.make_verifier()
        assert 43 <= len(v) <= 128 and set(v) <= pkce_token._UNRESERVED


def test_authorize_url(pkce_env, tokens):
    browser = Browser(tokens)
    p = _provider(tokens, browser)
    assert p.get_token() == ACCESS_1
    url, params = browser.urls[0], browser.params[0]
    assert url.startswith(DOMAIN + "/oauth2/authorize?")
    assert params["response_type"] == "code" and params["code_challenge_method"] == "S256"
    assert params["client_id"] == CLIENT_ID and params["scope"] == "openid gac-engine/invoke"
    assert params["redirect_uri"] == f"http://127.0.0.1:{pkce_env[0]}/callback"
    assert len(params["state"]) >= 43 and len(params["nonce"]) >= 43
    verifier = tokens.grants("authorization_code")[0]["form"]["code_verifier"]
    assert params["code_challenge"] == pkce_token.s256_challenge(verifier)
    assert verifier not in url and "code_verifier" not in url
    form = tokens.grants("authorization_code")[0]["form"]
    assert form["redirect_uri"] == params["redirect_uri"] and form["code"] == CODE


# ---- 3, 4: state and the error callback --------------------------------------------------------

def test_bad_state_does_not_end_the_wait(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [{"code": CODE, "state": "wrong"}, {"code": CODE},
                                               {"code": CODE, "state": "\u00e9t\u00e9"},
                                               {"code": CODE, "state": p["state"]}])
    assert _provider(tokens, browser).get_token() == ACCESS_1
    assert [s[0] for s in browser.join()] == [400, 400, 400, 200]


def test_only_bad_state_times_out_without_token_request(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [{"code": CODE, "state": "wrong"}])
    with pytest.raises(UpstreamError) as e:
        _provider(tokens, browser, timeout=1).get_token()
    assert "not completed within 1 s" in str(e.value)
    assert tokens.requests == []


def test_error_callback(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [
        {"error": "access_denied", "error_description": "<b>secret-desc</b>"},  # no state
        {"error": "access_denied", "error_description": "<b>secret-desc</b>",
         "state": p["state"]}])
    with pytest.raises(UpstreamError) as e:
        _provider(tokens, browser).get_token()
    assert e.value.kind == "auth"
    assert str(e.value) == "Not fetched: Cognito denied the sign-in (access_denied)"
    assert "secret-desc" not in str(e.value)
    browser.join()
    assert browser.statuses[0][0] == 400 and browser.statuses[1][0] == 200
    assert browser.statuses[1][1] == pkce_token.PAGE_FAILED
    assert tokens.requests == []


def test_unknown_error_code_not_echoed(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [{"error": "<script>x", "state": p["state"]}])
    with pytest.raises(UpstreamError) as e:
        _provider(tokens, browser).get_token()
    assert str(e.value) == "Not fetched: Cognito denied the sign-in (error)"


def test_missing_code_rejected(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [{"state": p["state"]}])
    with pytest.raises(UpstreamError) as e:
        _provider(tokens, browser).get_token()
    assert "sign-in rejected: no authorization code" in str(e.value)
    assert browser.join()[0][0] == 400


# ---- 5: timeout --------------------------------------------------------------------------------

def test_timeout_text_and_port_closed(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [])
    with pytest.raises(UpstreamError) as e:
        _provider(tokens, browser, timeout=1).get_token()
    assert str(e.value) == ("Not fetched: Cognito denied the sign-in (browser sign-in not "
                            "completed within 1 s; retry the call)")
    assert _refused(pkce_env[0])


def test_timeout_env_bounds(pkce_env, monkeypatch):
    for bad in ("29", "601", "abc", "-5", "1.5"):
        monkeypatch.setenv("GAC_COGNITO_LOGIN_TIMEOUT", bad)
        with pytest.raises(CognitoConfigError) as e:
            PkceTokenProvider()
        assert str(e.value) == pkce_token.TIMEOUT_ERROR
    monkeypatch.setenv("GAC_COGNITO_LOGIN_TIMEOUT", "30")
    assert PkceTokenProvider()._login_timeout == 30
    monkeypatch.delenv("GAC_COGNITO_LOGIN_TIMEOUT")
    assert PkceTokenProvider()._login_timeout == 180


# ---- 6, 7, 8: ports, loopback binding, one request ---------------------------------------------

def test_port_fallback(pkce_env, tokens):
    held = socket.socket()
    held.bind(("127.0.0.1", pkce_env[0]))
    held.listen(1)
    try:
        browser = Browser(tokens)
        _provider(tokens, browser).get_token()
        assert browser.params[0]["redirect_uri"] == f"http://127.0.0.1:{pkce_env[1]}/callback"
    finally:
        held.close()


def test_all_ports_busy_is_config_error(pkce_env, tokens):
    held = []
    for port in pkce_env:
        s = socket.socket()
        s.bind(("127.0.0.1", port))
        s.listen(1)
        held.append(s)
    try:
        with pytest.raises(CognitoConfigError) as e:
            _provider(tokens).get_token()
        assert str(e.value).startswith("no free loopback port in ")
    finally:
        for s in held:
            s.close()


@pytest.mark.parametrize("raw,ok", [
    ("8080-8084", [8080, 8081, 8082, 8083, 8084]), ("8080,8082", [8080, 8082]),
    (None, [8080, 8081, 8082, 8083, 8084]),
    ("8084-8080", None), ("80-81", None), ("65535-65536", None), ("8000-8010", None),
    ("8080,8080", None), ("abc", None), ("8080;8081", None), ("", [8080, 8081, 8082, 8083, 8084])])
def test_parse_ports(raw, ok):
    if ok is None:
        with pytest.raises(CognitoConfigError):
            pkce_token.parse_ports(raw)
    else:
        assert pkce_token.parse_ports(raw) == ok


def test_listener_bound_to_loopback_only(ports):
    cb = pkce_token.LoopbackCallback(ports, "s")
    try:
        assert cb.server.server_address[0] == "127.0.0.1"
        assert cb.server.socket.getsockname()[0] == "127.0.0.1"
        assert cb.server.allow_reuse_address is False
        assert cb.redirect_uri == f"http://127.0.0.1:{ports[0]}/callback"
    finally:
        cb.server.server_close()


def test_listener_closed_after_one_request(pkce_env, tokens):
    browser = Browser(tokens)
    _provider(tokens, browser).get_token()
    assert _refused(pkce_env[0])


# ---- 9, 10: other paths, methods, Host; the fixed page -----------------------------------------

def _raw(port, method, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    c.endheaders()
    r = c.getresponse()
    out = (r.status, r.read(), dict(r.getheaders()))
    c.close()
    return out


def test_other_paths_methods_and_host(pkce_env, tokens):
    seen = {}

    def before(redirect_uri):
        port = int(urllib.parse.urlsplit(redirect_uri).port)
        seen["favicon"] = _raw(port, "GET", "/favicon.ico")[0]
        seen["other"] = _raw(port, "GET", "/other?code=x")[0]
        seen["post"] = _raw(port, "POST", "/callback")[0]
        seen["host"] = _raw(port, "GET", "/callback?code=x&state=y", host="evil.example")[0]
        seen["localhost"] = _raw(port, "GET", "/callback", host=f"localhost:{port}")[0]

    browser = Browser(tokens, before=before)
    assert _provider(tokens, browser).get_token() == ACCESS_1
    assert seen == {"favicon": 404, "other": 404, "post": 404, "host": 400, "localhost": 400}


def test_callback_page_is_fixed_and_hardened(pkce_env, tokens):
    browser = Browser(tokens, query=lambda p: [{"code": CODE, "state": p["state"],
                                                "x": "<script>alert(1)</script>"}])
    _provider(tokens, browser).get_token()
    status, body, headers = browser.join()[0]
    assert status == 200 and body == pkce_token.PAGE_OK
    assert b"<script" not in body and CODE.encode() not in body
    assert headers["Content-Security-Policy"] == "default-src 'none'"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "Python" not in headers.get("Server", "")


# ---- 11: refresh --------------------------------------------------------------------------------

def test_refresh_before_expiry(pkce_env, tokens):
    def respond(form):
        status, doc, extra = TokenStub.default(tokens, form)
        doc["expires_in"] = 60  # inside the 120 s skew
        if form["grant_type"] == "refresh_token":
            doc["id_token"] = _jwt({"aud": CLIENT_ID, "token_use": "id"})  # no nonce: ignored
        return status, doc, extra
    tokens.respond = respond
    browser = Browser(tokens)
    p = _provider(tokens, browser)
    assert p.get_token() == ACCESS_1
    assert p.get_token() == ACCESS_2
    assert len(browser.urls) == 1  # no second bind or browser
    r = tokens.grants("refresh_token")[0]["form"]
    assert r == {"grant_type": "refresh_token", "client_id": CLIENT_ID,
                 "refresh_token": REFRESH_1}


def test_invalidate_refreshes_and_rotation_adopted(pkce_env, tokens):
    def respond(form):
        status, doc, extra = TokenStub.default(tokens, form)
        if form["grant_type"] == "refresh_token":
            doc["refresh_token"] = f"rotated-refresh-{tokens.n}"
        return status, doc, extra
    tokens.respond = respond
    browser = Browser(tokens)
    p = _provider(tokens, browser)
    p.get_token()
    assert p.get_token() == ACCESS_1  # cached
    p.invalidate()
    assert p.get_token() == ACCESS_2
    p.invalidate()
    p.get_token()
    refreshes = tokens.grants("refresh_token")
    assert refreshes[0]["form"]["refresh_token"] == REFRESH_1
    rotated = "-".join(("rotated", "refresh", "2"))
    assert refreshes[1]["form"]["refresh_token"] == rotated
    assert len(browser.urls) == 1


def test_invalid_grant_falls_back_to_one_login(pkce_env, tokens):
    def respond(form):
        if form["grant_type"] == "refresh_token":
            return 400, {"error": "invalid_grant", "error_description": "expired-desc"}, {}
        return TokenStub.default(tokens, form)
    tokens.respond = respond
    browser = Browser(tokens)
    p = _provider(tokens, browser)
    p.get_token()
    p.invalidate()
    assert p.get_token() == ACCESS_2
    assert len(browser.urls) == 2 and len(tokens.grants("refresh_token")) == 1


def test_other_refresh_error_raises(pkce_env, tokens):
    def respond(form):
        if form["grant_type"] == "refresh_token":
            return 400, {"error": "invalid_client", "error_description": "desc-x"}, {}
        return TokenStub.default(tokens, form)
    tokens.respond = respond
    p = _provider(tokens)
    p.get_token()
    p.invalidate()
    with pytest.raises(UpstreamError) as e:
        p.get_token()
    assert str(e.value) == "Not fetched: Cognito denied the sign-in (invalid_client)"


# ---- 12, 13: no secret sent; nothing secret in output ------------------------------------------

def test_no_secret_sent(pkce_env, tokens):
    p = _provider(tokens)
    p.get_token()
    p.invalidate()
    p.get_token()
    assert len(tokens.requests) == 2
    for r in tokens.requests:
        assert "Authorization" not in r["headers"] and "authorization" not in {
            k.lower() for k in r["headers"]}
        assert "client_secret" not in r["form"]
        assert r["form"]["client_id"] == CLIENT_ID
        assert r["path"] == "/oauth2/token"


def test_tokens_never_in_logs_or_output(pkce_env, tokens, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    browser = Browser(tokens)
    p = _provider(tokens, browser)
    p.get_token()
    p.invalidate()
    p.get_token()
    out, err = capfd.readouterr()
    text = "\n".join([out, err, caplog.text])
    form = tokens.grants("authorization_code")[0]["form"]
    secrets_ = [form["code"], form["code_verifier"], ACCESS_1,
                ACCESS_2, REFRESH_1, REFRESH_2]
    for s in secrets_:
        assert s not in text
    assert "id_token" not in text and "eyJ" not in text
    assert browser.urls[0] in err and browser.urls[0] not in out
    assert "Auth response received: length=" in caplog.text


# ---- 14, 15: id_token checks and token-endpoint hardening -------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda d, n: d.update(id_token=_jwt({"nonce": "other", "aud": CLIENT_ID, "token_use": "id"})),
    lambda d, n: d.pop("id_token"),
    lambda d, n: d.update(id_token=_jwt({"nonce": n, "aud": "someoneelse", "token_use": "id"})),
    lambda d, n: d.update(id_token=_jwt({"nonce": n, "aud": CLIENT_ID, "token_use": "access"})),
    lambda d, n: d.update(id_token=_jwt({"nonce": "\u00e9", "aud": CLIENT_ID, "token_use": "id"})),
    lambda d, n: d.update(id_token="not-a-jwt"),
])
def test_id_token_checks(pkce_env, tokens, mutate):
    def respond(form):
        status, doc, extra = TokenStub.default(tokens, form)
        mutate(doc, tokens.nonce)
        return status, doc, extra
    tokens.respond = respond
    p = _provider(tokens)
    with pytest.raises(UpstreamError) as e:
        p.get_token()
    assert str(e.value) == "Not fetched: Cognito denied the sign-in (id_token check failed)"
    assert p._token is None and p._refresh is None


def test_token_redirect_refused(pkce_env, tokens):
    tokens.respond = lambda form: (302, b"", {"Location": "https://evil.example/steal"})
    with pytest.raises(UpstreamError) as e:
        _provider(tokens).get_token()
    assert "redirect refused" in str(e.value) and "evil" not in str(e.value)


def test_token_response_over_cap(pkce_env, tokens):
    def respond(form):
        status, doc, extra = TokenStub.default(tokens, form)
        doc["pad"] = "x" * 70000
        return status, doc, extra
    tokens.respond = respond
    with pytest.raises(UpstreamError) as e:
        _provider(tokens).get_token()
    assert "over 64 KiB" in str(e.value)


def _shape(access, kind, expires):
    doc = {"token_type": kind, "expires_in": expires}
    if access is not None:
        doc["access_token"] = access
    return doc


@pytest.mark.parametrize("doc", [
    _shape(None, BEARER, 3600),        # no access token
    _shape("a", "mac", 3600),          # not a bearer token
    _shape("a", BEARER, "3600"),       # expires_in not an integer
    _shape("a", BEARER, True),         # expires_in a boolean
])
def test_token_response_shape(pkce_env, tokens, doc):
    tokens.respond = lambda form: (200, doc, {})
    with pytest.raises(UpstreamError) as e:
        _provider(tokens).get_token()
    assert "unexpected token response" in str(e.value)


def test_token_5xx_and_network(pkce_env, tokens):
    tokens.respond = lambda form: (503, {"error": "x"}, {})
    with pytest.raises(UpstreamError) as e:
        _provider(tokens).get_token()
    assert e.value.kind == "http" and e.value.status == 503
    dead = f"http://127.0.0.1:{_free_ports(1)[0]}/oauth2/token"
    p = PkceTokenProvider(open_browser=Browser(tokens), login_timeout_s=5, token_url=dead)
    with pytest.raises(UpstreamError) as e:
        p.get_token()
    assert e.value.kind == "network"


# ---- 16, 17: selection, config validation and the domain rule ---------------------------------

def test_selection(pkce_env, monkeypatch):
    monkeypatch.delenv("GAC_AUTH_FLOW")
    assert isinstance(cognito_token.build_token_provider(), PkceTokenProvider)
    monkeypatch.setenv("GAC_AUTH_FLOW", "pkce")
    assert isinstance(cognito_token.build_token_provider(), PkceTokenProvider)
    monkeypatch.setenv("GAC_AUTH_FLOW", "password")
    assert isinstance(cognito_token.build_token_provider(), CognitoTokenProvider)
    monkeypatch.setenv("GAC_AUTH_FLOW", "bogus-flow")
    with pytest.raises(CognitoConfigError) as e:
        cognito_token.build_token_provider()
    assert "bogus-flow" not in str(e.value)


def test_secret_with_pkce_errors_before_bind(pkce_env, monkeypatch):
    monkeypatch.setenv("GAC_COGNITO_CLIENT_SECRET", "a-client-secret-value")
    binds = []
    monkeypatch.setattr(pkce_token, "LoopbackCallback", lambda *a, **k: binds.append(a))
    with pytest.raises(CognitoConfigError) as e:
        cognito_token.build_token_provider()
    assert str(e.value) == pkce_token.PUBLIC_CLIENT_ERROR and binds == []
    assert "a-client-secret-value" not in str(e.value)


def test_missing_env(pkce_env, monkeypatch):
    monkeypatch.delenv("GAC_COGNITO_DOMAIN")
    with pytest.raises(CognitoConfigError, match="Missing env: GAC_COGNITO_DOMAIN"):
        PkceTokenProvider()


@pytest.mark.parametrize("bad", ["has space", "abc\n", "x" * 129, "abc-def", "abc.def"])
def test_client_id_rule(pkce_env, monkeypatch, bad):
    monkeypatch.setenv("GAC_COGNITO_CLIENT_ID", bad)
    with pytest.raises(CognitoConfigError) as e:
        PkceTokenProvider()
    assert str(e.value) == pkce_token.CLIENT_ID_ERROR


@pytest.mark.parametrize("raw,ok", [
    ("openid gac-engine/invoke", "openid gac-engine/invoke"),
    ("acme-engine/invoke openid", "openid acme-engine/invoke"),
    ("openid", None), ("gac-engine/invoke", None),
    ("openid gac-engine/invoke aws.cognito.signin.user.admin", None),
    ("openid gac-engine/invoke email", None), ("openid profile", None),
    ("openid /invoke", None), ("openid openid", None)])
def test_scope_rule(raw, ok):
    if ok is None:
        with pytest.raises(CognitoConfigError):
            pkce_token.validate_scopes(raw)
    else:
        assert pkce_token.validate_scopes(raw) == ok


@pytest.mark.parametrize("raw", [
    "https://p-1.auth.ap-southeast-2.amazoncognito.com",
    "https://p-1.auth.ap-southeast-2.amazoncognito.com/",
    "https://gac.auth-fips.us-gov-west-1.amazoncognito.com",
    "https://gac.auth-fips.us-east-1.amazoncognito.com"])
def test_domain_accepted(raw):
    assert pkce_token.validate_domain(raw) == raw.rstrip("/")


@pytest.mark.parametrize("raw", [
    "http://p.auth.us-east-1.amazoncognito.com",
    "https://p.auth.us-east-1.amazoncognito.com/oauth2",
    "https://p.auth.us-east-1.amazoncognito.com?x=1",
    "https://p.auth.us-east-1.amazoncognito.com/?",
    "https://p.auth.us-east-1.amazoncognito.com#f",
    "https://" + "user" + ":" + "pw" + "@p.auth.us-east-1.amazoncognito.com",
    "https://p.auth.us-east-1.amazoncognito.com:443",
    "https://evil.example",
    "https://x.auth.us-east-1.amazoncognito.com.evil.example",
    "https://P.auth.us-east-1.amazoncognito.com",
    "https://p\u00e4.auth.us-east-1.amazoncognito.com",
    "https://xn--p-qfa.auth.us-east-1.amazoncognito.com.evil",
    "https://-p.auth.us-east-1.amazoncognito.com", ""])
def test_domain_rejected_without_echo(raw):
    with pytest.raises(CognitoConfigError) as e:
        pkce_token.validate_domain(raw)
    assert str(e.value) == pkce_token.DOMAIN_ERROR
    if raw:
        assert raw not in str(e.value)


def test_lazy_provider_semantics(monkeypatch):
    calls = []

    class P:
        last_ttl = 77

        def get_token(self):
            return "t"

        def invalidate(self):
            calls.append("inv")

    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise CognitoConfigError("first fails")
        return P()

    lazy = LazyTokenProvider(factory)
    lazy.invalidate()  # before build: a no-op
    assert lazy.last_ttl == 0
    with pytest.raises(CognitoConfigError):
        lazy.get_token()
    assert lazy.get_token() == "t" and lazy.get_token() == "t"
    assert len(attempts) == 2 and lazy.last_ttl == 77
    lazy.invalidate()
    assert calls == ["inv"]


def test_concurrent_calls_share_one_login(pkce_env, tokens):
    browser = Browser(tokens)
    p = _provider(tokens, browser)
    out = []
    threads = [threading.Thread(target=lambda: out.append(p.get_token())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert out == [ACCESS_1] * 4 and len(browser.urls) == 1


# ---- 18, 19, 20: the bridge and diagram-fetch over the real provider seam ---------------------

def _payload(name="pkce-e2e"):
    drawio = "<mxfile><diagram/></mxfile>"
    report = json.dumps({"schema": "gac.diagram-report/1", "name": name})

    def art(ext, content, media):
        data = content.encode()
        return {"file": f"{name}.{ext}", "media_type": media, "encoding": "utf-8",
                "content": content, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {"response": "Gated diagram text.", "gated": True,
            "diagram": {"schema": "gac.diagram/1", "name": name, "artifacts": [
                art("drawio", drawio, "application/vnd.jgraph.mxfile"),
                art("report.json", report, "application/json")]}}


class _Item:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, text):
        self.content = [_Item(text)]
        self.isError = False


@pytest.fixture
def seams(pkce_env, tokens, monkeypatch, tmp_path):
    """The REAL PkceTokenProvider behind the bridge's own LazyTokenProvider(build_token_provider),
    with only its argument seams pointed at the stubs; the gateway session factory records the
    bearer it is given."""
    browser = Browser(tokens)
    monkeypatch.setattr(pkce_token, "PkceTokenProvider", functools.partial(
        PkceTokenProvider, open_browser=browser, login_timeout_s=5, token_url=tokens.url))
    monkeypatch.setenv("GAC_GATEWAY_URL", GW_URL)
    monkeypatch.setenv("GAC_GATEWAY_REGION", REGION)
    monkeypatch.setenv("GAC_RUNTIME_ARN", ARN)
    monkeypatch.setenv("GAC_RUNTIME_REGION", REGION)
    monkeypatch.delenv("GAC_COGNITO_REGION", raising=False)
    monkeypatch.delenv("GAC_AGENTCORE_ENDPOINT", raising=False)
    monkeypatch.delenv("GAC_DIAGRAM_LOCAL_DIR", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GAC_DIAGRAM_WORKSPACE_ROOT", str(ws))
    import diagram_relay
    bearers = []

    def recorder(url, token):
        assert url == GW_URL
        bearers.append(token)

        @asynccontextmanager
        async def factory():
            class S:
                async def call_tool(self, name, args):
                    if name == "gac___draw_diagram":
                        return _Result(json.dumps(_payload()))
                    return _Result(json.dumps({"response": f"answer to {name}"}))
            yield S()
        return factory
    monkeypatch.setattr(diagram_relay, "_default_session_factory", recorder)
    return {"browser": browser, "tokens": tokens, "bearers": bearers, "ws": ws}


def _call_tool(server, name, args):
    from mcp import types as mt
    handler = server._mcp_server.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(method="tools/call",
                             params=mt.CallToolRequestParams(name=name, arguments=args))
    return asyncio.run(handler(req)).root


def _files(ws):
    return sorted(p.name for p in (ws / "outputs" / "diagrams" / "pkce-e2e").iterdir())


def test_bridge_gateway_one_sign_in_and_files(seams):
    pytest.importorskip("mcp")
    import mcp_bridge as mb
    server = mb.build_server("gateway", list(mb.ALL_TOOLS))
    assert seams["browser"].urls == []  # nothing at build time
    res = _call_tool(server, "draw_diagram", {"prompt": "draw it"})
    assert res.isError is False, res.content[0].text
    assert "Saved locally:" in res.content[0].text
    res2 = _call_tool(server, "ask_companion", {"prompt": "q"})
    assert res2.isError is False and res2.content[0].text == "answer to gac___ask_companion"
    assert len(seams["browser"].urls) == 1
    assert len(seams["tokens"].grants("authorization_code")) == 1
    assert seams["bearers"] == [ACCESS_1] * 2
    assert _files(seams["ws"]) == ["pkce-e2e.drawio", "pkce-e2e.report.json"]


def test_bridge_runtime_pkce_end_to_end(seams, monkeypatch):
    pytest.importorskip("mcp")
    import mcp_bridge as mb
    import runtime_client as rc
    seen = []

    def post(url, headers, data):
        seen.append(headers["Authorization"])
        body = json.loads(data)
        if body.get("action") == "diagram":
            return 200, json.dumps(_payload())
        return 200, json.dumps({"response": "kb answer"})
    monkeypatch.setattr(mb, "CompanionRuntimeClient",
                        lambda tp: rc.CompanionRuntimeClient(tp, _post=post))
    server = mb.build_server("runtime", list(mb.ALL_TOOLS))
    res = _call_tool(server, "draw_diagram", {"prompt": "draw it"})
    assert res.isError is False, res.content[0].text
    assert _call_tool(server, "companion_kb", {"query": "q"}).content[0].text == "kb answer"
    assert seen == ["Bearer stub-access-token-1-aa11"] * 2
    assert len(seams["browser"].urls) == 1
    assert _files(seams["ws"]) == ["pkce-e2e.drawio", "pkce-e2e.report.json"]


def test_bridge_config_error_is_clean_is_error(pkce_env, monkeypatch):
    pytest.importorskip("mcp")
    import mcp_bridge as mb
    monkeypatch.setenv("GAC_GATEWAY_URL", GW_URL)
    monkeypatch.setenv("GAC_GATEWAY_REGION", REGION)
    monkeypatch.setenv("GAC_COGNITO_CLIENT_SECRET", "a-client-secret-value")
    server = mb.build_server("gateway", list(mb.ALL_TOOLS))  # no crash at build time
    res = _call_tool(server, "draw_diagram", {"prompt": "draw it"})
    assert res.isError is True
    assert res.content[0].text.endswith("Not fetched: " + pkce_token.PUBLIC_CLIENT_ERROR)
    assert "Traceback" not in res.content[0].text


def test_runtime_tools_are_async_and_do_not_block(monkeypatch):
    pytest.importorskip("mcp")
    import anyio
    import mcp_bridge as mb

    class Slow:
        def invoke(self, **kw):
            time.sleep(0.6)  # a blocking sign-in or HTTPS call
            return {"response": "slow answer"}
    server = mb.build_server("runtime", list(mb.ALL_TOOLS), Slow())
    tools = {t.name: t.fn for t in server._tool_manager.list_tools()}
    assert set(tools) == set(mb.ALL_TOOLS)
    assert all(inspect.iscoroutinefunction(fn) for fn in tools.values())

    async def main():
        ticks, out = [], {}

        async def ticker():
            for _ in range(5):
                await anyio.sleep(0.05)
                ticks.append(time.monotonic())

        async def call():
            out["text"] = await tools["ask_companion"](prompt="q")
            out["done"] = time.monotonic()
        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            tg.start_soon(ticker)
        return ticks, out
    ticks, out = anyio.run(main)
    assert out["text"] == "slow answer" and len(ticks) == 5 and ticks[-1] < out["done"]


def test_diagram_fetch_pkce_gateway(seams, monkeypatch, capsys):
    pytest.importorskip("mcp")
    import main as cli
    monkeypatch.setattr(cli, "_REPO", seams["ws"])
    assert cli.main(["diagram-fetch", "draw it"]) == 0
    assert "Saved locally:" in capsys.readouterr().out
    assert seams["bearers"] == [ACCESS_1]
    assert _files(seams["ws"]) == ["pkce-e2e.drawio", "pkce-e2e.report.json"]


def test_diagram_fetch_pkce_config_error_exit_2(pkce_env, monkeypatch, capsys, tmp_path):
    pytest.importorskip("mcp")
    import main as cli
    monkeypatch.setattr(cli, "_REPO", tmp_path)
    monkeypatch.setenv("GAC_GATEWAY_URL", GW_URL)
    monkeypatch.setenv("GAC_GATEWAY_REGION", REGION)
    monkeypatch.setenv("GAC_COGNITO_CLIENT_SECRET", "a-client-secret-value")
    for upstream in ("gateway", "runtime"):
        assert cli.main(["diagram-fetch", "--upstream", upstream, "draw it"]) == 2
        out = capsys.readouterr().out
        assert out.strip() == "Not fetched: " + pkce_token.PUBLIC_CLIENT_ERROR
        assert "a-client-secret-value" not in out


# ---- 21: the entitlement check on a code-flow access token ------------------------------------

def _interceptor(monkeypatch, group):
    monkeypatch.setenv("GAC_REQUIRED_GROUP", group)
    monkeypatch.delenv("GAC_REQUIRED_GROUP_CLAIM", raising=False)
    spec = importlib.util.spec_from_file_location(
        "identity_interceptor_pkce", ROOT / "agentcore" / "interceptor" / "identity_interceptor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _event(token):
    return {"mcp": {"gatewayRequest": {
        "headers": {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        "body": {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "gac___draw_diagram", "arguments": {}}}}}}


def test_code_flow_access_token_passes_entitlement(monkeypatch):
    mod = _interceptor(monkeypatch, "gactest-users")
    use = "access"
    claims = {"sub": "user-9", "token_use": use, "client_id": CLIENT_ID,
              "scope": "openid gac-engine/invoke", "cognito:groups": ["gactest-users"],
              "auth_time": 1, "iss": "https://cognito-idp.ap-southeast-2.amazonaws.com/pool",
              "exp": 2, "iat": 1, "jti": "j", "username": "user-9", "version": 2}
    out = mod.lambda_handler(_event(_jwt(claims)), None)
    assert out["mcp"]["transformedGatewayRequest"]["headers"] == {"X-Gac-Actor-Sub": "user-9"}
    claims.pop("cognito:groups")
    out = mod.lambda_handler(_event(_jwt(claims)), None)
    resp = out["mcp"]["transformedGatewayResponse"]
    assert resp["statusCode"] == 403 and resp["body"]["error"]["message"] == "GAC: not entitled"


# ---- 22, 23: the shipped example and the root guard --------------------------------------------

def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


def test_mcp_example_hygiene():
    import re
    doc = json.loads((ROOT / "frontdoor" / "mcp.example.json").read_text(encoding="utf-8"))
    a = doc["option_A_local_bridge"]["mcpServers"]["companion-engine"]
    gw = doc["kiro_example"]["mcpServers"]["gac-gateway"]
    br = doc["kiro_example"]["mcpServers"]["gac-bridge"]
    bad = re.compile(r"(SECRET|PASSWORD|USERNAME)$")
    for entry in (a, gw, br):
        assert not [k for k in _keys(entry) if bad.search(k)]
        assert entry["autoApprove"] == []
    assert a["env"]["GAC_AUTH_FLOW"] == "pkce" and br["env"]["GAC_AUTH_FLOW"] == "pkce"
    assert gw["disabledTools"] == ["gac___draw_diagram"]
    assert br["env"]["GAC_BRIDGE_UPSTREAM"] == "gateway"
    assert br["env"]["GAC_BRIDGE_TOOLS"] == "draw_diagram"
    for k in ("GAC_GATEWAY_URL", "GAC_GATEWAY_REGION", "GAC_COGNITO_DOMAIN",
              "GAC_COGNITO_CLIENT_ID", "GAC_COGNITO_SCOPES", "GAC_DIAGRAM_WORKSPACE_ROOT"):
        assert k in br["env"]
    for k in ("GAC_RUNTIME_ARN", "GAC_RUNTIME_REGION", "GAC_COGNITO_DOMAIN",
              "GAC_COGNITO_CLIENT_ID", "GAC_COGNITO_SCOPES", "GAC_BRIDGE_TOOLS",
              "GAC_DIAGRAM_WORKSPACE_ROOT"):
        assert k in a["env"]
    assert br["command"].endswith("/.venv/bin/python")
    assert br["args"][0].endswith("/frontdoor/mcp_bridge.py")


def test_root_guard():
    import os
    import webbrowser
    assert os.environ["GAC_AUTH_FLOW"] == "password"
    assert isinstance(cognito_token.build_token_provider(), CognitoTokenProvider)
    with pytest.raises(AssertionError):
        webbrowser.open("https://example.invalid")
