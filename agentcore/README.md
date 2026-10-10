# AgentCore reference code (deploy the governed companion to Amazon Bedrock AgentCore)

> **⚠️ Sample code — not for production as-is.** Provided for demonstration and educational
> purposes. The OAuth M2M, JWT auth, and IAM role grants referenced here are illustrative and
> **not intended for production use without additional security review, testing, and hardening**.

This directory is the **deployable reference** for putting the companion on Amazon Bedrock
AgentCore. It is intentionally thin: it wraps the same engine (`agents/`, `governance/`,
`knowledge/`) that runs locally — **no reasoning or governance is re-implemented here**. The
Governance Gate ships inside every image, so every response is gated by construction.

> A human performs every AWS/IdP mutation (Tenet 1). These container images are built and the
> stack is provisioned by a human via **Terraform** ([`terraform/`](terraform/), `terraform
> apply`); no agent provisions or invokes against AWS. See [`../docs/DEPLOY.md`](../docs/DEPLOY.md)
> for the full human-run sequence and [`terraform/README.md`](terraform/README.md) for the resource
> inventory and identity/OAuth setup.

## Two protocol faces of one engine

| Face | Entry | Serves | When to deploy |
|---|---|---|---|
| **Orchestrator** (`app/orchestrator/`) | `runtime_entrypoint.py` | Plain HTTP `/invocations` on :8080 | Always — the core runtime; the local stdio bridge front door targets this |
| **MCP runtime** (`app/mcp_runtime/`) | `runtime_mcp_entrypoint.py` | Stateless MCP `/mcp` on :8000 | When an AgentCore **Gateway** should front the engine directly (the gateway HTTP front door) |

Both COPY the engine from the **repo root** (single source of truth) and both are hardened:
non-root `appuser`, a `HEALTHCHECK`, and they launch under the ADOT `opentelemetry-instrument`
wrapper so custom + auto-instrumented OTEL export to CloudWatch (BYO containers must do this
themselves).

## Gateway topology (native Terraform)

The gateway is provisioned as **native Terraform** resources (`terraform/gateway.tf`,
hashicorp/aws ≥ v6.66) — not the AgentCore CLI:

- A **`protocol_type = "MCP"`** gateway with an **`mcp_server` target** whose endpoint is the
  mcp-runtime's `/mcp` invocations URL. (MCP is the only valid gateway protocol; the target brokers
  to the mcp-runtime as an upstream MCP server.)
- The mcp-runtime is **stateless** on **port 8000** at `/mcp` (the platform health-checks 8000).
  Running stateful or on another port fails the health check.
- Inbound is **CUSTOM_JWT** (the builder's IdP token, PKCE for the IDE path). Outbound to the
  runtime is **OAuth M2M** (a Cognito `client_credentials` grant + the `gac-engine/invoke` scope),
  wired via an AgentCore Identity OAuth2 credential provider.
- The gateway target is named `gac`, so its tools are namespaced **`gac___ask_companion`** and
  **`gac___companion_kb`**. `GAC_GATEWAY_URL` is the gateway's `/mcp` URL.
- An opt-in **identity interceptor** Lambda (ADR-0001) injects the verified builder subject
  (`X-Gac-Actor-Sub`) into the forwarded request so per-builder memory attributes to a real actor.

## Files

```
agentcore/
  agentcore.json.example          # optional AgentCore CLI project config template (the deployable path is terraform/)
  app/orchestrator/
    runtime_entrypoint.py         # thin /invocations adapter over Orchestrator.handle (gated by construction)
    Dockerfile                    # hardened: non-root + healthcheck + ADOT
    requirements.txt              # patched floors (bedrock-agentcore>=1.18.1, PyJWT>=2.13.0)
  app/mcp_runtime/
    runtime_mcp_entrypoint.py     # stateless MCP /mcp face; same governed tools (ask_companion, companion_kb).
                                  #   Imports MCPServer (mcp 2.x) with a FastMCP (mcp 1.x) fallback.
    Dockerfile                    # hardened: non-root + healthcheck + ADOT
    requirements.txt              # + mcp>=1.28.0,<2, uvicorn
  interceptor/
    identity_interceptor.py       # gateway REQUEST interceptor — injects X-Gac-Actor-Sub (ADR-0001)
  terraform/                      # the deployable IaC stack (see terraform/README.md)
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

## Runtime env (injected by Terraform, never baked in the image)

The runtime env contract is set by the Terraform stack (`terraform/locals.tf`) and is scoped to
what the engine actually reads (see `agents/kb_overlay.py`). The authoritative list lives in
[`terraform/README.md`](terraform/README.md) → "the runtime env contract"; the essentials:

| Var | Purpose |
|---|---|
| `GAC_KB_OVERLAY=s3` + `GAC_KB_BUCKET` + `GAC_KB_OVERLAY_KEY` | Read the curated Tier-B overlay object from S3 |
| `GAC_AWS_REGION` | Region for the S3/Bedrock clients |
| `GAC_DATA_PROTECTION_MODE` / `GAC_PII_BACKEND` (+ `GAC_GUARDRAIL_*`, `GAC_PROPRIETARY_TERMS`, `GAC_PII_REGION`) | The data-protection guard (Tenet 6) |
| `GAC_ENGINE_ROOT` | Locate the engine on `sys.path` inside the container |

The shared runtime execution role is granted S3 KB read+write on the KB prefix **by construction**
in `terraform/iam.tf` — no out-of-band `put-role-policy` step, no role churn to chase.
