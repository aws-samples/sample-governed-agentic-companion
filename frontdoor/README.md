# Front doors — connect your IDE (Kiro / Claude Code / any MCP client)

The companion is reached from any MCP-capable agentic IDE over the same governed engine.
Two paths, both exposing the identical tools (ask_companion, companion_kb, draw_diagram); pick one, or pair them in Kiro (below).

- **A direct OAuth MCP client** (Kiro's native `oauth` entry, or an `http` client with a Bearer
  token) gets the gated text and, for diagrams, a manifest only.
- **The local stdio bridge lands the diagram files** (as does `./run.sh diagram-fetch`).
- **PKCE browser sign-in is the developer default** for the bridge and `diagram-fetch`
  (`GAC_AUTH_FLOW=pkce`): no client secret, password or AWS credentials, only non-secret values.
  The password flow (`GAC_AUTH_FLOW=password`) is for the test harness
  (`scripts/e2e_deployed.py`), `gateway_token.py`, the probe and automation.

- `ask_companion(prompt, environment="dev")` — route a question to the governed orchestrator.
- `companion_kb(query, environment="dev")` — read-only knowledge-base query (gated).

Governance is identical on both paths: the runtime's response is already gated in the cloud;
a human deploys (Tenet 1); systems of record stay read-only (Tenet 3).

## Prerequisites

- A deployed AgentCore runtime (see `../agentcore/README.md`) and its ARN.
- The stack's public PKCE app client (`terraform output cognito_oauth_frontdoor_client_id`; the
  runtime's and gateway's JWT authorizers allow it by default) and a pool user.
- Bridge deps in the clone's venv (Python 3.10 or later; `mcp` 1.30.0 and `boto3` 1.43.111 both need it), from the exact pins in `requirements-frontdoor.txt`: `pip install -r requirements-frontdoor.txt`. It covers the bridge, `./run.sh diagram-fetch`, the gate MCP server and `scripts/e2e_deployed.py`. A missing package gives one line, for example `mcp is not installed; run: pip install -r requirements-frontdoor.txt`, and `diagram-fetch` exits 2. Bump each pin deliberately after checking advisories; boto3, botocore and mcp match the MCP runtime image pins.
- Membership of the companion's entitlement group (`<name_prefix>-users`, 3.4.0). Without it every call is denied (HTTP 403, or "GAC: not entitled" from the identity interceptor); see [`../docs/DEPLOY.md`](../docs/DEPLOY.md), "Entitlement group".
- A network path to the deployment. Private mode (default): the gateway and runtime accept requests only through your VPC endpoints; from other networks the names still resolve and requests get HTTP 403. Connect over your VPN/Direct Connect path (with DNS forwarding to a Route 53 Resolver inbound endpoint) or from an in-VPC desktop. Token sign-in to Cognito still uses public Cognito endpoints. See [`../docs/FRONT-DOORS.md`](../docs/FRONT-DOORS.md) "Private mode access (default)".
- The sign-in values, all non-secret (details and the shared-pool domain rule in
  [`../docs/FRONT-DOORS.md`](../docs/FRONT-DOORS.md), "Bridge sign-in"):
  ```bash
  GAC_AUTH_FLOW=pkce                     # the default
  GAC_COGNITO_DOMAIN='https://<prefix>.auth.<region>.amazoncognito.com'  # cognito_hosted_ui_domain (own pool)
  GAC_COGNITO_CLIENT_ID='<cognito_oauth_frontdoor_client_id>'            # the PUBLIC PKCE client
  GAC_COGNITO_SCOPES='openid gac-engine/invoke'                          # cognito_oauth_scope
  GAC_RUNTIME_ARN='<orchestrator_runtime_arn>'; GAC_RUNTIME_REGION='<region>'   # runtime upstream
  # or GAC_BRIDGE_UPSTREAM=gateway GAC_GATEWAY_URL='<gateway_url>' GAC_GATEWAY_REGION='<region>'
  ```
