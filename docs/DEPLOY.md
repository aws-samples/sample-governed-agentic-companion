# Deploy to Amazon Bedrock AgentCore (human-run — Tenet 1)

> **⚠️ Sample code — not for production as-is.** This deployment guide is provided for
> demonstration and educational purposes. The IAM grants, OAuth/OIDC and Cognito setup, and
> credential provisioning shown here are illustrative and **not intended for production use
> without additional security review, testing, and hardening** for your account and threat model.

The companion runs locally on the deterministic tier with no AWS. This is how you put it on
Amazon Bedrock AgentCore so builders reach it through the AgentCore Gateway. **A human performs
every AWS/IdP mutation** — no agent deploys, provisions, or invokes against AWS.

> **Reference code lives in [`../agentcore/`](../agentcore/).** Two hardened, thin runtime
> faces wrap the same governed engine: `app/orchestrator/` (the `/invocations` face) and
> `app/mcp_runtime/` (the stateless `/mcp` face the Gateway targets), plus
> `agentcore.json.example` (the CLI project config for both runtimes + the gateway shape) and
> `agentcore/README.md`. This page is the human-run sequence; that directory is what you build
> and deploy.

## Prerequisites

- An AWS account with **Amazon Bedrock** model access (for Tier-3; Tier-1 needs none).
- **Amazon Bedrock AgentCore** access — Runtime, Gateway, Identity, Observability.
- An **OIDC IdP** (Amazon Cognito reference) for the builder token + the gateway M2M credential.
- A container build toolchain + the AgentCore CLI.
- An **S3 bucket** for the evolving KB + the Tier-B overlay (the bucket name embeds the account
  id → injected as a runtime env var, never baked into the image).

## Identity & OAuth (two distinct flows — don't conflate them)

1. **Inbound (builder JWT).** Create a Cognito **user pool** (the OIDC issuer) + an **app client**
   (its id = the JWT audience). The builder authenticates (MFA) and the IDE presents
   `Authorization: Bearer <JWT>`. The gateway's inbound authorizer validates it.
   - Token nuance: an authorizer that checks `client_id` validates the **access token**; an `aud`
     check reads the **id token**. Decode a sample and match the token type to your authorizer.
2. **Outbound (gateway M2M).** Create a second app client for the **`client_credentials`** flow,
   a **resource server** with a scope (e.g. `companion-gateway/invoke`), and grant the client that
   scope. This client id + secret are the gateway's outbound credential. **Rotate the secret if it
   is ever exposed;** store it only in the platform credential provider.

## Verified gateway topology

- A **`protocol-type None`** gateway with an **`http-runtime` target** (ARN-resolved) — NOT an
  aggregated `protocol-type MCP` gateway (that rejects a runtime target).
- The runtime exposes a **stateless MCP** face (`/mcp`) on **port 8000** (the platform
  health-checks that port). Binding elsewhere or running stateful fails the health check.
- Inbound **CUSTOM_JWT**; outbound **OAuth M2M**. `GAC_GATEWAY_URL` is the **full path-based
  invocations URL** (`…/companion-engine/invocations`), not just the host.

## Sequence

```text
1. Build + deploy the runtime container (gate + Tier-A KB baked in; the gate enforces by construction).
2. Provision identity (inbound JWT app client; outbound M2M client + resource server + scope).
3. Provision the S3 KB bucket; set the runtime env (evolving-KB store + Tier-B overlay prefix);
   grant the runtime execution role s3:GetObject on the KB prefix.
   NOTE: each runtime gets its OWN execution role — grant the read to the RIGHT role, and
   re-apply after any role-churning redeploy (an idempotent script is the durable fix until the
   grant is in IaC).
4. Stand up the gateway: protocol-None + http-runtime target -> the MCP runtime; inbound
   CUSTOM_JWT; outbound M2M. Record the full invocations URL.
5. Point the builder IDE at the gateway (or the local bridge) with GAC_GATEWAY_URL + the token.
6. Verify: from the IDE, ask a question that hits a known KB marker; confirm a GATED answer with
   the governance-outcome footer.
```

## Verification checklist

- [ ] `./run.sh status` reports the governance integrity check **passing**.
- [ ] A deterministic question answers on Tier-1 with **no** LLM call.
- [ ] A boundary-crossing prompt (fake "I deployed it", or a secret) is **blocked** and the block
  notice **withholds** the original text.
- [ ] The end-to-end IDE → gateway → runtime → KB path returns a **gated** answer with the footer.
