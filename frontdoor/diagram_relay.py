"""Gateway relay: call a governed tool on the AgentCore Gateway over MCP streamable HTTP
(design D13.5). Shared by the bridge (GAC_BRIDGE_UPSTREAM=gateway) and ./run.sh diagram-fetch.

The bearer token goes only to an AgentCore gateway /mcp URL for the configured Region; the
rejected value is never echoed. The tool name prefix is validated. The MCP SDK parses the message
first; the summed tool-result text is then checked against the 5 MiB cap before the artefact JSON
is parsed, and nothing is written over the cap. A denial (HTTP 401/403, MCP -32002), another
HTTP or MCP error, or a network failure raises upstream_errors.UpstreamError (one safe line,
no URL or token). Read-only: it relays a request, it
never deploys or changes anything (Tenet 1).

Env: GAC_GATEWAY_URL, GAC_GATEWAY_REGION (else GAC_COGNITO_REGION), GAC_GATEWAY_TOOL_PREFIX
(default gac___), plus the sign-in env of the provider GAC_AUTH_FLOW selects
(cognito_token.build_token_provider: pkce by default, or password).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import timedelta
from typing import Any, Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("companion.frontdoor.relay")

from runtime_client import UPSTREAM_CAP, UpstreamTooLarge  # noqa: E402
from upstream_errors import MCP_MISSING, classify  # noqa: E402

#: An error body is read only up to this size, for its short message.
ERROR_BODY_CAP = 65536

_REGION_RE = re.compile(r"[a-z]{2}(-gov)?-[a-z]+-[0-9]", re.ASCII)
_PREFIX_RE = re.compile(r"^[a-z0-9_]{1,32}$")
URL_ERROR = "GAC_GATEWAY_URL is not an AgentCore gateway /mcp URL"


class RelayConfigError(RuntimeError):
    pass


def _region() -> str:
    return os.getenv("GAC_GATEWAY_REGION") or os.getenv("GAC_COGNITO_REGION") or ""


def gateway_url(raw: Optional[str] = None, region: Optional[str] = None) -> str:
    raw = raw if raw is not None else (os.getenv("GAC_GATEWAY_URL") or "")
    region = region if region is not None else _region()
    if not _REGION_RE.fullmatch(region or ""):
        raise RelayConfigError(URL_ERROR)
    pat = (r"^https://[a-z0-9][a-z0-9-]{0,99}\.gateway\.bedrock-agentcore\."
           + re.escape(region) + r"\.amazonaws\.com/mcp$")
    if not re.fullmatch(pat, raw, re.ASCII):
        raise RelayConfigError(URL_ERROR)
    return raw


def require_mcp() -> None:
    """Raise RelayConfigError(MCP_MISSING) when the MCP client SDK cannot be imported. Called
    before any network work (no token is minted), so a missing package is one install line."""
    try:
        from mcp import ClientSession  # noqa: F401
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F401
        from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: F401
    except ImportError:
        raise RelayConfigError(MCP_MISSING) from None


def tool_prefix() -> str:
    p = os.getenv("GAC_GATEWAY_TOOL_PREFIX", "gac___")
    if not _PREFIX_RE.match(p):
        raise RelayConfigError("GAC_GATEWAY_TOOL_PREFIX must match ^[a-z0-9_]{1,32}$")
    return p


async def _read_error_body(response) -> None:
    """httpx response hook: read a small error body so the denial's short server message
    (for example the JSON-RPC error.message of a 403) is available to upstream_errors."""
    if response.status_code < 400:
        return
    declared = response.headers.get("content-length", "")
    try:
        if declared.isdigit():
            if int(declared) <= ERROR_BODY_CAP:
                await response.aread()
            return
        # No declared length (chunked): stream at most ERROR_BODY_CAP bytes, never the whole body.
        buf = bytearray()
        async for chunk in response.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > ERROR_BODY_CAP:
                await response.aclose()  # over the cap: keep only the status
                return
        # Within the cap: keep it as the read body, as Response.aread() would (httpx 0.28).
        response._content = bytes(buf)
    except Exception as e:  # noqa: BLE001 - best effort; the status alone is enough
        logger.debug("error body not read (%s)", type(e).__name__)


def _http_client_factory(headers=None, timeout=None, auth=None):
    """The MCP SDK's default client (same timeouts, no redirect following) plus the error-body
    hook. NOTE: create_mcp_http_client lives in a PRIVATE SDK module (pinned mcp==1.30.0); on an
    SDK bump, test_production_factory_installs_error_body_hook catches a move."""
    try:
        from mcp.shared._httpx_utils import create_mcp_http_client
    except ImportError:
        raise RelayConfigError(MCP_MISSING) from None
    client = create_mcp_http_client(headers=headers, timeout=timeout, auth=auth)
    hooks = dict(client.event_hooks)
    hooks["response"] = list(hooks.get("response", [])) + [_read_error_body]
    client.event_hooks = hooks
    return client


def _default_session_factory(url: str, token: str):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory():
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            raise RelayConfigError(MCP_MISSING) from None
        async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"},
                                         timeout=timedelta(seconds=30),
                                         sse_read_timeout=timedelta(seconds=120),
                                         httpx_client_factory=_http_client_factory) as (r, w, _):
            async with ClientSession(r, w, read_timeout_seconds=timedelta(seconds=120)) as s:
                await s.initialize()
                yield s
    return factory


async def _token(token_provider) -> str:
    """The bearer token, fetched on a worker thread so a browser sign-in (PKCE) never blocks
    the event loop. Without a provider (direct library callers only) a NEW provider is built
    for this call from GAC_AUTH_FLOW; the bridge and diagram-fetch always pass their own."""
    import anyio
    if token_provider is None:
        from cognito_token import build_token_provider
        token_provider = build_token_provider()
    return await anyio.to_thread.run_sync(token_provider.get_token)


def _texts(result: Any) -> List[str]:
    out = []
    for item in getattr(result, "content", None) or []:
        t = getattr(item, "text", None)
        if isinstance(t, str):
            out.append(t)
    return out


async def call_gateway_tool(tool: str, arguments: dict, *, token_provider=None,
                            session_factory: Optional[Callable] = None) -> str:
    """Return the tool's text result (already gated in the cloud). Raises UpstreamTooLarge
    when the text is over the cap; the caller writes nothing then."""
    if session_factory is None:
        require_mcp()
        url = gateway_url()
        session_factory = _default_session_factory(url, await _token(token_provider))
    name = tool_prefix() + tool
    try:
        async with session_factory() as session:
            result = await session.call_tool(name, arguments)
    except RelayConfigError:
        raise
    except Exception as e:  # noqa: BLE001 - mapped to one safe line (no URL, token or trace)
        raise classify(e, "gateway") from None
    texts = _texts(result)
    if sum(len(t.encode("utf-8")) for t in texts) > UPSTREAM_CAP:
        raise UpstreamTooLarge()
    text = "\n".join(texts)
    if getattr(result, "isError", False):
        return text or "The gateway returned an error for this tool call."
    return text


async def list_gateway_tools(*, token_provider=None, session_factory=None) -> List[str]:
    if session_factory is None:
        require_mcp()
        url = gateway_url()
        session_factory = _default_session_factory(url, await _token(token_provider))
    try:
        async with session_factory() as session:
            listed = await session.list_tools()
    except RelayConfigError:
        raise
    except Exception as e:  # noqa: BLE001 - mapped to one safe line (no URL, token or trace)
        raise classify(e, "gateway") from None
    return sorted(t.name for t in getattr(listed, "tools", []) or [])
