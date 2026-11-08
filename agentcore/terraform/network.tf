# Private network mode (network_access = "private", the DEFAULT).
# ---------------------------------------------------------------------------
# The network itself (optional sandbox VPC, interface endpoints with private DNS, the S3 gateway
# endpoint and the endpoint security groups) is built by modules/private-network, called below
# as module.private_network in private mode only. moved.tf keeps every resource created before
# 3.2.0 at its new address without replacement.
#
# What stays here, because it is per-companion (or a consumer-side check):
#   - existing-VPC lookups used by guards.tf (DNS attributes, subnet AZs, route tables);
#   - the runtime security group: no ingress; egress TCP 443 only to the endpoint SGs and the S3
#     prefix list;
#   - ingress on the endpoint SGs from THIS companion's runtime SG.
# Endpoint policies and resource policies live in resource_policies.tf.
#
# Interface endpoints the kit creates: ecr.api, ecr.dkr, logs, bedrock-agentcore (data plane),
# bedrock-agentcore.gateway (when deploy_gateway), bedrock-runtime, xray, comprehend (when
# pii_backend=comprehend), plus extra_interface_endpoint_services, minus every key reused through
# existing_interface_endpoint_ids. No security group can carry an all-addresses (/0) CIDR.
#
# Sources: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html
#          https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-interface-endpoints.html

module "private_network" {
  source = "./modules/private-network"
  count  = local.network_private ? 1 : 0

  name_prefix                 = var.name_prefix
  create_vpc                  = local.create_vpc
  vpc_cidr                    = var.vpc_cidr
  az_ids                      = var.private_subnet_az_ids
  vpc_id                      = var.vpc_id
  subnet_ids                  = var.private_subnet_ids
  route_table_ids             = var.private_route_table_ids
  interface_endpoint_services = local.managed_endpoint_keys
  client_endpoint_services    = toset(local.client_endpoint_keys)
  endpoint_policies           = local.endpoint_policies
  create_s3_gateway_endpoint  = !local.reuse_s3_endpoint
  s3_gateway_endpoint_policy  = try(data.aws_iam_policy_document.s3_gateway_endpoint[0].json, "")
  client_cidrs                = local.client_cidrs_effective
  # Optional Route 53 Resolver inbound endpoint for Direct Connect / VPN workstations.
  enable_resolver_inbound_endpoint = var.enable_resolver_inbound_endpoint
  resolver_client_cidrs            = var.resolver_inbound_client_cidrs
  # Ordering token: the module's endpoints wait for the network guards (no module depends_on,
  # which would defer every data source in the module to apply time).
  guards_ready = terraform_data.network_guards.id
}

locals {
  # Existing VPC: each subnet's route table (explicit association, else the VPC main table).
  subnet_route_table = { for s, d in data.aws_route_tables.subnet_explicit : s => (length(d.ids) > 0 ? d.ids[0] : try(one(data.aws_route_tables.vpc_main[0].ids), "")) }
  s3_route_tables    = local.reuse_s3_endpoint ? try(data.aws_vpc_endpoint.existing_s3[0].route_table_ids, []) : var.private_route_table_ids

  # Existing-VPC lookups run only when the inputs are present (guard 3 reports missing ones).
  existing_vpc_lookups = local.network_private && !local.create_vpc && var.vpc_id != ""
}

# ---- Existing VPC lookups (create_vpc = false) ---------------------------------------------

data "aws_vpc" "existing" {
  count = local.existing_vpc_lookups ? 1 : 0
  id    = var.vpc_id
}

data "aws_subnet" "existing" {
  for_each = local.existing_vpc_lookups ? toset(var.private_subnet_ids) : toset([])
  id       = each.value
}

data "aws_route_tables" "subnet_explicit" {
  for_each = local.existing_vpc_lookups ? toset(var.private_subnet_ids) : toset([])
  vpc_id   = var.vpc_id

  filter {
    name   = "association.subnet-id"
    values = [each.value]
  }
}

