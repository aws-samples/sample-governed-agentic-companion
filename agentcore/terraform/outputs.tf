# Outputs — the values a human/front door needs after apply. No secrets are output.

output "aws_region" {
  description = "Region the solution was deployed into."
  value       = local.region
}

output "orchestrator_runtime_arn" {
  description = "ARN of the orchestrator runtime (HTTP /invocations) — used by the local stdio bridge (GAC_RUNTIME_ARN)."
  value       = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
}

output "mcp_runtime_arn" {
  description = "ARN of the MCP runtime (stateless /mcp) — the gateway's mcp_server target."
  value       = aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn
}

output "orchestrator_image_uri" {
  description = "ECR image URI referenced by the orchestrator runtime."
  value       = local.orchestrator_image_uri
}

output "mcp_runtime_image_uri" {
  description = "ECR image URI referenced by the MCP runtime."
  value       = local.mcp_runtime_image_uri
}

output "kb_bucket_name" {
  description = "S3 bucket backing the hybrid knowledge base (self-learning store + Tier-B overlay)."
  value       = aws_s3_bucket.kb.bucket
}

output "runtime_execution_role_arn" {
  description = "Shared execution role for both runtimes (already granted S3 KB read/write + AgentCore Memory data-plane when enabled — no out-of-band grant needed)."
  value       = aws_iam_role.runtime_exec.arn
}

output "memory_id" {
  description = "AgentCore Memory resource id backing session Events + cross-builder task summaries (empty when enable_memory=false). Wire a fork's engine to this via GAC_MEMORY_ID."
  value       = local.memory_enabled ? aws_bedrockagentcore_memory.this[0].id : ""
}

output "memory_sharing_mode" {
  description = "Cross-builder task-memory mode in effect: 'shared' (team-wide recall) or 'isolated' (strict per-builder). Only meaningful when enable_memory=true."
  value       = local.memory_enabled ? var.memory_sharing_mode : "n/a (memory disabled)"
}

output "data_protection_mode" {
  description = "Data-protection policy enforced at every KB/memory write: balanced | strict | redact."
  value       = var.data_protection_mode
}

output "pii_backend" {
  description = "Managed PII backend in effect (none = deterministic detectors only; comprehend | guardrails add fuller PII detection)."
  value       = var.pii_backend
}

output "guardrail_id" {
  description = "Effective Bedrock Guardrail id in use (Terraform-created when create_bedrock_guardrail=true, else the supplied guardrail_id). Empty when no guardrail."
  value       = local.effective_guardrail_id
}

output "guardrail_version" {
  description = "Effective Bedrock Guardrail version in use. Empty when no guardrail."
  value       = local.effective_guardrail_version
}

output "gateway_m2m_secret_arn" {
  description = "BYO override only: the Secrets Manager ARN a fork supplied via gateway_m2m_secret_arn for a non-gateway path. Empty by default — the gateway's outbound hop uses the native AgentCore Identity OAuth2 credential provider (no hand-managed secret). The secret VALUE is never output."
  value       = local.effective_m2m_secret_arn
}

output "auth_mode" {
  description = "Inbound identity mode in effect (cognito | entraid | none)."
  value       = var.auth_mode
}

output "inbound_jwt_discovery_url" {
  description = "OIDC discovery URL the inbound JWT authorizer trusts (empty for auth_mode=none)."
  value       = local.jwt_discovery_url
}

output "cognito_user_pool_id" {
  description = "Cognito user pool id (empty unless auth_mode=cognito)."
  value       = local.auth_is_cognito ? one(aws_cognito_user_pool.this[*].id) : ""
}

output "cognito_frontdoor_client_id" {
  description = "Cognito front-door user client id (empty unless auth_mode=cognito). The client SECRET is not output — read it from the Cognito console/API."
  value       = local.auth_is_cognito ? one(aws_cognito_user_pool_client.frontdoor[*].id) : ""
}

output "cognito_m2m_client_id" {
  description = "Cognito M2M (client_credentials) client id for the gateway outbound hop (empty unless auth_mode=cognito)."
  value       = local.auth_is_cognito ? one(aws_cognito_user_pool_client.m2m[*].id) : ""
}

output "cognito_oauth_frontdoor_client_id" {
  description = "PKCE PUBLIC client id for the Kiro/Claude IDE OAuth path (auto-refreshing). Empty unless auth_mode=cognito + enable_oauth_frontdoor_client. Use in the Kiro mcp.json oauth.clientId."
  value       = (local.auth_is_cognito && var.enable_oauth_frontdoor_client) ? one(aws_cognito_user_pool_client.oauth_frontdoor[*].id) : ""
}

output "cognito_hosted_ui_domain" {
  description = "Cognito hosted-UI domain base (authorize/token endpoints live here). Empty unless auth_mode=cognito."
  value       = local.auth_is_cognito ? "https://${local.cognito_domain}.auth.${local.region}.amazoncognito.com" : ""
}

output "gateway_url" {
  description = "AgentCore Gateway URL (empty unless deploy_gateway=true). Set as GAC_GATEWAY_URL in the front door."
  value       = local.gateway_url
}

output "frontdoor_config_dir" {
  description = "Directory containing generated front-door config (empty unless generate_frontdoor_config=true)."
  value       = local.gen_enabled ? local.gen_dir : ""
}
