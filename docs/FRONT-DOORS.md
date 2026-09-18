# Front doors

See [`../frontdoor/README.md`](../frontdoor/README.md) for the two ways to connect an MCP-capable
IDE (Kiro, Claude Code, or other):

- **Option A — local stdio MCP bridge** (`frontdoor/mcp_bridge.py`): turnkey, self-refreshing;
  works as a gateway-down fallback.
- **Option B — direct gateway HTTP path**: point the IDE's `http` MCP server at the gateway
  invocations URL with a Bearer JWT; keep it fresh with `frontdoor/gateway_token.py`
  (`print` / `write-env` / `daemon`).

Copy the server entry from [`../frontdoor/mcp.example.json`](../frontdoor/mcp.example.json) into
your IDE's MCP config and merge with any existing `mcpServers`. Never commit real paths, ARNs,
tokens, or account ids — the kit gitignores `.kiro/settings/` and `config.yaml`.
