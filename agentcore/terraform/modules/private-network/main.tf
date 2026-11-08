# modules/private-network: a private network for AgentCore companions.
# ---------------------------------------------------------------------------
# Creates, depending on the inputs:
#   - optionally a VPC (create_vpc = true): 2 private subnets in AgentCore-supported AZs, one
#     route table with only the local route + the S3 gateway endpoint, the default SG stripped.
#     No internet gateway, no NAT gateway, no Elastic IP, no route other than local and S3.
#   - interface endpoints with private DNS (endpoints.tf) and an optional S3 gateway endpoint.
#   - the endpoint security groups (security_groups.tf): client (HTTPS from approved client
#     CIDRs) and runtime-only. Ingress from a companion's runtime SG is added by the companion.
#
# Used in two places:
#   1. inside a companion stack (agentcore/terraform/network.tf, module.private_network), for
#      both create_vpc = true and an existing VPC;
#   2. in the platform stack (agentcore/terraform/platform/network.tf, module.network) as ONE
#      shared private network that several companions consume through vpc_id / existing_*.
#
# Per-companion controls (resource policies, runtime SG, gateway, KB and ECR policies, IAM) stay
# in the companion stack.
#
# Sources: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html
#          https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-interface-endpoints.html
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# K5: KICS "VPC FlowLogs Disabled" (query f83121ea) matches only a flow log whose vpc_id is
# aws_vpc.<name>.id in the same file; the flow log is in flow_logs.tf (aws_flow_log.this,
# vpc_id = aws_vpc.this[0].id).
# K6: KICS "VPC Without Network Firewall" (query fd632aaf). The VPC has no internet gateway, no
# NAT gateway and no route other than local and the S3 gateway endpoint, so there is no
# internet-bound traffic for a Network Firewall to inspect; egress is to VPC endpoints only. A
# fork that adds internet egress must add network inspection (for example AWS Network Firewall)
# and remove fd632aaf from this list.
# These three queries are disabled for this file by query id; every other KICS query still runs.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370,f83121ea-03da-434f-9277-9cd247ab3047,fd632aaf-b8a1-424d-a4d1-0de22fd3247a

data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region

  # AgentCore Runtime supported AZ IDs per Region (agentcore-vpc.html "Supported Availability
  # Zones", read 2026-10-27).
  agentcore_supported_az_ids = {
    "us-east-1"      = ["use1-az1", "use1-az2", "use1-az4"]
    "us-east-2"      = ["use2-az1", "use2-az2", "use2-az3"]
    "us-west-1"      = ["usw1-az1", "usw1-az3"]
    "us-west-2"      = ["usw2-az1", "usw2-az2", "usw2-az3"]
    "ap-southeast-5" = ["apse5-az1", "apse5-az2", "apse5-az3"]
    "ap-south-1"     = ["aps1-az1", "aps1-az2", "aps1-az3"]
    "ap-south-2"     = ["aps2-az1", "aps2-az2", "aps2-az3"]
    "ap-northeast-2" = ["apne2-az1", "apne2-az2", "apne2-az3"]
    "ap-southeast-1" = ["apse1-az1", "apse1-az2", "apse1-az3"]
    "ap-southeast-2" = ["apse2-az1", "apse2-az2", "apse2-az3"]
    "ap-southeast-7" = ["apse7-az1", "apse7-az2", "apse7-az3"]
    "ap-northeast-1" = ["apne1-az1", "apne1-az2", "apne1-az4"]
    "ca-central-1"   = ["cac1-az1", "cac1-az2", "cac1-az4"]
    "eu-central-1"   = ["euc1-az1", "euc1-az2", "euc1-az3"]
    "eu-west-1"      = ["euw1-az1", "euw1-az2", "euw1-az3"]
    "eu-west-2"      = ["euw2-az1", "euw2-az2", "euw2-az3"]
    "eu-south-1"     = ["eus1-az1", "eus1-az2", "eus1-az3"]
    "eu-west-3"      = ["euw3-az1", "euw3-az2", "euw3-az3"]
    "eu-south-2"     = ["eus2-az1", "eus2-az2", "eus2-az3"]
    "eu-north-1"     = ["eun1-az1", "eun1-az2", "eun1-az3"]
    "sa-east-1"      = ["sae1-az1", "sae1-az2", "sae1-az3"]
    "us-gov-west-1"  = ["usgw1-az1", "usgw1-az2", "usgw1-az3"]
  }
  supported_az_ids = lookup(local.agentcore_supported_az_ids, local.region, [])

  # The caller's two AZ IDs, or the first two available supported AZ IDs. try() keeps slice()
  # from failing on < 2 entries; the aws_vpc.this precondition then fails with a clear message.
  az_ids_effective = length(var.az_ids) > 0 ? var.az_ids : try(
    slice(sort(tolist(setintersection(
      try(data.aws_availability_zones.available[0].zone_ids, []),
      local.supported_az_ids,
    ))), 0, 2),
    [],
  )

  vpc_id_effective          = var.create_vpc ? one(aws_vpc.this[*].id) : var.vpc_id
  subnet_ids_effective      = var.create_vpc ? aws_subnet.private[*].id : var.subnet_ids
  route_table_ids_effective = var.create_vpc ? aws_route_table.private[*].id : var.route_table_ids
}

# ---- VPC (create_vpc = true) ---------------------------------------------------------------

data "aws_availability_zones" "available" {
  count = var.create_vpc ? 1 : 0
  state = "available"

  # Allowlist the AgentCore-supported AZ IDs (checkov CKV_AWS_394), so a newly added AZ never
  # enters the set. Same result as before: az_ids_effective already intersects with this list.
  # For a Region not in the list the filter matches nothing and the aws_vpc.this precondition
  # fails with its usual message (or the caller passes az_ids).
  filter {
    name   = "zone-id"
    values = length(local.supported_az_ids) > 0 ? local.supported_az_ids : ["unsupported-region"]
  }
}

# K5 and K6 (KICS flow-log and Network Firewall queries): disabled by query id in the file header.
resource "aws_vpc" "this" {
  # VPC Flow Logs: flow_logs.tf (checkov CKV2_AWS_11).
  count                = var.create_vpc ? 1 : 0
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name_prefix}-private"
  }

  lifecycle {
    precondition {
      condition     = length(local.az_ids_effective) == 2
      error_message = "create_vpc=true needs exactly two AgentCore-supported AZ IDs: set the AZ IDs (private_subnet_az_ids / network_az_ids), or use a Region where two supported AZs are available."
    }
  }
}

# Strip every rule from the VPC's default security group (checkov CKV2_AWS_12).
resource "aws_default_security_group" "this" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = aws_vpc.this[0].id
}

resource "aws_subnet" "private" {
  count                   = var.create_vpc ? 2 : 0
  vpc_id                  = aws_vpc.this[0].id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone_id    = try(local.az_ids_effective[count.index], null)
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-private-${count.index}"
  }
}

# Only the implicit local route plus the S3 gateway endpoint route. No internet route.
resource "aws_route_table" "private" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = aws_vpc.this[0].id

  tags = {
    Name = "${var.name_prefix}-private"
  }
}

resource "aws_route_table_association" "private" {
  count          = var.create_vpc ? 2 : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}
