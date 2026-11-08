# Outputs map onto each consuming companion's existing_* variables (existing_user_pool_id /
# existing_oidc_issuer in agentcore/terraform/variables.tf). Wire them by copying the value into
# the companion's gitignored tfvars, or via terraform_remote_state once the remote backend exists.
output "user_pool_id" {
  description = "Shared Cognito user pool id. Feeds each consuming companion's existing_user_pool_id."
  value       = aws_cognito_user_pool.shared.id
}

output "oidc_issuer" {
  description = "OIDC issuer URL. Feeds each consuming companion's existing_oidc_issuer."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.shared.id}"
}

output "oidc_discovery_url" {
  description = "OIDC discovery URL for a CUSTOM_JWT authorizer (issuer + /.well-known/openid-configuration)."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.shared.id}/.well-known/openid-configuration"
}

output "user_pool_domain" {
  description = "Shared Cognito hosted-UI/OAuth domain prefix."
  value       = aws_cognito_user_pool_domain.shared.domain
}

output "app_groups" {
  description = "Map of app key -> entitlement group name."
  value       = { for k, g in aws_cognito_user_group.app : k => g.name }
}

data "aws_region" "current" {}
