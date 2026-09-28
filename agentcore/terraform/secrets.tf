# Outbound gateway->runtime M2M credential — handled by AgentCore Identity, NOT a hand-managed
# Secrets Manager secret.
# ---------------------------------------------------------------------------
# The AgentCore Gateway reaches the mcp_runtime over an OUTBOUND OAuth client_credentials hop.
# On the cognito path that hop authenticates via the NATIVE AgentCore Identity OAuth2 credential
# provider (see gateway.tf → aws_bedrockagentcore_oauth2_credential_provider.gateway_m2m), which
# receives the Cognito client_id/secret directly and stores the credential in its own managed
# Token Vault. So the kit does NOT create a separate, hand-managed Secrets Manager copy of the
# credential by default — that would be a redundant long-lived secret (more sprawl + a rotation
# obligation) for no functional gain, and the gateway never reads it.
#
# BRING-YOUR-OWN: a fork that needs a readable Secrets Manager copy on a NON-gateway path (e.g. a
# direct-invoke client it builds) can point var.gateway_m2m_secret_arn at a secret it manages
# elsewhere (with its own rotation policy). For auth_mode=entraid, supply the Entra
# client-credentials secret ARN the same way.
#
# GOVERNANCE (Tenet 6): no credential is stored in the repo; the only credential store on the
# gateway path is the AgentCore Identity Token Vault, which is a managed, encrypted surface.

locals {
  # The gateway path does not consume a Secrets Manager ARN (it uses the OAuth2 credential
  # provider). This local is the BYO override only: a fork-supplied existing secret ARN, else "".
  effective_m2m_secret_arn = var.gateway_m2m_secret_arn
}
