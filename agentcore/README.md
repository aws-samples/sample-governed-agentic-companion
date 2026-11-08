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
a digest-pinned Chainguard Python base with no shell in the runtime stage, the non-root uid
65532, a `HEALTHCHECK`, and they launch under the ADOT `opentelemetry-instrument`
wrapper so custom + auto-instrumented OTEL export to CloudWatch (BYO containers must do this
themselves).

## Gateway topology (native Terraform)

The gateway is provisioned as **native Terraform** resources (`terraform/gateway.tf`,
hashicorp/aws ≥ v6.66) — not the AgentCore CLI — when `deploy_gateway = true` (default `false`).
Without a gateway, the MCP runtime's private-mode resource policy allows calls only through the
data-plane endpoint (like the orchestrator); with a gateway it allows only the gateway execution
role. The sandbox-validated behaviour is recorded in
[`../docs/DEPLOY.md`](../docs/DEPLOY.md#sandbox-validation-record-2026-10-31):

- A **`protocol_type = "MCP"`** gateway with an **`mcp_server` target** whose endpoint is the
  mcp-runtime's `/mcp` invocations URL. Setting `protocol_type = "MCP"` puts the gateway
  in aggregation mode with MCP targets only; the target brokers to the mcp-runtime as an upstream
  MCP server. AgentCore Gateway also supports HTTP targets, including an AgentCore Runtime target
  that forwards requests without aggregation; this kit uses the MCP server target for the tool
  catalog.
- The mcp-runtime serves MCP at `/mcp` on `0.0.0.0:8000`, the host, port, and path the AgentCore
  MCP protocol contract requires. Binding another host, port, or path is the common failure.
- It runs **stateless** (`stateless_http=True`) by design: the AWS-recommended default, and
  sufficient because the companion's tools are single request/response calls. AgentCore also
  supports **stateful** MCP servers (`stateless_http=False`) for elicitation, sampling, and
  progress notifications. For MCP protocol version 2025-11-25 and earlier, elicitation and sampling
  require stateful mode; from 2026-07-28 they use multi round-trip requests (MRTR), which don't.
  Switch only if your fork adds tools that need these features.
- Inbound is **CUSTOM_JWT** (the builder's IdP token, PKCE for the IDE path).
- Outbound to the runtime depends on `network_access`:
  - **Private mode (default):** **SigV4** signed with the gateway execution role
    (`gateway_iam_role { service = "bedrock-agentcore" }`;
    [MCP server targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html),
    [outbound auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)).
    The MCP runtime has no JWT authorizer, and its resource policy allows only that role
    ([runtime-oauth.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).
    No Cognito M2M client is created, and no client secret lands in state.
  - **Public mode (demo opt-in):** **OAuth M2M** (a Cognito `client_credentials` grant + the
    `gac-engine/invoke` scope), wired via an AgentCore Identity OAuth2 credential provider.
- **Endpoints and traffic flow (private mode).** The IDE reaches the gateway through the
  `bedrock-agentcore.gateway` interface endpoint, and the stdio bridge reaches the orchestrator
  through the `bedrock-agentcore` data-plane endpoint, both with private DNS
  ([AgentCore VPC endpoints](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-interface-endpoints.html)).
  Resource policies deny requests without the approved `aws:SourceVpce`. Both runtimes run in
  `VPC` network mode and reach ECR, CloudWatch Logs, X-Ray and Bedrock through runtime-only
  interface endpoints, and S3 (ECR layers and the KB) through the S3 gateway endpoint; there is no
  NAT or internet route ([agentcore-vpc.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)).
  The gateway-to-runtime hop and the interceptor run on AWS-managed networks outside your VPC.
  See [`../docs/SECURITY.md`](../docs/SECURITY.md) "Network posture".
- The gateway target is named `gac`, so its tools are namespaced **`gac___ask_companion`** and
  **`gac___companion_kb`**. `GAC_GATEWAY_URL` is the gateway's `/mcp` URL.
- An opt-in **identity interceptor** Lambda (ADR-0001) injects the verified builder subject
  (`X-Gac-Actor-Sub`) into the forwarded request so per-builder memory attributes to a real actor.

## Adoption paths

Two ways to deploy, both human-run (Tenet 1):

- **Path A: shared platform first.** Apply [`terraform/platform/`](terraform/platform/README.md):
  the remote-state bucket, one shared Cognito pool with an entitlement group per companion, and
  optionally one shared private network. Each companion then consumes it with
  `manage_identity_pool = false`, its own `engine_resource_server_identifier`, and (for the shared
  network) `create_vpc = false` plus the platform's `companion_consumer_inputs` output.
- **Path B: standalone companion (default).** The companion owns its pool
  (`manage_identity_pool = true`) and either a sandbox VPC (`create_vpc = true`) or your own VPC.

The optional private access probe (`enable_private_access_probe`) is a test stand-in for a
workstation on Direct Connect or VPN; disable it after testing. Its opt-in diagram mode
(`{token, diagram}`) calls `gac___draw_diagram` through the gateway inside the VPC and returns the
artefact envelope; `./run.sh diagram-fetch --via-probe <function> "<request>"` saves the files
through the bridge's writer. In private mode this is the end-to-end diagram test when no
workstation network path exists (`../docs/DEPLOY.md`, 3.3.1 check 4). Full steps, including adopting an
existing platform state: [`../docs/DEPLOY.md`](../docs/DEPLOY.md) "Adoption paths".

## Files

```
agentcore/
  agentcore.json.example          # optional AgentCore CLI project config template (the deployable path is terraform/)
  app/orchestrator/
    runtime_entrypoint.py         # thin /invocations adapter over Orchestrator.handle (gated by construction)
    Dockerfile                    # hardened: non-root + healthcheck + ADOT
    requirements.txt              # patched floors (bedrock-agentcore>=1.18.1, PyJWT>=2.13.0) + resvg-py
  app/diagram-assets/
    requirements.txt              # build-stage pins (pip, defusedxml) for the icon catalog stage
  app/mcp_runtime/
    runtime_mcp_entrypoint.py     # stateless MCP /mcp face; governed tools ask_companion, companion_kb,
                                  #   draw_diagram (registered with structured_output=False).
                                  #   Imports MCPServer (mcp 2.x) with a FastMCP (mcp 1.x) fallback.
    Dockerfile                    # hardened: non-root + healthcheck + ADOT
    requirements.txt              # + mcp>=1.28.0,<2, uvicorn
  diagram-icons/
    README.md                     # stage exactly one AWS Architecture Icons zip here (gitignored)
  interceptor/
    identity_interceptor.py       # gateway REQUEST interceptor — injects X-Gac-Actor-Sub (ADR-0001)
  probe/
    private_access_probe.py       # opt-in private access probe (test stand-in for a DX/VPN workstation);
                                  #   diagram mode returns the gac.diagram/1 envelope (--via-probe)
  terraform/                      # the deployable IaC stack (see terraform/README.md)
    modules/                      # backend-bootstrap, shared-identity, private-network
    platform/                     # optional shared layer: state backend, shared identity, shared network
```

## Action surface (the diagram)

| Face | Call | Returns |
|---|---|---|
| `/invocations` | `{"prompt", "environment"}` (no `action`) or `"action": "ask"` | `{"response", "gated": true}`, unchanged |
| `/invocations` | `"action": "diagram"`, optional `"title"`, `"name"`, `"include_artifacts"` (JSON boolean) | `{"response", "gated": true, "diagram"}` when `include_artifacts` is `true`, else `{"response", "gated": true, "diagram_manifest"}`; neither key when the gate blocked or nothing rendered |
| `/invocations` | any other `action` | gated `Unknown action '<slug>'; supported: ask, diagram.` |
| `/mcp` | `draw_diagram(prompt, environment="dev", title="", name="", include_artifacts=False)` | a JSON string: `{"response", "diagram_manifest"}` by default (at most 16 KiB on the wire), `{"response", "diagram"}` with `include_artifacts=true` |
| gateway | `gac___draw_diagram` | as `/mcp` |
| probe (test stand-in) | `{token, diagram: "<1-500 chars>"}` | `{"ok", "mode": "diagram", "steps", "result": {"response", "diagram"}, "error"}`; the tool name is fixed in `GAC_PROBE_DIAGRAM_TOOL_NAME`; over the 5 MiB upstream cap or Lambda's 6 MB response limit, an error and no artefacts |

`diagram` is the `gac.diagram/1` payload: `.drawio` and `.svg` as UTF-8 text, the `.png` as
base64 and `<name>.report.json`, each with `bytes` and `sha256`, within per-artefact caps (drawio
2 MiB, SVG 1.5 MiB, PNG 1 MiB, report 256 KiB). The wire budget is 4 MiB measured on the
serialized result. It sits under the AgentCore quotas on the
[AgentCore quotas page](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html):
Runtime maximum payload size 100 MB (checked 2026-11-02) and the gateway's tool-call payload size
of 6 MB (as documented there on 2026-11-01; quotas change, so confirm the current value). Over
budget the PNG is re-rendered at 1x, then omitted (`png_over_limit`), then no artefacts are
returned, and the text says which files were not delivered. `diagram_manifest` carries names,
sizes and 12-hex short hashes only. Only the local bridge requests the artefacts and writes them
(`../docs/FRONT-DOORS.md`).

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
| `GAC_DIAGRAM_ENABLED=false` | Only when Terraform `diagram_enabled = false`: the diagram specialist stays registered and declines (disable-only) |
| `GAC_DIAGRAM_ASSETS_DIR` | Set in the Dockerfiles (`/app/engine/diagram_assets`): the baked icon catalog; selects image mode |

The shared runtime execution role is granted S3 KB read+write on the KB prefix **by construction**
in `terraform/iam.tf` — no out-of-band `put-role-policy` step, no role churn to chase.