- Test harness and automation only (`GAC_AUTH_FLOW=password`): a gitignored env file (never
  commit it) with `GAC_COGNITO_REGION`, `GAC_COGNITO_USER_POOL_ID`, the confidential
  `GAC_COGNITO_CLIENT_ID` (`cognito_frontdoor_client_id`), `GAC_COGNITO_CLIENT_SECRET`,
  `GAC_COGNITO_USERNAME`, `GAC_COGNITO_PASSWORD`, and AWS credentials allowed to call
  `AdminInitiateAuth`. A client secret set under `GAC_AUTH_FLOW=pkce` is an error.

## Option A — Local stdio MCP bridge (turnkey, self-refreshing)

The IDE spawns `mcp_bridge.py` as a stdio MCP server. On the first tool call the bridge opens
your browser for the Cognito sign-in (PKCE), keeps the token in memory, refreshes it before it
expires, and relays each call to the deployed runtime (or the gateway). No secret or token is in
the client config, and it works as a fallback if the gateway is down.

1. Make sure the clone's `.venv` has the bridge deps.
2. Copy the `companion-engine` entry from `option_A_local_bridge` in `mcp.example.json` into your
   IDE's MCP config (Kiro: `.kiro/settings/mcp.json`; Claude Code: `.mcp.json`), set the absolute
   paths and the non-secret values, and reconnect. The three tools appear (`ask_companion`,
   `companion_kb`, `draw_diagram`).
3. The first call opens the browser. Headless, open the sign-in URL from the bridge's stderr
   (Kiro: the MCP server log).

**Kiro pair.** To keep Kiro's native OAuth entry for questions and still land diagram files, use
`kiro_example` in `mcp.example.json`: `gac-gateway` (native OAuth, its `gac___draw_diagram`
disabled) plus `gac-bridge` (this bridge with `GAC_BRIDGE_UPSTREAM=gateway`,
`GAC_BRIDGE_TOOLS=draw_diagram`, `GAC_GATEWAY_URL`, `GAC_GATEWAY_REGION`, `GAC_COGNITO_DOMAIN`,
`GAC_COGNITO_CLIENT_ID`, `GAC_COGNITO_SCOPES`, `GAC_DIAGRAM_WORKSPACE_ROOT`). Expect two sign-ins,
one per entry; if Kiro holds port 8080, the bridge uses the next free port of 8080-8084.

The bridge is fully wired: `runtime_client.py` POSTs to the runtime's `/invocations` endpoint
with the bearer token and retries once on a 401/403 after refreshing the token.
`pkce_token.py` signs in through a one-shot listener on 127.0.0.1 only (the single `/callback`,
state checked in constant time, closed before the code exchange, 180 s timeout) and refreshes the
token in memory; `cognito_token.py` holds the password flow and picks the provider from
`GAC_AUTH_FLOW`. Nothing is logged but token length and TTL; no token, code or verifier is
printed.

**Private mode.** The bridge calls `https://bedrock-agentcore.<region>.amazonaws.com`, which
resolves to the AgentCore data-plane VPC endpoint inside networks that use its private DNS. On a
network that cannot resolve the private DNS name (for example on-premises without Resolver
forwarding), set `GAC_AGENTCORE_ENDPOINT` to the endpoint-specific DNS name from the
`agentcore_dataplane_vpce_dns_names` output (`https://vpce-<id>-<suffix>.bedrock-agentcore.<region>.vpce.amazonaws.com`).
The bridge accepts only that form or the regional AgentCore name for `GAC_RUNTIME_REGION`
(https, port 443, no path, query, fragment or credentials) and refuses anything else before it
fetches a token. TLS for the vpce-specific name is unverified until post-deploy test 4b in
[`../docs/DEPLOY.md`](../docs/DEPLOY.md). From outside the approved networks the runtime returns
HTTP 403 (resource-policy deny) or the connection fails, and the bridge reports that the
endpoint is not reachable from this network.

