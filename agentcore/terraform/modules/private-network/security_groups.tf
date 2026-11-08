# Endpoint security groups. Neither can carry an all-addresses (/0) CIDR: the only CIDR ingress
# iterates the validated var.client_cidrs. A companion adds ingress from its own runtime SG
# (aws_vpc_security_group_ingress_rule.vpce_from_runtime in the companion stack).
#
# The name and description strings are kept verbatim from the original companion network.tf: a
# description change forces security group replacement.
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# K10: KICS "Security Group Not Used" (query 4849211b) does not follow the role-keyed
# local.sg_ids map that attaches both groups to the interface endpoints (endpoints.tf).
# Both queries are disabled for this file by query id; every other KICS query still runs.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370,4849211b-ac39-479e-ae78-5694d506cb24

resource "aws_security_group" "vpce_client" {
  # checkov:skip=CKV2_AWS_5: false positive; attached to the interface endpoints through the role-keyed local.sg_ids map in endpoints.tf (security_group_ids), which the graph check does not follow.
  count       = length(setintersection(var.client_endpoint_services, var.interface_endpoint_services)) > 0 ? 1 : 0
  name        = "${var.name_prefix}-vpce-client"
  description = "AgentCore gateway/data-plane endpoints: HTTPS from the runtime SG and approved client CIDRs"
  vpc_id      = local.vpc_id_effective

  tags = {
    Name = "${var.name_prefix}-vpce-client"
  }
}

resource "aws_security_group" "vpce_runtime" {
  # checkov:skip=CKV2_AWS_5: false positive; attached to the interface endpoints through the role-keyed local.sg_ids map in endpoints.tf (security_group_ids), which the graph check does not follow.
  count       = length(setsubtract(var.interface_endpoint_services, var.client_endpoint_services)) > 0 ? 1 : 0
  name        = "${var.name_prefix}-vpce-runtime"
  description = "Runtime-only endpoints (ECR, Logs, X-Ray, Bedrock, Comprehend): HTTPS from the runtime SG only"
  vpc_id      = local.vpc_id_effective

  tags = {
    Name = "${var.name_prefix}-vpce-runtime"
  }
}

locals {
  # Keys are known at plan time (both counts derive from variables).
  sg_ids = merge(
    length(aws_security_group.vpce_client) > 0 ? { client = aws_security_group.vpce_client[0].id } : {},
    length(aws_security_group.vpce_runtime) > 0 ? { runtime = aws_security_group.vpce_runtime[0].id } : {},
  )
}

# The only CIDR ingress in the module: approved client networks to the CLIENT endpoints only.
# var.client_cidrs validation rejects any /0 CIDR (IPv4 or IPv6) and anything wider than /8.
resource "aws_vpc_security_group_ingress_rule" "vpce_client_from_cidrs" {
  for_each          = length(aws_security_group.vpce_client) > 0 ? toset(var.client_cidrs) : toset([])
  security_group_id = aws_security_group.vpce_client[0].id
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "IDE / developer network"
}
