"""Clean, safe errors for the front doors when an upstream refuses or cannot be reached.

Shared by the gateway relay (diagram_relay), the runtime client (runtime_client), the Cognito
token provider (cognito_token), the probe helper (probe_fetch), the stdio bridge (mcp_bridge) and
`./run.sh diagram-fetch`. `classify()` turns an httpx status error, an MCP JSON-RPC error
(McpError), a transport error, or an exception group holding one of them into an UpstreamError
whose text is one short line plus a hint. The text never carries a token, a header, a URL or an
ARN: the server message is reduced to its JSON `message` field, stripped of URLs, ARNs, bearer
values and JWT-shaped strings, and truncated.

Exit code: EXIT_UPSTREAM (3) for an upstream denial or failure; config errors stay 2.
Standard library only; httpx and mcp are recognised by duck typing, so this imports without them.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, Optional

EXIT_UPSTREAM = 3
DETAIL_MAX = 160

#: One install line for every missing front-door dependency (config error, exit 2).
INSTALL_LINE = "pip install -r requirements-frontdoor.txt"
BOTO3_MISSING = f"boto3 is not installed; run: {INSTALL_LINE}"
REQUESTS_MISSING = f"requests is not installed; run: {INSTALL_LINE}"
MCP_MISSING = f"mcp is not installed; run: {INSTALL_LINE}"

HINT = ("Hint: in private mode, run from a network that reaches the VPC endpoints (the VPN or "
        "Direct Connect path, or an in-VPC desktop), or run the end-to-end test through the "
        "private access probe (./run.sh diagram-fetch --via-probe <function>).")

_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+")
_ARN_RE = re.compile(r"\barn:aws[a-z-]*:\S+")
_BEARER_RE = re.compile(r"(?i)bearer\s+[^\s\"',;]+")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def short_message(text: Any) -> str:
    """A server message made safe to print: no URL, ARN, bearer value or JWT; one line; short."""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    s = _JWT_RE.sub("[redacted]", s)
    s = _BEARER_RE.sub("Bearer [redacted]", s)
    s = _URL_RE.sub("[url]", s)
    s = _ARN_RE.sub("[arn]", s)
    s = _CTRL_RE.sub(" ", s).strip()
    if len(s) > DETAIL_MAX:
        s = s[:DETAIL_MAX].rstrip() + "..."
    return s


def message_from_body(body: Any) -> str:
    """The short message from an error body: JSON-RPC error.message, an AWS `message` /
    `Message`, else the first line of the text."""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body[:65536]).decode("utf-8", "replace")
    if not isinstance(body, str) or not body.strip():
        return ""
    try:
        doc = json.loads(body)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        err = doc.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return short_message(err["message"])
        for key in ("message", "Message", "errorMessage"):
            if isinstance(doc.get(key), str):
                return short_message(doc[key])
        return ""
    return short_message(body.strip().splitlines()[0])


class UpstreamError(RuntimeError):
    """An upstream denied or failed a request. str() is the safe, printable text.

    kind: denied (HTTP 401/403 or MCP -32002), http (another HTTP status), mcp (a JSON-RPC
    error), network (not reachable), auth (Cognito refused the sign-in), timeout (reached but no
    answer within the client's read timeout), error (anything else;
    the detail is the exception type name only)."""

    def __init__(self, upstream: str, kind: str, status: Optional[int] = None, detail: str = "",
                 code: Optional[int] = None):
        self.upstream = upstream
        self.kind = kind
        self.status = status
        self.code = code
        self.detail = short_message(detail)
        super().__init__(self.text())

    def line(self) -> str:
        u, d = self.upstream, self.detail
        if self.kind == "denied" and self.status is not None:
            return f"Not fetched: {u} denied the request (HTTP {self.status}" + (f": {d})" if d else ")")
        if self.kind == "denied":
            return f"Not fetched: {u} denied the request (MCP {self.code}" + (f": {d})" if d else ")")
        if self.kind == "http":
            return f"Not fetched: {u} returned an error (HTTP {self.status}" + (f": {d})" if d else ")")
        if self.kind == "mcp":
            return f"Not fetched: {u} returned an MCP error ({self.code}" + (f": {d})" if d else ")")
        if self.kind == "network":
            return f"Not fetched: {u} is not reachable from this network" + (f" ({d})" if d else "")
        if self.kind == "auth":
            return f"Not fetched: {u} denied the sign-in" + (f" ({d})" if d else "")
        if self.kind == "timeout":
            return f"Not fetched: {u} did not answer in time" + (f" ({d})" if d else "")
        return f"Not fetched: the {u} call failed" + (f" ({d})" if d else "")

    @property
    def hinted(self) -> bool:
        return self.kind in ("denied", "network") and self.upstream in ("gateway", "runtime")

    def text(self) -> str:
        return self.line() + ("\n" + HINT if self.hinted else "")

    def __str__(self) -> str:
        return self.text()


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    """Every leaf of an exception group (duck typed, so Python 3.9 works without the builtin)."""
    subs = getattr(exc, "exceptions", None)
    if isinstance(subs, (list, tuple)) and subs and all(isinstance(s, BaseException) for s in subs):
        for s in subs:
            yield from _leaves(s)
    else:
        yield exc


def _status_error(exc: BaseException, upstream: str) -> Optional[UpstreamError]:
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if not isinstance(status, int) or getattr(exc, "request", None) is None:
        return None
    detail = ""
    try:
        detail = message_from_body(resp.content)
    except Exception:  # noqa: BLE001 - a streamed body may not have been read
        detail = ""
    if not detail:
        detail = short_message(getattr(resp, "reason_phrase", "") or "")
    kind = "denied" if status in (401, 403) else "http"
    return UpstreamError(upstream, kind, status, detail)


def _mcp_error(exc: BaseException, upstream: str) -> Optional[UpstreamError]:
    if type(exc).__name__ != "McpError":
        return None
    data = getattr(exc, "error", None)
    code = getattr(data, "code", None)
    msg = getattr(data, "message", "") or ""
    kind = "denied" if code == -32002 else "mcp"
    return UpstreamError(upstream, kind, None, msg, code=code if isinstance(code, int) else None)


_NETWORK_NAMES = {"ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
                  "ReadError", "WriteError", "RemoteProtocolError", "NetworkError", "TimeoutException",
                  "TransportError", "ConnectionError", "Timeout", "EndpointConnectionError",
                  "ConnectTimeoutError", "ReadTimeoutError", "gaierror", "TimeoutError",
                  "ConnectionRefusedError", "ConnectionResetError"}


def _network_error(exc: BaseException, upstream: str) -> Optional[UpstreamError]:
    names = {c.__name__ for c in type(exc).__mro__}
    if names & _NETWORK_NAMES:
        return UpstreamError(upstream, "network", None, type(exc).__name__)
    return None


def classify(exc: BaseException, upstream: str) -> UpstreamError:
    """Map any upstream failure to an UpstreamError. An unrecognised exception becomes kind
    'error' with only its type name, so nothing it carries (a URL, a header) is shown."""
    leaves = list(_leaves(exc))
    for finder in (lambda e: e if isinstance(e, UpstreamError) else None,
                   lambda e: _status_error(e, upstream),
                   lambda e: _mcp_error(e, upstream),
                   lambda e: _network_error(e, upstream)):
        for leaf in leaves:
            found = finder(leaf)
            if found is not None:
                return found
    first = leaves[0] if leaves else exc
    return UpstreamError(upstream, "error", None, type(first).__name__)