data "aws_route_tables" "vpc_main" {
  count  = local.existing_vpc_lookups ? 1 : 0
  vpc_id = var.vpc_id

  filter {
    name   = "association.main"
    values = ["true"]
  }
}

data "aws_vpc_endpoint" "existing_s3" {
  count = local.reuse_s3_endpoint ? 1 : 0
  id    = var.existing_s3_gateway_endpoint_id
}

data "aws_ec2_managed_prefix_list" "s3" {
  count = local.reuse_s3_endpoint ? 1 : 0
  name  = "com.amazonaws.${local.region}.s3"
}

# ---- Runtime security group and its rules (per companion) ----------------------------------

resource "aws_security_group" "runtime" {
  # checkov:skip=CKV2_AWS_5: false positive; attached to both AgentCore runtimes (aws_bedrockagentcore_agent_runtime network_configuration network_mode_config security_groups in runtimes.tf), a resource type the graph check does not model.
  count       = local.network_private ? 1 : 0
  name        = "${var.name_prefix}-runtime"
  description = "AgentCore runtime ENIs: HTTPS egress to VPC endpoints only"
  vpc_id      = local.vpc_id_effective

  tags = {
    Name = "${var.name_prefix}-runtime"
  }
}

locals {
  # Values read from module.private_network (one instance in private mode, none in public).
  # Each reads one named output behind a plan-known condition (a conditional reports errors only
  # for the selected branch). Not try(): it turns a partly unknown value (known keys, unknown
  # ids) into a wholly unknown one and breaks for_each. Not a splat or for-expression over the
  # module: that references every module output and makes network_guards a dependency cycle.
  # Module endpoint SGs by role (client / runtime); keys derive from variables.
  vpce_sg_ids               = local.network_private ? module.private_network[0].endpoint_security_group_ids : {}
  module_endpoint_ids       = local.network_private ? module.private_network[0].interface_endpoint_ids : {}
  module_endpoint_dns_names = local.network_private ? module.private_network[0].interface_endpoint_dns_names : {}
  # AgentCore-supported AZ IDs in this Region; empty in public mode or for a Region not in the
  # module's list, which skips the AZ-ID part of guard 7.
  agentcore_supported_az_ids = local.network_private ? module.private_network[0].supported_az_ids : []
  s3_prefix_list_id          = local.reuse_s3_endpoint ? try(data.aws_ec2_managed_prefix_list.s3[0].id, "") : (local.network_private ? module.private_network[0].s3_prefix_list_id : "")
}

resource "aws_vpc_security_group_egress_rule" "runtime_to_vpce" {
  for_each                     = local.vpce_sg_ids
  security_group_id            = aws_security_group.runtime[0].id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Runtime to VPC endpoints HTTPS (${each.key})"
}

resource "aws_vpc_security_group_egress_rule" "runtime_to_s3" {
  count             = local.network_private ? 1 : 0
  security_group_id = aws_security_group.runtime[0].id
  prefix_list_id    = local.s3_prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Runtime to S3 prefix list HTTPS (ECR layers, KB bucket)"
}

resource "aws_vpc_security_group_egress_rule" "runtime_to_existing_endpoints" {
  for_each                     = local.network_private ? toset(var.existing_endpoint_security_group_ids) : toset([])
  security_group_id            = aws_security_group.runtime[0].id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Runtime to reused VPC endpoint HTTPS"
}

resource "aws_vpc_security_group_ingress_rule" "vpce_from_runtime" {
  for_each                     = local.vpce_sg_ids
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.runtime[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Endpoint HTTPS from the runtime SG"
}

# Shared-network consumers (manage_existing_endpoint_ingress = true): admit THIS companion's
# runtime SG on each reused endpoint SG (for example the platform's shared endpoint SGs). Off by
# default, so operator-managed central endpoints are untouched.
resource "aws_vpc_security_group_ingress_rule" "existing_endpoint_from_runtime" {
  for_each                     = local.network_private && var.manage_existing_endpoint_ingress ? toset(var.existing_endpoint_security_group_ids) : toset([])
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.runtime[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Endpoint HTTPS from companion runtime SG (shared network)"
}