**Diagrams.** `draw_diagram(prompt, environment="dev", title="", name="", out_dir="",
overwrite=False, planner="")` takes natural-language questions as well as draw requests ("what
talks to Bedrock", "draw the architecture end to end"). It asks the companion for the diagram
artefacts, verifies each SHA-256 and size (`diagram_writer.py`), and writes `<name>.drawio`,
`.svg`, `.png` and `.report.json` under `<workspace>/outputs/diagrams/<name>/`, one folder per
view of a multi-view answer (`diagram` then each `diagram_views` entry; each view all-or-nothing).
An honest decline writes nothing and returns the text with prompts that work. `planner` is `""`
or `"deterministic"` (downgrade only). It refuses traversal, symlinks, a root that is `/` or your
home folder, and overwrites unless asked, and returns only the paths it wrote. New env:
`GAC_BRIDGE_UPSTREAM` (`runtime` | `gateway`), `GAC_GATEWAY_URL`, `GAC_GATEWAY_REGION`,
`GAC_GATEWAY_TOOL_PREFIX`, `GAC_BRIDGE_TOOLS`, `GAC_DIAGRAM_WORKSPACE_ROOT`,
`GAC_DIAGRAM_LOCAL_DIR`, `GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE` (details in
[`../docs/FRONT-DOORS.md`](../docs/FRONT-DOORS.md)). `diagram_relay.py` is the gateway client the
bridge and `./run.sh diagram-fetch` share (`diagram-fetch --upstream runtime` uses the runtime
path instead); `python3 frontdoor/mcp_bridge.py --check-gateway-tools`
lists the gateway's tools (read-only). An upstream denial (HTTP 401/403, MCP -32002), another HTTP
or MCP error, or a network failure is one clean line plus a private-mode hint
(`upstream_errors.py`): `diagram-fetch` exits 3 (2 for config), the bridge tools return an MCP
error result, and nothing is written. `diagram-fetch` exits 0 when every view was saved, 1 when
the writer refused one, and 4 on an honest decline. `probe_fetch.py` backs
`./run.sh diagram-fetch --via-probe <function>`: it invokes the private access probe's diagram
mode (test stand-in) and saves the returned envelope through `diagram_writer.py`; it always uses
the password flow (`GAC_AUTH_FLOW` does not apply).

## Option B — Direct AgentCore Gateway HTTP path

Point the IDE's MCP client (an `http` server) at the gateway's **`/mcp` URL** with a Bearer JWT.
The gateway namespaces tools by its target name `gac` (`gac___ask_companion` / `gac___companion_kb`).
Keep the token fresh with `gateway_token.py` (the password flow), or use Kiro's native oauth/PKCE
block (see `../docs/FRONT-DOORS.md`). This path gets the diagram text and a manifest only:

```bash
source ~/.companion-frontdoor.env
export GAC_GATEWAY_URL='https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp'
export GAC_GATEWAY_TOKEN=$(python3 frontdoor/gateway_token.py print)   # one-shot
# or keep an env file fresh for a long session:
python3 frontdoor/gateway_token.py daemon &
```

Copy the `companion-engine` entry from `option_B_gateway_http` in `mcp.example.json`.

## Claude Code plugin (packaged agents)

For Claude Code, an installable plugin under `../plugin/` packages the orchestrator + specialist
sub-agents, a domain Skill, this MCP wiring, and a `Stop`-hook governance backstop that runs the
engine's gate over each sub-agent's completed response. See `../plugin/README.md`.

## One-command onboarding

`../onboarding/install-companion-frontdoor.sh` (or the `.ps1` on Windows) sets up the venv, runs
`status`, and optionally installs routing pointers into a parent workspace for BOTH front doors
(Kiro `inclusion: auto` steering pointer + a marked Claude Code `CLAUDE.md` block). It never
overwrites your own steering/CLAUDE.md and never deploys anything.
