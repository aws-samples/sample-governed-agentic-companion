"""Local stdio MCP bridge: any MCP IDE (Kiro / Claude Code / ...) -> the deployed Governed
Agentic Companion (authenticated).

Why this exists: AgentCore's Runtime is a plain `/invocations` app, and standing up the
AgentCore Gateway MCP path is optional. This bridge is the turnkey front door: an MCP server
the IDE spawns locally, which forwards each call to the deployed runtime (default) or to the
gateway (GAC_BRIDGE_UPSTREAM=gateway) over an authenticated HTTPS call, refreshing the Cognito
token. It is also the front door that LANDS diagram files on the developer's machine.

Governance is preserved by construction: the bridge NEVER reasons or gates locally: it relays
to the companion, whose response has ALREADY passed the always-on Governance Gate in the cloud.
Human-owned deployment (Tenet 1) is unchanged; this is a read/answer path. The only local write
is the verified diagram artefacts, under your workspace (frontdoor/diagram_writer.py).

Tools exposed to the IDE (GAC_BRIDGE_TOOLS filters them; default all three):
    ask_companion(prompt, environment="dev")  -> route a question to the governed orchestrator
    companion_kb(query, environment="dev")     -> read-only knowledge-base query (gated)
    draw_diagram(prompt, environment="dev", title="", name="", out_dir="", overwrite=False,
                 planner="")
        -> draws AWS diagrams from the KB (a natural-language request may give up to 4 views);
           writes each view's <name>.drawio/.svg/.png/.report.json under
           <workspace>/outputs/diagrams/<name>/ after verifying each SHA-256; returns the gated
           text plus the paths it wrote (never the file content).

Env: GAC_BRIDGE_UPSTREAM (runtime | gateway), GAC_BRIDGE_TOOLS, GAC_RUNTIME_ARN /
GAC_RUNTIME_REGION (runtime upstream), GAC_GATEWAY_URL / GAC_GATEWAY_REGION /
GAC_GATEWAY_TOOL_PREFIX (gateway upstream), GAC_DIAGRAM_WORKSPACE_ROOT, GAC_DIAGRAM_LOCAL_DIR,
GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE.

Sign-in: GAC_AUTH_FLOW=pkce (the default) signs in through the browser with the public PKCE
client (frontdoor/pkce_token.py: GAC_COGNITO_DOMAIN, GAC_COGNITO_CLIENT_ID, GAC_COGNITO_SCOPES;
no secret, password or AWS credentials). The first tool call opens the browser; headless, the
sign-in URL is on stderr (the IDE's MCP server log). One provider per bridge process, so one
sign-in lasts the IDE session (refresh before expiry). GAC_AUTH_FLOW=password keeps the
test-harness flow (cognito_token.CognitoTokenProvider). Under pkce, set GAC_RUNTIME_REGION or
GAC_GATEWAY_REGION explicitly (the GAC_COGNITO_REGION fallback is a password-flow variable).

    python3 frontdoor/mcp_bridge.py --check-gateway-tools   # read-only; exit 0 if
                                                           # gac___draw_diagram is listed

An upstream denial (HTTP 401/403, MCP -32002), another HTTP or MCP error, or a network failure
becomes a clean MCP error result (isError, one line plus a private-mode hint; no traceback, URL
or token) and nothing is written (safe_error, upstream_errors.py).

The `mcp` SDK is imported lazily in main() so this module stays importable/testable without it.
"""
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import List

logging.basicConfig(level=os.getenv("GAC_BRIDGE_LOG_LEVEL", "INFO"),
                    stream=sys.stderr)  # stdout is the MCP channel; logs MUST go to stderr
logger = logging.getLogger("companion.frontdoor.bridge")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cognito_token import CognitoConfigError, LazyTokenProvider, build_token_provider  # noqa: E402
from diagram_writer import OVERSIZE, front_door_lines, write_artifacts  # noqa: E402
from runtime_client import CompanionRuntimeClient, RuntimeConfigError, UpstreamTooLarge  # noqa: E402
from upstream_errors import classify  # noqa: E402

ALL_TOOLS = ("ask_companion", "companion_kb", "draw_diagram")


def safe_error(exc: BaseException, upstream_name: str) -> Exception:
    """The exception a bridge tool raises instead of `exc`: a config error keeps its own (safe)
    text; anything else becomes one upstream_errors line. The MCP SDK turns it into an
    isError tool result, so the IDE sees a clean message, never a traceback, URL or token."""
    from diagram_relay import RelayConfigError
    if isinstance(exc, (RelayConfigError, RuntimeConfigError, CognitoConfigError)):
        return RuntimeError(f"Not fetched: {exc}")
    return classify(exc, upstream_name)


def build_client(token_provider=None) -> CompanionRuntimeClient:
    """The runtime client over `token_provider` (default: a new lazy provider for
    GAC_AUTH_FLOW, built on the first call)."""
    return CompanionRuntimeClient(token_provider or LazyTokenProvider(build_token_provider))


