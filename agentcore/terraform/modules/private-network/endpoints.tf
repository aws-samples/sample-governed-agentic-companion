# Interface endpoints (private DNS) and the S3 gateway endpoint.
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370

# Fails plan with "no matching VPC Endpoint Service" in a Region without the service.
data "aws_vpc_endpoint_service" "svc" {
  for_each     = var.interface_endpoint_services
  service_name = "com.amazonaws.${local.region}.${each.key}"
  service_type = "Interface"
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = var.interface_endpoint_services
  vpc_id              = local.vpc_id_effective
  service_name        = data.aws_vpc_endpoint_service.svc[each.key].service_name
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.subnet_ids_effective
  security_group_ids  = [contains(var.client_endpoint_services, each.key) ? local.sg_ids["client"] : local.sg_ids["runtime"]]
  # null = the AWS default (full access) policy; the caller passes the tightened ones.
  policy = lookup(var.endpoint_policies, each.key, null)

  tags = {
    Name = "${var.name_prefix}-${each.key}"
  }

  lifecycle {
    # Ordering token: the reference makes this resource wait for the caller's guard resource;
    # the condition itself is always true once known.
    precondition {
      condition     = var.guards_ready != null
      error_message = "guards_ready must not be null."
    }
  }
}

# S3 gateway endpoint: ECR image layers and the KB buckets (no additional charge).
resource "aws_vpc_endpoint" "s3" {
  count             = var.create_s3_gateway_endpoint ? 1 : 0
  vpc_id            = local.vpc_id_effective
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = local.route_table_ids_effective
  policy            = var.s3_gateway_endpoint_policy

  tags = {
    Name = "${var.name_prefix}-s3"
  }

  lifecycle {
    precondition {
      condition     = var.s3_gateway_endpoint_policy != ""
      error_message = "s3_gateway_endpoint_policy must be set when the S3 gateway endpoint is created (an empty policy would mean full access)."
    }
    # Ordering token (see aws_vpc_endpoint.interface).
    precondition {
      condition     = var.guards_ready != null
      error_message = "guards_ready must not be null."
    }
  }
}
