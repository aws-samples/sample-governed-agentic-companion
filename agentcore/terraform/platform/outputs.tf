# Outputs consumed by each companion stack. No output is a secret.
#
# Wiring: copy the values into a companion's gitignored terraform.tfvars (documented path). The
# companion root stays standalone (`init -backend=false` works) and takes no hard dependency on a
# platform state key. A fork can read them with terraform_remote_state instead (README.md).

# ---- Original names (kept for compatibility with an adopted platform state) ---------------

output "state_bucket" {
  description = "Remote-state S3 bucket name. Use it as `bucket` in each companion's backend block."
  value       = module.backend.state_bucket
}

output "lock_table" {
  description = "DynamoDB lock table name (empty when disabled; the default uses S3 native locking)."
  value       = module.backend.lock_table
}

output "shared_user_pool_id" {
  description = "Shared Cognito user pool id (same value as existing_user_pool_id)."
  value       = module.identity.user_pool_id
}

output "shared_oidc_issuer" {
  description = "Shared pool OIDC issuer (same value as existing_oidc_issuer)."
  value       = module.identity.oidc_issuer
}

output "shared_oidc_discovery_url" {
  description = "Shared pool OIDC discovery URL."
  value       = module.identity.oidc_discovery_url
}

output "shared_user_pool_domain" {
  description = "Shared pool hosted-UI/OAuth domain prefix."
  value       = module.identity.user_pool_domain
}

output "app_groups" {
  description = "Map of companion key -> entitlement group name."
  value       = module.identity.app_groups
}

# ---- Outputs named after the companion inputs they feed (1:1) ------------------------------

output "existing_user_pool_id" {
  description = "Companion input existing_user_pool_id (set manage_identity_pool = false)."
  value       = module.identity.user_pool_id
}

output "existing_oidc_issuer" {
  description = "Companion input existing_oidc_issuer."
  value       = module.identity.oidc_issuer
}

output "state_backend_example" {
  description = "Backend block template for a companion's gitignored backend.tf. Replace <name> in the key with the companion's name; one key per stack."
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${module.backend.state_bucket}"
        key          = "companions/<name>/terraform.tfstate"
        region       = "${var.aws_region}"
        use_lockfile = true
        encrypt      = true
      }
    }
  EOT
}

# ---- Shared private network (empty when enable_private_network = false) -------------------

locals {
  net_on = var.enable_private_network
}

output "vpc_id" {
  description = "Companion input vpc_id (shared VPC). Empty when the network is off."
  value       = local.net_on ? module.network[0].vpc_id : ""
}

output "private_subnet_ids" {
  description = "Companion input private_subnet_ids."
  value       = local.net_on ? module.network[0].private_subnet_ids : []
}

output "private_route_table_ids" {
  description = "Companion input private_route_table_ids."
  value       = local.net_on ? module.network[0].private_route_table_ids : []
}

output "existing_s3_gateway_endpoint_id" {
  description = "Companion input existing_s3_gateway_endpoint_id."
  value       = local.net_on ? module.network[0].s3_gateway_endpoint_id : ""
}

output "existing_interface_endpoint_ids" {
  description = "Companion input existing_interface_endpoint_ids: pass the WHOLE map, or the companion tries to create a second private-DNS endpoint for a missing key and the apply fails. Extra services are excluded (the companion validation would reject them)."
  value       = local.net_on ? { for k, v in module.network[0].interface_endpoint_ids : k => v if contains(local.consumer_endpoint_keys, k) } : {}
}

output "existing_endpoint_security_group_ids" {
  description = "Companion input existing_endpoint_security_group_ids (client SG and runtime-only SG)."
  value       = local.net_on ? module.network[0].endpoint_security_group_id_list : []
}

output "existing_client_endpoint_security_group_ids" {
  description = "Companion input existing_client_endpoint_security_group_ids: the client SG only (gateway and data-plane endpoints). A subset of existing_endpoint_security_group_ids; used by the private access probe so it never gets a rule on the runtime-only SG."
  value       = local.net_on ? [module.network[0].endpoint_security_group_ids["client"]] : []
}

output "private_client_cidrs" {
  description = "Companion input private_client_cidrs (the platform's approved client networks)."
  value       = local.net_on ? var.private_client_cidrs : []
}

output "resolver_inbound_ip_addresses" {
  description = "IP addresses of the shared Route 53 Resolver inbound endpoint (targets for on-premises conditional forwarders). Empty when off."
  value       = local.net_on ? module.network[0].resolver_inbound_ip_addresses : []
}

output "companion_consumer_inputs" {
  description = "Every value a consuming companion sets in its gitignored terraform.tfvars (`terraform output -json companion_consumer_inputs`). Network values are empty when enable_private_network = false; then set only the identity values and keep the companion's own network."
  value = {
    manage_identity_pool                        = false
    existing_user_pool_id                       = module.identity.user_pool_id
    existing_oidc_issuer                        = module.identity.oidc_issuer
    network_access                              = "private"
    create_vpc                                  = false
    manage_existing_endpoint_ingress            = true
    vpc_id                                      = local.net_on ? module.network[0].vpc_id : ""
    private_subnet_ids                          = local.net_on ? module.network[0].private_subnet_ids : []
    private_route_table_ids                     = local.net_on ? module.network[0].private_route_table_ids : []
    existing_s3_gateway_endpoint_id             = local.net_on ? module.network[0].s3_gateway_endpoint_id : ""
    existing_interface_endpoint_ids             = local.net_on ? { for k, v in module.network[0].interface_endpoint_ids : k => v if contains(local.consumer_endpoint_keys, k) } : {}
    existing_endpoint_security_group_ids        = local.net_on ? module.network[0].endpoint_security_group_id_list : []
    existing_client_endpoint_security_group_ids = local.net_on ? [module.network[0].endpoint_security_group_ids["client"]] : []
    private_client_cidrs                        = local.net_on ? var.private_client_cidrs : []
  }
}
