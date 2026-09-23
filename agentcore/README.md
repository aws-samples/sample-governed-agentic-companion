# AgentCore reference code (deploy the governed companion to Amazon Bedrock AgentCore)

This directory is the **deployable reference** for putting the companion on Amazon Bedrock
AgentCore. It is intentionally thin: it wraps the same engine (`agents/`, `governance/`,
`knowledge/`) that runs locally — **no reasoning or governance is re-implemented here**. The
Governance Gate ships inside every image, so every response is gated by construction.

> A human performs every AWS/IdP mutation (Tenet 1). This code is built and deployed by a
> human via the AgentCore CLI; no agent provisions or invokes against AWS. See
> [`../docs/DEPLOY.md`](../docs/DEPLOY.md) for the full human-run sequence and the identity/OAuth
> setup.

## Two protocol faces of one engine

| Face | Entry | Serves | When to deploy |
|---|---|---|---|
| **Orchestrator** (`app/orchestrator/`) | `runtime_entrypoint.py` | Plain HTTP `/invocations` on :8080 | Always — the core runtime; the local stdio bridge front door targets this |
| **MCP runtime** (`app/mcp_runtime/`) | `runtime_mcp_entrypoint.py` | Stateless MCP `/mcp` on :8000 | When an AgentCore **Gateway** should front the engine directly (the gateway HTTP front door) |

Both COPY the engine from the **repo root** (single source of truth) and both are hardened:
non-root `appuser`, a `HEALTHCHECK`, and they launch under the ADOT `opentelemetry-instrument`
wrapper so custom + auto-instrumented OTEL export to CloudWatch (BYO containers must do this
themselves).

## Verified gateway topology (learn from the scar tissue)

- The gateway is **`protocol-type None`** with an **`http-runtime` target** (ARN-resolved) that
  points at the **mcp-runtime** — **not** an aggregated `protocol-type MCP` gateway (that shape
  rejects a runtime target), and not the `/invocations` app.
- The mcp-runtime must be **stateless** on **port 8000** at `/mcp` (the platform health-checks
  8000). Running stateful or on another port fails the health check.
- Inbound is **CUSTOM_JWT** (the builder's enterprise-IdP token). Outbound to the runtime is
  **OAuth M2M** (the gateway's own `client_credentials` grant + a resource-server scope).
- `GAC_GATEWAY_URL` is the **full path-based invocations URL** (`…/companion-engine/invocations`),
  not just the host.

## Files

```
agentcore/
  agentcore.json.example          # CLI project config: both runtimes + the gateway shape (copy -> gitignored agentcore.json)
  app/orchestrator/
    runtime_entrypoint.py         # thin /invocations adapter over Orchestrator.handle (gated by construction)
    Dockerfile                    # hardened: non-root + healthcheck + ADOT
    requirements.txt              # patched floors (bedrock-agentcore>=1.18.1, PyJWT>=2.13.0)
  app/mcp_runtime/
    runtime_mcp_entrypoint.py     # stateless MCP /mcp face; same governed tools (ask_companion, companion_kb)
    Dockerfile                    # hardened: non-root + healthcheck + ADOT
    requirements.txt              # + mcp>=1.23.0, uvicorn
```

## Local sanity (no AWS)

The entrypoints are import-guarded, so you can exercise the SDK-independent core without the
AgentCore/MCP SDKs installed:

```bash
python -c "import sys; sys.path.insert(0,'.'); \
  from agentcore.app.orchestrator.runtime_entrypoint import handle_invocation; \
  print(handle_invocation({'prompt': 'what is the deployment policy?', 'environment': 'dev'})['gated'])"
```

A `True` (gated) result means the adapter routed through the governed engine. Full deploy +
verify is in [`../docs/DEPLOY.md`](../docs/DEPLOY.md).

## Env vars (set in the gitignored `agentcore.json`, never baked in the image)

| Var | Purpose |
|---|---|
| `GAC_KNOWLEDGE_STORE=s3` + `GAC_KB_BUCKET`/`GAC_KB_PREFIX` | Persist the evolving KB to S3 |
| `GAC_KB_OVERLAY=s3` + `GAC_KB_OVERLAY_PREFIX` | Tier-B curated overlay on top of the image baseline |
| `GAC_INBOUND_AUTH` | `none` (default; platform validates JWT at the edge) or `jwt` (in-code seam, if the platform forwards the token) |

Grant the runtime execution role `s3:GetObject` on the KB prefix — and re-apply it after any
role-churning redeploy (an idempotent script is the durable fix until the grant is in IaC).
