<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: MIT-0 -->

# Integration — connect Kiro & Claude Code to the companion

This is the integration guide for reaching the deployed companion from an agentic IDE as an **MCP
server**, over the **AgentCore Gateway**. It covers the recommended native-OAuth path for Kiro and
Claude Code, plus a local stdio-bridge fallback. Every path preserves governance identically: the
response is gated in the cloud, a human deploys (Tenet 1), and systems of record stay read-only
(Tenet 3).

> **Never commit real endpoints, client ids, tokens, or account ids.** The kit gitignores
> `.kiro/settings/`, `config.yaml`, and `.env*`. Use the placeholders below and fill them from your
> `terraform output` (see [`DEPLOY.md`](DEPLOY.md)); keep secrets in a gitignored env file.

## Prerequisites

- The stack is deployed with `deploy_gateway = true` (see [`DEPLOY.md`](DEPLOY.md)).
- You have these values from `terraform output`:
  - `gateway_url` — the gateway's `/mcp` invocations URL
  - `cognito_oauth_frontdoor_client_id` — the **PKCE public** client id (the IDE OAuth path)
  - the resource-server scope is `gac-engine/invoke`
- A Cognito user exists in the pool (admin-created), for the OAuth login.

The gateway namespaces each tool as `gac___<tool>`. The kit ships two tools, so it exposes
**`gac___ask_companion`** and **`gac___companion_kb`**. Confirm the exact names in your IDE's MCP
server view after connecting.

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
      "autoApprove": ["gac___ask_companion", "gac___companion_kb"],
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

The PKCE client only allows `http://127.0.0.1:8080/oauth/callback` and `localhost:8080` — keep the
`redirectUri` exactly `http://127.0.0.1:8080` (Kiro appends `/oauth/callback`), and free port 8080.

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

## Option C — local stdio bridge (fallback; direct to the runtime)

When the gateway is not deployed, or as a gateway-down fallback, the IDE spawns
`frontdoor/mcp_bridge.py`, which mints + auto-refreshes a Cognito token and relays each call to the
orchestrator runtime. Kiro entry:

```json
{
  "mcpServers": {
    "gac-engine": {
      "command": "bash",
      "args": ["-c", "source ~/.gac-frontdoor.env && exec /ABSOLUTE/PATH/TO/governed-agentic-companion/.venv/bin/python /ABSOLUTE/PATH/TO/governed-agentic-companion/frontdoor/mcp_bridge.py"],
      "disabled": false,
      "autoApprove": ["companion_kb"]
    }
  }
}
```

`~/.gac-frontdoor.env` (Terraform generates a skeleton at `agentcore/terraform/generated/gac-frontdoor.env`):

```
GAC_RUNTIME_ARN=<orchestrator_runtime_arn>
GAC_RUNTIME_REGION=<REGION>
GAC_COGNITO_REGION=<REGION>
GAC_COGNITO_USER_POOL_ID=<cognito_user_pool_id>
GAC_COGNITO_CLIENT_ID=<cognito_frontdoor_client_id>
GAC_COGNITO_CLIENT_SECRET=<from the Cognito app client — do not commit>
GAC_COGNITO_USERNAME=<your pool user>
GAC_COGNITO_PASSWORD=<your pool user password — do not commit>
```

## One-command onboarding

From inside your clone, wire routing pointers for BOTH IDEs without touching your own files:

```bash
bash onboarding/install-companion-frontdoor.sh          # macOS / Linux / WSL
powershell -ExecutionPolicy Bypass -File onboarding\Install-CompanionFrontDoor.ps1   # Windows
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Browser flow doesn't open / redirect fails | `redirectUri` must be exactly `http://127.0.0.1:8080`; free port 8080 |
| `insufficient_scope` on a call | The token must carry `gac-engine/invoke`; check `oauthScopes` |
| 401/403 after login | The token's client must be in the runtime allow-list (the PKCE client is, by default) |
| Tool names differ from `autoApprove` | Harmless — update `autoApprove` to the names shown in the server view |
| Gateway target `FAILED` at deploy | The MCP runtime crashed on boot — check `/aws/bedrock-agentcore/runtimes/*` logs |

## Related resources

- [`DEPLOY.md`](DEPLOY.md) — provision the stack and get the output values used above.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the front-door → gateway → runtime topology.
- [`../frontdoor/README.md`](../frontdoor/README.md) — bridge/token-helper details.