def _text(result: dict) -> str:
    """Extract the human-facing text from the runtime envelope (already gated in-cloud)."""
    if isinstance(result, dict):
        return str(result.get("response", "")) or "(empty response from runtime)"
    return str(result)


def enabled_tools() -> list:
    raw = os.getenv("GAC_BRIDGE_TOOLS")
    if not raw:
        return list(ALL_TOOLS)
    want = {t.strip() for t in raw.split(",") if t.strip()}
    return [t for t in ALL_TOOLS if t in want]


def upstream() -> str:
    return "gateway" if (os.getenv("GAC_BRIDGE_UPSTREAM") or "").lower() == "gateway" else "runtime"


@dataclass
class DrawResult:
    """What a draw call produced. `text` is all the MCP tool returns; the CLI maps the rest to
    its exit code (0 every delivered view saved, 1 a view refused, 4 declined).

    declined: the upstream answered with a parsed result and no `diagram` (an honest decline).
    saved: the workspace-relative paths written, every view. refused: one entry per view (or
    oversize body) that was not saved."""
    text: str
    declined: bool = False
    saved: List[str] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)


def _views_of(result: dict) -> list:
    """`diagram` (view 1) then each `diagram_views` entry (views 2..n), as delivered."""
    views = [result.get("diagram")]
    extra = result.get("diagram_views")
    if isinstance(extra, list):
        views.extend(extra)
    return views


def build_draw_result(result, *, out_dir: str = "", overwrite: bool = False,
                      workspace_root=None, cwd=None) -> DrawResult:
    """Pure result builder: parse the upstream result (runtime envelope dict or gateway tool
    text), write each delivered view's verified artefacts (each view all-or-nothing; a refused
    view is reported and the others still land), and return the gated text plus the front-door
    lines. Never returns artefact content."""
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError:
            return DrawResult(result)  # already gated upstream; nothing to write
        if not isinstance(parsed, dict):
            return DrawResult(result)
        result = parsed
    text = _text(result)
    if not isinstance(result, dict) or not isinstance(result.get("diagram"), dict):
        return DrawResult(text, declined=isinstance(result, dict))
    parts, saved, refused = [text], [], []
    for i, view in enumerate(_views_of(result), start=1):
        written = write_artifacts(view, out_dir=out_dir, overwrite=overwrite,
                                  workspace_root=workspace_root, cwd=cwd)
        parts.append(front_door_lines(written))
        if written.get("written"):
            saved.extend(written.get("shown") or written["written"])
        else:
            name = view.get("name") if isinstance(view, dict) else None
            refused.append(name if isinstance(name, str) and name else f"view {i}")
    return DrawResult("\n\n".join(parts), saved=saved, refused=refused)


def _oversize() -> DrawResult:
    return DrawResult(OVERSIZE, refused=["oversize"])


def _draw_args(prompt, environment, title, name, planner=""):
    args = {"prompt": prompt, "environment": environment, "title": title, "name": name,
            "include_artifacts": True}
    if planner:  # sent only when set, so an older runtime's tool schema still matches
        args["planner"] = planner
    return args


async def draw_via_runtime(client, prompt, environment="dev", title="", name="", out_dir="",
                           overwrite=False, planner="") -> DrawResult:
    import anyio
    kw = {"planner": planner} if planner else {}
    try:
        result = await anyio.to_thread.run_sync(lambda: client.invoke(
            prompt, environment, action="diagram", title=title, name=name,
            include_artifacts=True, **kw))
    except UpstreamTooLarge:
        return _oversize()
    return build_draw_result(result, out_dir=out_dir, overwrite=overwrite)


async def draw_via_gateway(prompt, environment="dev", title="", name="", out_dir="",
                           overwrite=False, planner="", *, session_factory=None,
                           token_provider=None) -> DrawResult:
    from diagram_relay import call_gateway_tool
    try:
        text = await call_gateway_tool("draw_diagram",
                                       _draw_args(prompt, environment, title, name, planner),
                                       session_factory=session_factory,
                                       token_provider=token_provider)
    except UpstreamTooLarge:
        return _oversize()
    return build_draw_result(text, out_dir=out_dir, overwrite=overwrite)


def check_gateway_tools(session_factory=None) -> int:
    """Read-only: list the gateway's tools; 0 if <prefix>draw_diagram is present, else 1
    (3 with one clean line when the gateway denies or cannot be reached)."""
    import anyio
    from diagram_relay import list_gateway_tools, tool_prefix
    from upstream_errors import EXIT_UPSTREAM, UpstreamError
    try:
        names = anyio.run(lambda: list_gateway_tools(session_factory=session_factory))
    except UpstreamError as e:
        print(str(e))
        return EXIT_UPSTREAM
    want = tool_prefix() + "draw_diagram"
    print("\n".join(names))
    print(("OK: " if want in names else "MISSING: ") + want)
    return 0 if want in names else 1


def _planner_arg(planner) -> str:
    """Only "" or "deterministic" are passed on (the caller can only downgrade the planner)."""
    return "deterministic" if planner == "deterministic" else ""


