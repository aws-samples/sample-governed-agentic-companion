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
  description = "S3 bucket backing the hybrid knowledge base (Tier-B overlay; the kb/ prefix is reserved for a fork's own knowledge tier)."
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
  description = "Cognito user pool id (empty unless auth_mode=cognito). On the consume path this is the shared pool id."
  value       = local.auth_is_cognito ? local.effective_pool_id : ""
}

output "cognito_frontdoor_client_id" {
  description = "Cognito front-door user client id (empty unless auth_mode=cognito). The client SECRET is not output — read it from the Cognito console/API."
  value       = local.auth_is_cognito ? one(aws_cognito_user_pool_client.frontdoor[*].id) : ""
}

output "cognito_m2m_client_id" {
  description = "Cognito M2M (client_credentials) client id for the gateway outbound hop (empty unless auth_mode=cognito). Empty in private mode, where the gateway uses SigV4."
  value       = local.auth_is_cognito ? try(aws_cognito_user_pool_client.m2m[0].id, "") : ""
}

output "cognito_oauth_frontdoor_client_id" {
  description = "PKCE PUBLIC client id for the Kiro/Claude IDE OAuth path (auto-refreshing). Empty unless auth_mode=cognito + enable_oauth_frontdoor_client. Use in the Kiro mcp.json oauth.clientId."
  value       = (local.auth_is_cognito && var.enable_oauth_frontdoor_client) ? one(aws_cognito_user_pool_client.oauth_frontdoor[*].id) : ""
}

output "cognito_oauth_scope" {
  description = "OAuth scope a front-door token carries for this companion (<engine_resource_server_identifier>/invoke). Empty unless auth_mode=cognito."
  value       = local.auth_is_cognito ? "${local.m2m_resource_server}/${local.m2m_scope_name}" : ""
}

output "cognito_hosted_ui_domain" {
  description = "Cognito hosted-UI domain base (authorize/token endpoints live here). Empty unless auth_mode=cognito."
  value       = local.auth_is_cognito ? "https://${local.cognito_domain}.auth.${local.region}.amazoncognito.com" : ""
}

output "gateway_url" {
  description = "AgentCore Gateway URL (empty unless deploy_gateway=true). Set as GAC_GATEWAY_URL in the front door. Private mode: the name resolves to the gateway endpoint's private IPs only inside VPCs/networks using its private DNS; requests that do not arrive through the endpoint are denied."
  value       = local.gateway_url
}

# ---- Network access ----------------------------------------------------------------------
# Every value below is empty or null in public mode. None is a secret.

locals {
  gateway_vpce_dns_names   = lookup(local.module_endpoint_dns_names, "bedrock-agentcore.gateway", [])
  dataplane_vpce_dns_names = lookup(local.module_endpoint_dns_names, "bedrock-agentcore", [])
  # Only works from inside an approved network. The token is exported separately and is never
  # interpolated here.
  private_access_test_command = local.network_private ? "curl -sS -o /dev/null -w '%%{http_code}\\n' -X POST \"$GAC_GATEWAY_URL\" -H \"Authorization: Bearer $GAC_GATEWAY_TOKEN\" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"0\"}}}'" : ""
}

output "network_access" {
  description = "Network exposure in effect: private (default; VPC endpoints only) or public (demo opt-in)."
  value       = var.network_access
}

output "vpc_id" {
  description = "VPC the runtimes and endpoints use (empty in public mode)."
  value       = local.network_private ? local.vpc_id_effective : ""
}

output "private_subnet_ids" {
  description = "Private subnets for the runtime ENIs and the interface endpoints (empty in public mode)."
  value       = local.network_private ? local.private_subnet_ids_effective : []
}

output "runtime_security_group_id" {
  description = "Security group on the runtime ENIs (HTTPS egress to the endpoints only). Empty in public mode."
  value       = try(aws_security_group.runtime[0].id, "")
}

output "endpoint_client_security_group_id" {
  description = "Security group on the AgentCore gateway/data-plane endpoints (runtime SG + private_client_cidrs). Empty in public mode or when those endpoints are reused."
  value       = lookup(local.vpce_sg_ids, "client", "")
}

