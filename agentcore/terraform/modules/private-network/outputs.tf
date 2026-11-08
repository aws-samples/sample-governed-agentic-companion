# Outputs map 1:1 onto the companion consumer inputs (vpc_id, private_subnet_ids,
# private_route_table_ids, existing_s3_gateway_endpoint_id, existing_interface_endpoint_ids,
# existing_endpoint_security_group_ids). None is a secret.

output "vpc_id" {
  description = "VPC id (created or supplied)."
  value       = local.vpc_id_effective
}

output "vpc_cidr" {
  description = "CIDR of the created VPC; empty when an existing VPC is used."
  value       = var.create_vpc ? var.vpc_cidr : ""
}

output "private_subnet_ids" {
  description = "Private subnet ids (created or supplied)."
  value       = local.subnet_ids_effective
}

output "private_route_table_ids" {
  description = "Route table ids (created or supplied)."
  value       = local.route_table_ids_effective
}

output "az_ids" {
  description = "AZ IDs of the created subnets (empty list for an existing VPC unless az_ids was set)."
  value       = var.create_vpc ? local.az_ids_effective : var.az_ids
}

output "supported_az_ids" {
  description = "AgentCore Runtime supported AZ IDs in this Region; empty when the Region is not in the module's list."
  value       = local.supported_az_ids
}

output "interface_endpoint_ids" {
  description = "Interface endpoint ids by service key (created by this module)."
  value       = { for k, e in aws_vpc_endpoint.interface : k => e.id }
}

output "interface_endpoint_dns_names" {
  description = "Interface endpoint DNS names (regional and zonal) by service key."
  value       = { for k, e in aws_vpc_endpoint.interface : k => e.dns_entry[*].dns_name }
}

output "s3_gateway_endpoint_id" {
  description = "S3 gateway endpoint id; empty when not created."
  value       = join("", aws_vpc_endpoint.s3[*].id)
}

output "s3_prefix_list_id" {
  description = "S3 prefix list id of the created gateway endpoint; empty when not created."
  value       = join("", aws_vpc_endpoint.s3[*].prefix_list_id)
}

output "endpoint_security_group_ids" {
  description = "Endpoint security group ids by role: client (gateway/data-plane endpoints) and runtime (runtime-only endpoints). Keys are known at plan."
  value       = local.sg_ids
}

output "endpoint_security_group_id_list" {
  description = "The endpoint security group ids as a list (client and runtime)."
  value       = values(local.sg_ids)
}

output "resolver_inbound_endpoint_id" {
  description = "Route 53 Resolver inbound endpoint id; empty when disabled."
  value       = join("", aws_route53_resolver_endpoint.inbound[*].id)
}

output "resolver_inbound_ip_addresses" {
  description = "IP addresses of the Resolver inbound endpoint (targets for on-premises conditional forwarders); empty when disabled."
  value       = flatten([for e in aws_route53_resolver_endpoint.inbound : [for a in e.ip_address : a.ip]])
}

output "resolver_client_cidrs" {
  description = "CIDRs allowed to query the Resolver inbound endpoint (TCP/UDP 53); empty when disabled."
  value       = var.enable_resolver_inbound_endpoint ? local.resolver_cidrs : []
}