def _server_class():
    # Support both MCP SDK lines: v2.x renamed FastMCP -> MCPServer. Try 2.x first, then v1.
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server   # mcp 1.x
        except ImportError as e:
            raise SystemExit(
                "The MCP SDK is not installed in this environment. Install the bridge deps:\n"
                "  pip install -r requirements-frontdoor.txt\n"
                f"(import error: {e})"
            )
    return Server


def build_server(mode=None, tools=None, client=None):
    """The stdio MCP server with the enabled tools registered (not started). Every tool turns
    an upstream failure into a clean isError result (safe_error)."""
    mode = mode or upstream()
    tools = enabled_tools() if tools is None else tools
    # ONE provider for the whole process: one token cache, one browser sign-in. It is built on
    # the first call, so a config error is a clean isError result, not a crash at spawn.
    provider = LazyTokenProvider(build_token_provider)
    if mode == "runtime" and client is None:
        client = build_client(provider)
    mcp = _server_class()("companion-engine")

    async def _invoke(**kw):
        import anyio
        try:
            return _text(await anyio.to_thread.run_sync(lambda: client.invoke(**kw)))
        except Exception as e:  # noqa: BLE001 - a clean isError result
            raise safe_error(e, "runtime") from None

    async def _relay_text(tool, args):
        from diagram_relay import call_gateway_tool
        try:
            text = await call_gateway_tool(tool, args, token_provider=provider)
        except UpstreamTooLarge:
            return OVERSIZE
        except Exception as e:  # noqa: BLE001 - a clean isError result, never a traceback
            raise safe_error(e, "gateway") from None
        try:
            return _text(json.loads(text))
        except ValueError:
            return text

    if "ask_companion" in tools:
        if mode == "runtime":
            @mcp.tool()
            async def ask_companion(prompt: str, environment: str = "dev") -> str:
                """Ask the governed agentic companion (routes to the orchestrator + specialists).
                Returns a governed, gated answer. A human deploys; this never mutates any environment."""
                return await _invoke(prompt=prompt, environment=environment)
        else:
            @mcp.tool()
            async def ask_companion(prompt: str, environment: str = "dev") -> str:
                """Ask the governed agentic companion through the gateway (gated answer)."""
                return await _relay_text("ask_companion", {"prompt": prompt, "environment": environment})

    if "companion_kb" in tools:
        if mode == "runtime":
            @mcp.tool()
            async def companion_kb(query: str, environment: str = "dev") -> str:
                """Read-only knowledge-base query, routed through the governed orchestrator (gated)."""
                return await _invoke(prompt=query, environment=environment)
        else:
            @mcp.tool()
            async def companion_kb(query: str, environment: str = "dev") -> str:
                """Read-only knowledge-base query through the gateway (gated)."""
                return await _relay_text("companion_kb", {"query": query, "environment": environment})

    if "draw_diagram" in tools:
        @mcp.tool()
        async def draw_diagram(prompt: str, environment: str = "dev", title: str = "",
                               name: str = "", out_dir: str = "", overwrite: bool = False,
                               planner: str = "") -> str:
            """Draw AWS architecture diagrams of this companion's implementation from its
            knowledge base. Accepts natural-language questions as well as draw requests, for
            example 'draw the architecture end to end', 'what talks to Bedrock', 'how does a
            request reach the knowledge base', 'diagram our security boundaries'. Returns the
            governed text and, when the knowledge cannot support the request, an honest
            decline with prompts that work.

            Saves each view's <name>.drawio, .svg, .png and .report.json under your workspace
            (outputs/diagrams/<name>/ by default) after verifying each SHA-256. Returns the
            governed text and the saved paths. planner: "" or "deterministic" (it can only
            downgrade the planner). Describe-only: nothing is deployed."""
            try:
                if mode == "runtime":
                    res = await draw_via_runtime(client, prompt, environment, title, name,
                                                 out_dir, overwrite, _planner_arg(planner))
                else:
                    res = await draw_via_gateway(prompt, environment, title, name, out_dir,
                                                 overwrite, _planner_arg(planner),
                                                 token_provider=provider)
                return res.text
            except Exception as e:  # noqa: BLE001 - a clean isError result, nothing written
                raise safe_error(e, mode) from None

    return mcp


def main(argv=None):
    """Run the stdio MCP server. The IDE (Kiro/Claude Code/...) launches this as an MCP `command`."""
    argv = sys.argv[1:] if argv is None else argv
    if "--check-gateway-tools" in argv:
        raise SystemExit(check_gateway_tools())
    mode = upstream()
    tools = enabled_tools()
    mcp = build_server(mode, tools)
    logger.info("Companion MCP bridge starting (stdio, upstream=%s, tools=%s). Target: %s",
                mode, ",".join(tools),
                os.getenv("GAC_RUNTIME_ARN", "<GAC_RUNTIME_ARN not set>") if mode == "runtime"
                else "gateway")
    mcp.run()


if __name__ == "__main__":
    main()
