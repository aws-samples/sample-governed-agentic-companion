# Optional ONE shared private network for several companions (enable_private_network, off by
# default). Each companion consumes it with network_access = "private", create_vpc = false and
# the companion_consumer_inputs output (vpc_id, private_subnet_ids, private_route_table_ids,
# existing_s3_gateway_endpoint_id, existing_interface_endpoint_ids,
# existing_endpoint_security_group_ids, existing_client_endpoint_security_group_ids,
# manage_existing_endpoint_ingress = true).
#
# A shared network is one network boundary for several companions: aws:SourceVpce no longer
# separates them. Separation rests on each companion's own JWT/SigV4 authorizers, the G-none-shared
# guard, the Memory DenyOtherPrincipalsOnSharedEndpoint statement and per-companion KMS keys
# (docs/SECURITY.md SD-27). The endpoint policies here are account-scoped, not per companion,
# because they cannot name one companion's resources (network_policies.tf).

locals {
  network_endpoint_services = toset(concat(
    ["ecr.api", "ecr.dkr", "logs", "bedrock-agentcore"],
    var.enable_gateway_endpoint ? ["bedrock-agentcore.gateway"] : [],
    var.enable_bedrock_runtime_endpoint ? ["bedrock-runtime"] : [],
    var.enable_xray_endpoint ? ["xray"] : [],
    var.enable_comprehend_endpoint ? ["comprehend"] : [],
    var.extra_interface_endpoint_services,
  ))

  network_client_endpoint_services = toset(concat(
    ["bedrock-agentcore"],
    var.enable_gateway_endpoint ? ["bedrock-agentcore.gateway"] : [],
    var.bedrock_runtime_client_access && var.enable_bedrock_runtime_endpoint ? ["bedrock-runtime"] : [],
  ))

  # The keys a companion accepts in existing_interface_endpoint_ids (its validation set). Extra
  # services are not passed on: the companion validation would reject them.
  consumer_endpoint_keys = ["bedrock-agentcore", "bedrock-agentcore.gateway", "bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "xray", "comprehend"]
}

module "network" {
  source = "../modules/private-network"
  count  = var.enable_private_network ? 1 : 0

  name_prefix                      = var.network_name_prefix
  create_vpc                       = true
  vpc_cidr                         = var.network_vpc_cidr
  az_ids                           = var.network_az_ids
  interface_endpoint_services      = local.network_endpoint_services
  client_endpoint_services         = local.network_client_endpoint_services
  endpoint_policies                = local.network_endpoint_policies
  create_s3_gateway_endpoint       = true
  s3_gateway_endpoint_policy       = one(data.aws_iam_policy_document.network_s3_gateway[*].json)
  client_cidrs                     = var.private_client_cidrs
  enable_resolver_inbound_endpoint = var.enable_resolver_inbound_endpoint
  resolver_client_cidrs            = var.resolver_inbound_client_cidrs
  # Ordering token: the endpoints wait for the platform guards.
  guards_ready = one(terraform_data.platform_guards[*].id)
}