output "endpoint_runtime_security_group_id" {
  description = "Security group on the runtime-only endpoints (runtime SG only). Empty in public mode."
  value       = lookup(local.vpce_sg_ids, "runtime", "")
}

output "vpc_endpoint_ids" {
  description = "Interface endpoint ids by service key (created plus reused). Empty in public mode."
  value       = local.network_private ? local.vpce_id : {}
}

output "gateway_vpce_id" {
  description = "AgentCore Gateway interface endpoint id (empty in public mode or without a gateway)."
  value       = try(local.vpce_id["bedrock-agentcore.gateway"], "")
}

output "gateway_vpce_dns_names" {
  description = "DNS names of the kit-managed gateway endpoint (regional and zonal). Empty in public mode."
  value       = local.gateway_vpce_dns_names
}

output "agentcore_dataplane_vpce_id" {
  description = "AgentCore data-plane interface endpoint id (empty in public mode)."
  value       = try(local.vpce_id["bedrock-agentcore"], "")
}

output "agentcore_dataplane_vpce_dns_names" {
  description = "DNS names of the kit-managed data-plane endpoint (for GAC_AGENTCORE_ENDPOINT on networks without private DNS). Empty in public mode."
  value       = local.dataplane_vpce_dns_names
}

output "s3_gateway_endpoint_id" {
  description = "S3 gateway endpoint id (created or reused). Null in public mode."
  value       = local.network_private ? local.s3_gateway_vpce_id : null
}

output "orchestrator_require_service_s3_endpoint" {
  description = "Computed by AgentCore for the orchestrator's VPC config; expected false (the customer S3 gateway endpoint is used). Null in public mode."
  value       = try(aws_bedrockagentcore_agent_runtime.orchestrator.network_configuration[0].network_mode_config[0].require_service_s3_endpoint, null)
}

output "mcp_runtime_require_service_s3_endpoint" {
  description = "Computed by AgentCore for the MCP runtime's VPC config; expected false. Null in public mode."
  value       = try(aws_bedrockagentcore_agent_runtime.mcp_runtime.network_configuration[0].network_mode_config[0].require_service_s3_endpoint, null)
}

output "kb_kms_key_arn" {
  description = "ARN of the KB bucket's KMS key. A curator's identity policy needs kms:Decrypt, kms:Encrypt, kms:GenerateDataKey* and kms:DescribeKey on it (docs/DEPLOY.md). Not a secret."
  value       = aws_kms_key.kb.arn
}

output "resolver_inbound_ip_addresses" {
  description = "IP addresses of the Route 53 Resolver inbound endpoint (targets for on-premises conditional forwarders of bedrock-agentcore.<region>.amazonaws.com). Empty unless enable_resolver_inbound_endpoint=true."
  value       = local.network_private ? module.private_network[0].resolver_inbound_ip_addresses : []
}

output "private_access_test_command" {
  description = "Probe for docs/DEPLOY.md post-deploy tests 2 and 3: export GAC_GATEWAY_URL and GAC_GATEWAY_TOKEN, then run it. Expect 200 inside an approved network and 403 outside. Empty in public mode."
  value       = local.private_access_test_command
}

# ---- Private access probe (TEST STAND-IN; empty when disabled) -----------------------------

output "private_access_probe_function_name" {
  description = "Name of the private access probe Lambda function (empty unless enable_private_access_probe=true). A test stand-in for a DX/VPN workstation; disable it after testing."
  value       = local.probe_enabled ? aws_lambda_function.private_access_probe[0].function_name : ""
}

output "private_access_probe_invoke_command" {
  description = "Command that invokes the probe. Prerequisites: AWS CLI v2, jq, and GAC_GATEWAY_TOKEN exported in the current shell (a front-door access token). The token is read by jq from the environment, never interpolated here. Empty unless enable_private_access_probe=true."
  value       = local.private_access_probe_invoke_command
}

output "frontdoor_config_dir" {
  description = "Directory containing generated front-door config (empty unless generate_frontdoor_config=true)."
  value       = local.gen_enabled ? local.gen_dir : ""
}
