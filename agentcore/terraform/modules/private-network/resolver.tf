# Optional Amazon Route 53 Resolver INBOUND endpoint (off by default).
# ---------------------------------------------------------------------------
# For builder workstations on AWS Direct Connect or Site-to-Site / Client VPN: an on-premises DNS
# server forwards bedrock-agentcore.<region>.amazonaws.com (which also covers
# <id>.gateway.bedrock-agentcore.<region>.amazonaws.com) to these endpoint IPs, so the
# workstation resolves the interface endpoints' private DNS names to their private IPs.
#
# When enabled, the endpoint answers ANY name the VPC resolver can resolve, for the approved
# client CIDRs only (docs/SECURITY.md SD-26). Facts from the AWS guide
# (https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries-values.html):
#   - inbound rules must allow DNS (TCP and UDP 53) from the forwarders; at least two IPs;
#   - the endpoint's security group cannot be changed after creation, so a new SG replaces the
#     endpoint and its IPs (update the on-premises forwarders);
#   - tracked connections can cap the endpoint at about 1500 queries per second per IP. The kit
#     keeps the tracked shape (untracked needs all-traffic rules both ways); add IPs to scale.
# Do53 only: DoH is not enabled, so 443 is not opened on this endpoint.
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370

locals {
  resolver_cidrs = length(var.resolver_client_cidrs) > 0 ? var.resolver_client_cidrs : var.client_cidrs
}

resource "aws_security_group" "resolver_inbound" {
  count       = var.enable_resolver_inbound_endpoint ? 1 : 0
  name        = "${var.name_prefix}-resolver-inbound"
  description = "Route 53 Resolver inbound endpoint: DNS (TCP/UDP 53) from approved client CIDRs only"
  vpc_id      = local.vpc_id_effective

  tags = {
    Name = "${var.name_prefix}-resolver-inbound"
  }
}

# DNS from the approved client CIDRs only (validated: never /0, prefix /8 or longer).
resource "aws_vpc_security_group_ingress_rule" "resolver_dns" {
  for_each          = var.enable_resolver_inbound_endpoint ? { for p in setproduct(local.resolver_cidrs, ["tcp", "udp"]) : "${p[0]}|${p[1]}" => p } : {}
  security_group_id = aws_security_group.resolver_inbound[0].id
  cidr_ipv4         = each.value[0]
  ip_protocol       = each.value[1]
  from_port         = 53
  to_port           = 53
  description       = "DNS from approved client network"
}

resource "aws_route53_resolver_endpoint" "inbound" {
  count                  = var.enable_resolver_inbound_endpoint ? 1 : 0
  name                   = "${var.name_prefix}-inbound"
  direction              = "INBOUND"
  resolver_endpoint_type = "IPV4"
  protocols              = ["Do53"]
  security_group_ids     = [aws_security_group.resolver_inbound[0].id]

  dynamic "ip_address" {
    for_each = local.subnet_ids_effective
    content {
      subnet_id = ip_address.value
    }
  }

  lifecycle {
    precondition {
      condition     = length(local.resolver_cidrs) > 0
      error_message = "The Route 53 Resolver inbound endpoint needs resolver_client_cidrs (or client_cidrs): the networks whose DNS forwarders may query it."
    }
    precondition {
      condition     = length(local.subnet_ids_effective) >= 2
      error_message = "The Route 53 Resolver inbound endpoint needs at least two subnets (one IP address in each)."
    }
  }
}
