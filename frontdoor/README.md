# Front doors — connect your IDE (Kiro / Claude Code / any MCP client)

The companion is reached from any MCP-capable agentic IDE over the same governed engine.
Two paths, both exposing the identical two tools — pick one.

- `ask_companion(prompt, environment="dev")` — route a question to the governed orchestrator.
- `companion_kb(query, environment="dev")` — read-only knowledge-base query (gated).

Governance is identical on both paths: the runtime's response is already gated in the cloud;
a human deploys (Tenet 1); systems of record stay read-only (Tenet 3).

## Prerequisites

- A deployed AgentCore runtime (see `../agentcore/README.md`) and its ARN.
- A Cognito user-pool app client for the runtime's CUSTOM_JWT authorizer.
- Bridge deps in the clone's venv: `pip install 'mcp>=1.23.0' boto3 requests`.
- A gitignored env file with your identity + runtime config (never commit it):
  ```bash
  # ~/.companion-frontdoor.env  (chmod 600)
  export GAC_RUNTIME_ARN='arn:aws:bedrock-agentcore:<region>:<acct>:runtime/<name>'
  export GAC_RUNTIME_REGION='<region>'
  export GAC_COGNITO_REGION='<region>'
  export GAC_COGNITO_USER_POOL_ID='<region>_XXXX'
  export GAC_COGNITO_CLIENT_ID='<app-client-id>'
  export GAC_COGNITO_CLIENT_SECRET='<app-client-secret>'
  export GAC_COGNITO_USERNAME='<user>'
  export GAC_COGNITO_PASSWORD='<password>'
  ```

## Option A — Local stdio MCP bridge (turnkey, self-refreshing)

The IDE spawns `mcp_bridge.py` as a stdio MCP server. The bridge mints and auto-refreshes the
Cognito access token and relays each call to the deployed runtime. Best for a hands-off setup —
no token in client config, and it works as a fallback if the gateway is down.

1. Ensure the env file above exists and the venv has the bridge deps.
2. Copy the `companion-engine` entry from `option_A_local_bridge` in `mcp.example.json` into your
   IDE's MCP config (Kiro: `.kiro/settings/mcp.json`; Claude Code: `.mcp.json`), set the absolute
   path to `mcp_bridge.py`, and reconnect. The two tools appear.

The bridge is fully wired: `runtime_client.py` POSTs to the runtime's `/invocations` endpoint
with the bearer token and retries once on a 401/403 after refreshing the token. `cognito_token.py`
caches and pre-expiry-refreshes the token; nothing is logged but token length and TTL.

## Option B — Direct AgentCore Gateway HTTP path

Point the IDE's MCP client (an `http` server) at the gateway's **full path-based invocations URL**
with a Bearer JWT. Keep the token fresh with `gateway_token.py`:

```bash
source ~/.companion-frontdoor.env
export GAC_GATEWAY_URL='https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/companion-engine/invocations'
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
