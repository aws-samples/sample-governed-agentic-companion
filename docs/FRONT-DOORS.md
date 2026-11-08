<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Integration — connect Kiro & Claude Code to the companion

This is the integration guide for reaching the deployed companion from an agentic IDE as an **MCP
server**, over the **AgentCore Gateway**. It covers the native-OAuth path for Kiro and Claude
Code, and the local stdio bridge, which is the front door that lands diagram files. Every path preserves governance identically: the
response is gated in the cloud, a human deploys (Tenet 1), and systems of record stay read-only
(Tenet 3).

> **Never commit real endpoints, client ids, tokens, or account ids.** The kit gitignores
> `.kiro/settings/`, `config.yaml`, and `.env*`. Use the placeholders below and fill them from your
> `terraform output` (see [`DEPLOY.md`](DEPLOY.md)); keep secrets in a gitignored env file.

## Prerequisites

- The stack is deployed with `deploy_gateway = true` (see [`DEPLOY.md`](DEPLOY.md)).
- You have these values from `terraform output`:
  - `gateway_url` — the gateway's `/mcp` invocations URL
  - `cognito_oauth_frontdoor_client_id` — the **PKCE public** client id (Kiro's native OAuth
    and the stdio bridge's browser sign-in; no secret)
  - the resource-server scope is `<engine_resource_server_identifier>/invoke` (default
    `gac-engine/invoke`; the `cognito_oauth_scope` output shows the value in use)
  - the Cognito hosted UI domain for the bridge's sign-in (`GAC_COGNITO_DOMAIN`, see
    [Bridge sign-in](#bridge-sign-in-pkce-the-developer-default))
- A Cognito user exists in the pool (admin-created), for the OAuth login.
- From 3.4.0 that user is in the companion's entitlement group (`<name_prefix>-users` by default,
  in the token's `cognito:groups`). Otherwise every call is denied: HTTP 403 from the authorizer,
  or the identity interceptor's "GAC: not entitled". Groups are read when the token is minted, so
  sign in again after the pool's owner adds you ([`DEPLOY.md`](DEPLOY.md), "Entitlement group").
- The local helpers (stdio bridge, `diagram-fetch`, the gate MCP server, the E2E harness) need
  their pinned packages: `pip install -r requirements-frontdoor.txt` (Python 3.10 or later). A
  missing package prints one install line and exits 2.

The gateway namespaces each tool as `gac___<tool>`. The kit ships three tools, so it exposes
**`gac___ask_companion`**, **`gac___companion_kb`** and **`gac___draw_diagram`**. Confirm the exact
names in your IDE's MCP server view after connecting (an upgraded 3.2.x deployment lists
`gac___draw_diagram` only after the human-run target sync in `DEPLOY.md`, "Upgrading from 3.2.x").

## Private mode access (default)

With `network_access = "private"` (the default) the gateway and the orchestrator runtime accept
requests only through your AgentCore VPC endpoints. A valid token used from any other network gets
HTTP 403. The IDE therefore has to run on a network that reaches the endpoints:

| Option | What it is | Source |
|---|---|---|
| 1. AWS Client VPN into the companion VPC | Developers connect a VPN client; the client CIDR reaches the endpoints | [Client VPN](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/what-is.html) |
| 2. Site-to-Site VPN or Direct Connect from the corporate network | On-premises desktops reach the VPC over your existing private link | [Site-to-Site VPN](https://docs.aws.amazon.com/vpn/latest/s2svpn/VPC_VPN.html), [Direct Connect](https://docs.aws.amazon.com/directconnect/latest/UserGuide/Welcome.html) |
| 3. A desktop or dev environment inside the VPC (or a peered / transit-attached VPC) | For example Amazon WorkSpaces | [WorkSpaces](https://docs.aws.amazon.com/workspaces/latest/adminguide/amazon-workspaces.html) |

- **DNS.** Options 1 and 2 need DNS forwarding of `bedrock-agentcore.<region>.amazonaws.com` and
  `gateway.bedrock-agentcore.<region>.amazonaws.com` (and its subdomains) to a Route 53 Resolver
  inbound endpoint in the VPC, so those names resolve to the endpoints' private IPs
  ([inbound forwarding](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries.html)).
  The kit creates one when you set `enable_resolver_inbound_endpoint = true` (private mode; point
  your forwarders at the `resolver_inbound_ip_addresses` output), or you use your own. It carries
  its own hourly and per-query charge ([`COST.md`](COST.md),
  [Route 53 pricing](https://aws.amazon.com/route53/pricing/)). The endpoint-specific names
  (`vpce-...` DNS names) of interface endpoints are not publicly resolvable
  ([interface endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/interface-endpoints.html)).
  The regional and gateway names are: outside the VPC they resolve to public AWS addresses, and a
  request sent there is denied by the resource policy (HTTP 403).
  Without forwarding, the stdio bridge can use the endpoint-specific name through
  `GAC_AGENTCORE_ENDPOINT` (see [`../frontdoor/README.md`](../frontdoor/README.md)); TLS for that
  name is unverified until post-deploy test 4b.
- **Client CIDRs.** The network the IDE connects from must be in `private_client_cidrs`. Those
  CIDRs reach only the gateway and data-plane endpoints on TCP 443 (and `bedrock-runtime` only when
  `bedrock_runtime_client_access = true`).
- **What still uses public AWS endpoints.** Cognito managed login / hosted UI, the token endpoint
  (the bridge's PKCE sign-in), and `AdminInitiateAuth` (the test harness's password flow,
  `cognito_token.py`) stay on public Cognito endpoints,
  because the kit's user pool has a domain and user pools with a domain cannot complete requests
  through a VPC endpoint ([Cognito and AWS PrivateLink](https://docs.aws.amazon.com/cognito/latest/developerguide/vpc-interface-endpoints.html)).
  Entra ID sign-in uses Microsoft's endpoints. These carry credentials and tokens, not knowledge.
- **Fully isolated hosts.** A host in a VPC with no NAT (for example the sandbox VPC) cannot reach
  Cognito, so it cannot mint a token. Mint on a workstation and copy the token for the probe, or
  provide egress to Cognito.
- **CloudShell.** CloudShell VPC environments run in your subnets
  ([CloudShell VPC environments](https://docs.aws.amazon.com/cloudshell/latest/userguide/creating-vpc-environment.html)).
  Without NAT they have no internet, so they suit the probe (`terraform output
  private_access_test_command`) but not Cognito sign-in.
- **Before you hand anything out**, run the post-deploy tests in [`DEPLOY.md`](DEPLOY.md); tests 2
  and 4a (denial from outside) are a hard gate.
- **The IDE assistant still sees tool results.** Private mode does not control what the IDE does
  with a response; see [`SECURITY.md`](SECURITY.md) "What private mode does not protect".

`network_access = "public"` (demo opt-in, with `acknowledge_public_endpoint = true`) keeps the
previous behaviour: any network can reach the gateway with a valid token.

## Which front door lands the files

- A **direct OAuth MCP client** (Kiro's native `oauth` entry, Claude Code over HTTP) gets the
  gated text and, for `gac___draw_diagram`, a compact manifest only. It cannot save the files.
- The **local stdio bridge** (`frontdoor/mcp_bridge.py`) and `./run.sh diagram-fetch` land the
  files: they verify each SHA-256 and write them under your workspace.
- **PKCE browser sign-in is the developer default** for the bridge and `diagram-fetch`
  (`GAC_AUTH_FLOW=pkce`). The password flow (`GAC_AUTH_FLOW=password`) is for the test harness
  (`scripts/e2e_deployed.py`) and automation only.

## Bridge sign-in (PKCE, the developer default)

The bridge and `diagram-fetch` sign in with OAuth 2.0 authorization code + PKCE (S256) against
Cognito's hosted endpoints (`/oauth2/authorize`, `/oauth2/token`), using the stack's public PKCE
client. You need no client secret, no password and no AWS credentials. Every value is non-secret:

| Env | Value |
|---|---|
| `GAC_AUTH_FLOW` | `pkce` (the default; may be omitted) |
| `GAC_COGNITO_CLIENT_ID` | `cognito_oauth_frontdoor_client_id` (the public PKCE client). Under `GAC_AUTH_FLOW=password` this variable is instead the confidential `cognito_frontdoor_client_id` |
| `GAC_COGNITO_DOMAIN` | A stack that owns its pool (`manage_identity_pool = true`, the default): the `cognito_hosted_ui_domain` output. A stack on a shared pool: `https://<platform output shared_user_pool_domain>.auth.<region>.amazoncognito.com` (the companion's own `cognito_hosted_ui_domain` output names a domain it never creates) |
| `GAC_COGNITO_SCOPES` | `openid <id>/invoke`, the `cognito_oauth_scope` output (default `openid gac-engine/invoke`). Exactly these two: a missing invoke scope or any extra scope is a config error |
| `GAC_COGNITO_REDIRECT_PORTS` | optional, default `8080-8084` (a range or a comma list, at most 10) |
| `GAC_COGNITO_LOGIN_TIMEOUT` | optional, seconds, 30-600, default 180 |
| `GAC_COGNITO_NO_BROWSER` | optional, `1` prints the URL without opening a browser |
| `GAC_GATEWAY_REGION` / `GAC_RUNTIME_REGION` | set the upstream's Region explicitly: the `GAC_COGNITO_REGION` fallback belongs to the password flow |

How it behaves:

- The **first tool call** opens your browser at the Cognito sign-in page. Sign in; the tab says
  you can close it, and the call goes on. Headless, or when no browser opens, the sign-in URL is
  printed to the bridge's stderr: in Kiro, the MCP server log (output panel); for
  `diagram-fetch`, the terminal.
- The bridge listens on **127.0.0.1 only**, on the first free port of 8080-8084, for that one
  `/callback`, and closes the listener before it exchanges the code. If Kiro's own OAuth holds
  8080, the bridge uses the next port; a quick repeat sign-in may also move to the next port
  while the last one is in TIME_WAIT. Both are expected: every port in the range is a registered
  callback.
- Tokens stay **in the bridge's memory only** (no file, no keychain). One sign-in lasts the IDE
  session: the bridge refreshes the token before it expires. `diagram-fetch` is a short-lived
  process, so it signs in on each run (usually one click, because the hosted UI remembers you).
- With both Kiro entries below (`gac-gateway` and `gac-bridge`) expect **two sign-ins**, one per
  entry.
- If the IDE cancels the first call while you sign in, retry it: the sign-in finishes in the
  background and the retried call uses it.
- A set `GAC_COGNITO_CLIENT_SECRET` under `GAC_AUTH_FLOW=pkce` is an error ("uses the public PKCE
  client and no client secret"): unset it, or set `GAC_AUTH_FLOW=password` for the test-harness
  flow. `./run.sh diagram-fetch --via-probe` is the test-harness path and always uses the
  password flow (`GAC_AUTH_FLOW` does not apply).
- The token carries `cognito:groups`, so the 3.4.0 entitlement check applies unchanged. Both
  bridge upstreams accept the PKCE client: the gateway and the orchestrator runtime allow-lists
  include it when `enable_oauth_frontdoor_client = true` (the default).

## Option A — Kiro (native OAuth / PKCE) — recommended

Add this server to `~/.kiro/settings/mcp.json` (or the workspace `.kiro/settings/mcp.json`) under
`mcpServers`. Kiro runs the PKCE flow and auto-refreshes the token — no manual Bearer token, no
bridge, no env file.

```json
{
  "mcpServers": {
    "gac-gateway": {
      "url": "https://<GATEWAY_HOST>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
      "oauth": {
        "clientId": "<COGNITO_OAUTH_FRONTDOOR_CLIENT_ID>",
        "redirectUri": "http://127.0.0.1:8080",
        "oauthScopes": ["openid", "gac-engine/invoke"]
      },
      "disabled": false,
      "autoApprove": [],
      "disabledTools": []
    }
  }
}
```

**Connect + test:**
1. Open the Kiro **MCP Server** view (or Command Palette → "MCP") and reconnect `gac-gateway`.
2. A browser opens to the Cognito hosted UI — sign in with your pool user. It redirects to
   `http://127.0.0.1:8080/oauth/callback` and Kiro captures the token.
3. The server shows **connected** and lists its tools.
4. In chat: *"Use the gac-gateway companion: what is the deployment policy?"* — you should get a
   grounded answer with the **governance-outcome footer** (trust zone, tenets checked, sources).

The PKCE client allows `http://127.0.0.1:{8080..8084}` with `/oauth/callback` (Kiro) and
`/callback` (the bridge), plus the `localhost` equivalents. Keep the `redirectUri` exactly
`http://127.0.0.1:8080` (Kiro appends `/oauth/callback`), and free port 8080.

**Kiro with diagram files: add the bridge.** Kiro's native OAuth entry cannot save diagram files.
Add the stdio bridge next to it for `draw_diagram`, and disable the gateway's own draw tool so the
assistant sees one `draw_diagram`, the bridge's (this pair is `kiro_example` in
[`../frontdoor/mcp.example.json`](../frontdoor/mcp.example.json)). Every value is non-secret:

```json
{
  "mcpServers": {
    "gac-gateway": {
      "url": "https://<GATEWAY_HOST>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
      "oauth": {
        "clientId": "<COGNITO_OAUTH_FRONTDOOR_CLIENT_ID>",
        "redirectUri": "http://127.0.0.1:8080",
        "oauthScopes": ["openid", "gac-engine/invoke"]
      },
      "disabled": false,
      "autoApprove": [],
      "disabledTools": ["gac___draw_diagram"]
    },
    "gac-bridge": {
      "command": "/ABSOLUTE/PATH/TO/governed-agentic-companion/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/governed-agentic-companion/frontdoor/mcp_bridge.py"],
      "env": {
        "GAC_AUTH_FLOW": "pkce",
        "GAC_BRIDGE_UPSTREAM": "gateway",
        "GAC_BRIDGE_TOOLS": "draw_diagram",
        "GAC_GATEWAY_URL": "https://<GATEWAY_HOST>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
        "GAC_GATEWAY_REGION": "<REGION>",
        "GAC_COGNITO_DOMAIN": "https://<DOMAIN_PREFIX>.auth.<REGION>.amazoncognito.com",
        "GAC_COGNITO_CLIENT_ID": "<COGNITO_OAUTH_FRONTDOOR_CLIENT_ID>",
        "GAC_COGNITO_SCOPES": "openid gac-engine/invoke",
        "GAC_DIAGRAM_WORKSPACE_ROOT": "/ABSOLUTE/PATH/TO/your-project"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

`disabledTools` takes the tool name as Kiro's MCP server view shows it (`gac___draw_diagram`);
if your Kiro version lists it differently, use the name shown there. Install the bridge's
packages into the clone's `.venv` first (`pip install -r requirements-frontdoor.txt`).

## Option B — Claude Code (MCP via gateway)

Claude Code reads MCP servers from `.mcp.json`. Use the same gateway URL + PKCE client. If your
Claude Code version supports the OAuth block, use the Option A shape; otherwise use a Bearer header
kept fresh by the token helper:

```json
{
  "mcpServers": {
    "gac-gateway": {
      "type": "http",
      "url": "https://<GATEWAY_HOST>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
      "headers": { "Authorization": "Bearer ${GAC_GATEWAY_TOKEN}" }
    }
  }
}
```

Mint/refresh the token (never commit it):

```bash
GAC_GATEWAY_URL="https://<GATEWAY_HOST>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp" \
AWS_PROFILE=<your-profile> python3 frontdoor/gateway_token.py --daemon   # or --write-env ~/.gac-frontdoor.env
```

The packaged **Claude Code plugin** ([`../plugin/`](../plugin/README.md)) bundles the orchestrator +
specialist sub-agents, a Skill, MCP wiring, and a Stop-hook governance backstop:

```
/plugin marketplace add <git-url-of-this-repo>
/plugin install governed-agentic-companion
```

## Option C — local stdio bridge, direct to the runtime

When the gateway is not deployed, or as a gateway-down fallback, the IDE spawns
`frontdoor/mcp_bridge.py`, which signs in through the browser (PKCE), refreshes the token, and
relays each call to the orchestrator runtime. Kiro entry (all values non-secret):

```json
{
  "mcpServers": {
    "gac-engine": {
      "command": "/ABSOLUTE/PATH/TO/governed-agentic-companion/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/governed-agentic-companion/frontdoor/mcp_bridge.py"],
      "env": {
        "GAC_AUTH_FLOW": "pkce",
        "GAC_BRIDGE_UPSTREAM": "runtime",
        "GAC_RUNTIME_ARN": "<orchestrator_runtime_arn>",
        "GAC_RUNTIME_REGION": "<REGION>",
        "GAC_COGNITO_DOMAIN": "https://<DOMAIN_PREFIX>.auth.<REGION>.amazoncognito.com",
        "GAC_COGNITO_CLIENT_ID": "<COGNITO_OAUTH_FRONTDOOR_CLIENT_ID>",
        "GAC_COGNITO_SCOPES": "openid gac-engine/invoke",
        "GAC_DIAGRAM_WORKSPACE_ROOT": "/ABSOLUTE/PATH/TO/your-project"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

**Test harness and automation only (`GAC_AUTH_FLOW=password`).** The Terraform-generated
`agentcore/terraform/generated/gac-frontdoor.env` skeleton is the password-flow env (the
confidential `cognito_frontdoor_client_id`, its secret, a pool user and password, and AWS
credentials allowed to call `AdminInitiateAuth`). Use it only with `GAC_AUTH_FLOW=password`,
never as a developer's IDE config:

```
GAC_AUTH_FLOW=password
GAC_RUNTIME_ARN=<orchestrator_runtime_arn>
GAC_RUNTIME_REGION=<REGION>
GAC_COGNITO_REGION=<REGION>
GAC_COGNITO_USER_POOL_ID=<cognito_user_pool_id>
GAC_COGNITO_CLIENT_ID=<cognito_frontdoor_client_id>
GAC_COGNITO_CLIENT_SECRET=<from the Cognito app client — do not commit>
GAC_COGNITO_USERNAME=<your pool user>
GAC_COGNITO_PASSWORD=<your pool user password — do not commit>
```

## Diagrams: landing files needs the stdio bridge
> **Preview.** The diagram specialist behind `draw_diagram` is a preview. It is held out of any
> public release (for example a public samples port) until the owner accepts its redesign.
> Current limits: it does not collaborate with peer specialists or the orchestrator (it reads
> only their hand-authored `diagram_facts`); the deterministic keyword planner is the default and
> largely ignores intent such as "single view", "both" or "public"; the view splitter overrides
> an explicit single view; and each view is capped at 24 nodes (6 for a generic view), so a dense
> grouped single overview cannot be drawn. The front-door plumbing below (bridge, writer,
> sign-in) is not affected.


`draw_diagram` draws AWS architecture diagrams from the knowledge base. It takes natural-language
questions as well as draw requests: "draw the architecture end to end", "what talks to Bedrock",
"how does a request reach the knowledge base", "diagram our security boundaries", or a preset such
as "draw our private network path". Natural-language diagram questions go through
`draw_diagram`, `./run.sh diagram` or `./run.sh diagram-fetch`; `ask_companion` and
`./run.sh ask` keep their routing and do not draw them.

A large request can give up to 4 views. **Landing the files on your machine needs the local stdio
bridge** (`frontdoor/mcp_bridge.py`): its `draw_diagram` asks the companion for the artefacts,
checks each SHA-256 and size, and writes each view's `<name>.drawio`, `<name>.svg`, `<name>.png`
and `<name>.report.json` under `<workspace>/outputs/diagrams/<view name>/`, one folder per view
(`<base>-1-<view id>`, `<base>-2-<view id>`, ...; a single view keeps its plain name). Each view
is saved all-or-nothing; a view the writer refuses is reported and the others still land. An
older bridge saves view 1 only. You need no renderer locally: the PNG is rendered in the runtime
image. The bridge returns the gated text plus the paths it wrote, never the file content. An IDE
connected straight to the gateway (`gac___draw_diagram`) receives the gated text and a compact
manifest (names, sizes, short hashes, one entry per view) only.

When the knowledge base cannot support a request, the answer is an honest decline: what it
recognised, what is missing, and up to 5 prompts that work. No file is written.

```
draw_diagram(prompt, environment="dev", title="", name="", out_dir="", overwrite=False, planner="")
```

`planner` accepts `""` or `"deterministic"`: a caller can only downgrade the planner, never turn
on the opt-in Bedrock planner.

The bridge refuses to write outside the workspace root, through a symlink, or over an existing
file (unless `overwrite=true`), and creates `outputs/diagrams/.gitignore` (`*`) the first time.

| Env (the bridge's process) | Meaning |
|---|---|
| `GAC_BRIDGE_UPSTREAM` | `runtime` (default: the runtime ARN path above) or `gateway` |
| `GAC_GATEWAY_URL` | gateway upstream: `https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` (any other URL is refused, so the token only goes to an AgentCore gateway) |
| `GAC_GATEWAY_REGION` | the gateway's Region (set it under PKCE; the `GAC_COGNITO_REGION` fallback is a password-flow variable) |
| `GAC_GATEWAY_TOOL_PREFIX` | the target prefix (default `gac___`) |
| `GAC_BRIDGE_TOOLS` | tools to register (default `ask_companion,companion_kb,draw_diagram`) |
| `GAC_DIAGRAM_WORKSPACE_ROOT` | your project folder; without it the bridge's working folder must contain `.git`, `.kiro` or `.claude`; never `/` or your home folder |
| `GAC_DIAGRAM_LOCAL_DIR` | output folder (default `<root>/outputs/diagrams`) |
| `GAC_DIAGRAM_ALLOW_OUTSIDE_WORKSPACE` | `true` to allow an output folder outside the root (env only, never a tool argument) |

With `GAC_BRIDGE_UPSTREAM=gateway`, `ask_companion` and `companion_kb` relay through the gateway
too, so one bridge configuration needs no runtime ARN. The Claude Code plugin's `companion-files`
server is this bridge in gateway mode with only `draw_diagram`. The bridge and `diagram-fetch`
sign in as described in [Bridge sign-in](#bridge-sign-in-pkce-the-developer-default).

From a terminal: `./run.sh diagram "<request>"` renders locally (needs `./run.sh diagram-assets`
first), and `./run.sh diagram-fetch "<request>"` calls `gac___draw_diagram` through the gateway
with the same writer (`--upstream runtime` calls the runtime ARN instead, the bridge's default
upstream). `diagram-fetch` exits 0 when every delivered view was saved, 1 when the writer refused
a view, 2 on a local configuration error (including a missing front-door package), 3 when the
upstream denied or failed, and 4 on an honest decline, on every upstream including `--via-probe`. A diagram answer the governance gate blocks also exits 4 on `diagram-fetch` (the upstream returns the gated text with no files, the same shape as a decline), while `./run.sh diagram` prints the blocked text and exits 0, as every other local `./run.sh` answer the gate blocks does. In both doors no file is written. Nothing is written when an upstream result is over 5 MiB: the runtime path reads the
body with the cap before parsing it, and the gateway path checks the summed tool-result text
against the cap after the MCP SDK has parsed the message. When an upstream refuses (in private mode
from outside the VPC: HTTP 403), `diagram-fetch` prints `Not fetched: <gateway|runtime> denied the
request (HTTP 403: <short server message>)` and a hint, exits 3 and writes nothing; the bridge
tools return the same text as an MCP error result. Without a workstation network path,
`./run.sh diagram-fetch --via-probe <function> "<request>"` runs the diagram through the private
access probe inside the VPC (a test stand-in; `docs/DEPLOY.md`). `--via-probe` is the
test-harness path and always uses the password flow (`GAC_AUTH_FLOW` does not apply).

## Public demo stack with Kiro

On a public-mode stack (`network_access = "public"`, a demo opt-in) the IDE can use the stdio
bridge from any network. Add the bridge to `.kiro/settings/mcp.json`. It signs in through the
browser, so every value is non-secret; take them from the environment as below, or paste them:

```json
{
  "mcpServers": {
    "gac-public": {
      "command": "/ABSOLUTE/PATH/TO/governed-agentic-companion/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/governed-agentic-companion/frontdoor/mcp_bridge.py"],
      "env": {
        "GAC_BRIDGE_UPSTREAM": "${GAC_BRIDGE_UPSTREAM}",
        "GAC_GATEWAY_URL": "${GAC_GATEWAY_URL}",
        "GAC_GATEWAY_REGION": "${GAC_GATEWAY_REGION}",
        "GAC_GATEWAY_TOOL_PREFIX": "${GAC_GATEWAY_TOOL_PREFIX}",
        "GAC_RUNTIME_ARN": "${GAC_RUNTIME_ARN}",
        "GAC_RUNTIME_REGION": "${GAC_RUNTIME_REGION}",
        "GAC_AUTH_FLOW": "pkce",
        "GAC_COGNITO_DOMAIN": "${GAC_COGNITO_DOMAIN}",
        "GAC_COGNITO_CLIENT_ID": "${GAC_COGNITO_CLIENT_ID}",
        "GAC_COGNITO_SCOPES": "openid gac-engine/invoke",
        "GAC_DIAGRAM_WORKSPACE_ROOT": "${GAC_DIAGRAM_WORKSPACE_ROOT}"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

- `GAC_BRIDGE_UPSTREAM` is `gateway` (tools through the gateway, `GAC_GATEWAY_TOOL_PREFIX`
  `gac___`) or `runtime` (the orchestrator runtime ARN).
- The values live in `~/.companion-frontdoor.env` (`chmod 600`, outside any repository), loaded by
  the shell that starts Kiro (for example `set -a; . ~/.companion-frontdoor.env; set +a`, then
  start Kiro from that shell).
- The bridge signs in through the browser and refreshes the token in memory.
  `GAC_COGNITO_CLIENT_ID` is the PKCE client (`cognito_oauth_frontdoor_client_id`). No token,
  password or client secret is needed or written into `mcp.json`. `autoApprove` stays empty.
- Prerequisites: `pip install -r requirements-frontdoor.txt`, and membership of the companion's
  entitlement group (`<name_prefix>-users`); without it every call returns HTTP 403 or "GAC: not
  entitled".
- Diagram files land under `$GAC_DIAGRAM_WORKSPACE_ROOT/outputs/diagrams/<view name>/`, one
  folder per view.

## One-command onboarding

From inside your clone, wire routing pointers for BOTH IDEs without touching your own files:

```bash
bash onboarding/install-companion-frontdoor.sh          # macOS / Linux / WSL
powershell -ExecutionPolicy Bypass -File onboarding\Install-CompanionFrontDoor.ps1   # Windows
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Browser flow doesn't open / redirect fails | Kiro native OAuth: `redirectUri` must be exactly `http://127.0.0.1:8080`; free port 8080. Bridge: open the URL from the MCP server log (stderr) |
| The Cognito page shows `unauthorized_client`, `invalid_request` or a redirect mismatch (bridge) | `GAC_COGNITO_CLIENT_ID` is not the PKCE client: under `GAC_AUTH_FLOW=pkce` use `cognito_oauth_frontdoor_client_id`, not the confidential `cognito_frontdoor_client_id` |
| Browser shows a DNS error or "domain not found" (bridge) | `GAC_COGNITO_DOMAIN` is wrong. On a shared pool use the platform stack's domain, `https://<shared_user_pool_domain>.auth.<region>.amazoncognito.com`, not the companion's `cognito_hosted_ui_domain` output |
| `Not fetched: no free loopback port in 8080,...` | Ports 8080-8084 are all in use on 127.0.0.1: free one, or set `GAC_COGNITO_REDIRECT_PORTS` to registered ports that are free |
| `Not fetched: Cognito denied the sign-in (browser sign-in not completed within 180 s; retry the call)` | The sign-in was not finished in time: retry the call and complete the browser sign-in |
| `Not fetched: GAC_AUTH_FLOW=pkce uses the public PKCE client and no client secret ...` | A client secret is set in the bridge's environment (often an old password-flow env file): unset `GAC_COGNITO_CLIENT_SECRET`, or set `GAC_AUTH_FLOW=password` for the test-harness flow |
| `Not fetched: GAC_COGNITO_DOMAIN must be ...` or `GAC_COGNITO_SCOPES must be ...` | The domain must be a Cognito prefix domain (`https://<prefix>.auth.<region>.amazoncognito.com`, no path); the scopes must be exactly `openid` and the `cognito_oauth_scope` value |
| `insufficient_scope` on a call | The token must carry `<engine_resource_server_identifier>/invoke` (default `gac-engine/invoke`, output `cognito_oauth_scope`); check `oauthScopes` |
| 401/403 after login | The token's client must be in the gateway and runtime allow-lists. The PKCE client is, while `enable_oauth_frontdoor_client = true` (the default) |
| 403 "GAC: not entitled", or an authorizer 403 with a valid token (3.4.0) | The user is not in the companion's entitlement group (`<name_prefix>-users`). The pool's owner adds them (`docs/DEPLOY.md`, "Entitlement group"); then sign in again, because groups are read when the token is minted |
| `Not fetched: ... is not installed; run: pip install -r requirements-frontdoor.txt` | A front-door package is missing from this Python; install the pinned set |
| Tool names differ from the names you add to `autoApprove` | `autoApprove` is empty on purpose: each companion tool call returns proprietary knowledge into the IDE assistant's context, so every call asks for confirmation. If your team adds tool names, use the names shown in the server view, knowing those calls then run without confirmation |
| HTTP 403 with a valid token (private mode) | The request did not arrive through the VPC endpoint: connect over the VPN / Direct Connect path or from an in-VPC desktop, and check DNS forwarding resolves the gateway name to private IPs |
| Connection timeout (private mode) | Your network is not in `private_client_cidrs`, or there is no route to the VPC |
| Gateway target `FAILED` at deploy | The MCP runtime crashed on boot — check `/aws/bedrock-agentcore/runtimes/*` logs |

## Related resources

- [`DEPLOY.md`](DEPLOY.md) — provision the stack and get the output values used above.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the front-door → gateway → runtime topology.
- [`../frontdoor/README.md`](../frontdoor/README.md) — bridge/token-helper details.
