# modules/private-network inputs. Every input is validated here so the platform caller gets the
# same protections as the companion stack. No provider block and no tags input: the caller's
# provider default_tags apply.

variable "name_prefix" {
  description = "Prefix for resource names and Name tags."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name_prefix))
    error_message = "name_prefix must be 2-31 chars, start with a letter, and use only lowercase letters, digits, and hyphens."
  }
}

variable "create_vpc" {
  description = "true: create a VPC with two private subnets and one route table (no internet gateway, no NAT). false: use vpc_id, subnet_ids and route_table_ids."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR of the created VPC (create_vpc = true). Two /20 private subnets are carved from it."
  type        = string
  default     = "10.80.0.0/16"

  validation {
    # IPv4 only (cidrnetmask rejects IPv6). Every parse sits inside can()/try() because HCL
    # does not short-circuit && / ||.
    condition     = can(cidrnetmask(var.vpc_cidr)) && can(tonumber(split("/", var.vpc_cidr)[1])) && try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 24, false)
    error_message = "vpc_cidr must be an IPv4 CIDR with a prefix between /16 and /24."
  }
}

variable "az_ids" {
  description = "create_vpc only. Two AZ IDs for the subnets. Empty = the first two available AZ IDs that AgentCore Runtime supports in the Region."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for z in var.az_ids : can(regex("^[a-z0-9]+-az[0-9]+$", z))])
    error_message = "az_ids entries must be AZ IDs such as use1-az1."
  }
}

variable "vpc_id" {
  description = "Existing VPC (create_vpc = false)."
  type        = string
  default     = ""

  validation {
    condition     = var.vpc_id == "" || can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must be empty or a VPC id (vpc-...)."
  }
}

variable "subnet_ids" {
  description = "Existing private subnets for the interface endpoints (create_vpc = false)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", s))])
    error_message = "subnet_ids entries must be subnet ids (subnet-...)."
  }
}

variable "route_table_ids" {
  description = "Existing route tables for the S3 gateway endpoint (create_vpc = false)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for r in var.route_table_ids : can(regex("^rtb-[0-9a-f]{8,17}$", r))])
    error_message = "route_table_ids entries must be route table ids (rtb-...)."
  }
}

variable "interface_endpoint_services" {
  description = "Interface endpoints to create, by short service name (for example ecr.api, bedrock-agentcore). Private DNS is enabled on each."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for s in var.interface_endpoint_services : can(regex("^[a-z0-9.-]+$", s))])
    error_message = "interface_endpoint_services entries must be short service names such as ecr.api."
  }

  validation {
    # Fail closed on the AgentCore control plane (docs/SECURITY.md SD-21).
    condition     = alltrue([for s in var.interface_endpoint_services : !can(regex("bedrock-agentcore-control", s))])
    error_message = "bedrock-agentcore-control must not be reachable from the companion runtimes (docs/SECURITY.md SD-21)."
  }
}

variable "client_endpoint_services" {
  description = "The subset of interface_endpoint_services placed in the client security group (HTTPS from approved client CIDRs as well as the runtimes). Every other endpoint sits in the runtime-only security group."
  type        = set(string)
  default     = ["bedrock-agentcore", "bedrock-agentcore.gateway"]

  validation {
    condition     = alltrue([for s in var.client_endpoint_services : can(regex("^[a-z0-9.-]+$", s)) && !can(regex("bedrock-agentcore-control", s))])
    error_message = "client_endpoint_services entries must be short service names and never bedrock-agentcore-control."
  }
}

variable "endpoint_policies" {
  description = "Service key -> endpoint policy JSON. A key without an entry uses the AWS default policy (docs/SECURITY.md SD-15)."
  type        = map(string)
  default     = {}
}

variable "create_s3_gateway_endpoint" {
  description = "Create an S3 gateway endpoint on the route tables (ECR image layers, KB buckets)."
  type        = bool
  default     = true
}

variable "s3_gateway_endpoint_policy" {
  description = "Policy JSON for the S3 gateway endpoint. Required (non-empty) when the endpoint is created."
  type        = string
  default     = ""
}

variable "client_cidrs" {
  description = "IPv4 CIDRs of approved client networks (VPN, Direct Connect, in-VPC desktops) allowed HTTPS to the client endpoints only. Never an all-addresses CIDR; prefix /8 or longer."
  type        = list(string)
  default     = []

  validation {
    # IPv4 only (the SG rule uses cidr_ipv4). 0.0.0.0/0, ::/0 and anything wider than /8 are rejected.
    condition     = alltrue([for c in var.client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0" && can(tonumber(split("/", c)[1])) && try(tonumber(split("/", c)[1]) >= 8, false)])
    error_message = "client_cidrs must be IPv4 CIDRs with a prefix of /8 or longer. 0.0.0.0/0 and ::/0 are never allowed."
  }
}

variable "guards_ready" {
  description = "Ordering token from the caller's guard resource (for example terraform_data.network_guards.id). Endpoints wait for it without a module-level depends_on, so data sources in this module are still read during plan."
  type        = string
  default     = ""
}

variable "enable_resolver_inbound_endpoint" {
  description = "Create a Route 53 Resolver inbound endpoint (two IPs, one per subnet) so DNS forwarders on Direct Connect / VPN networks can resolve the endpoints' private DNS names. Off by default; billed per IP-hour and per query. It answers any name the VPC resolver resolves, for the approved CIDRs only (docs/SECURITY.md SD-26). Its security group cannot be changed after creation."
  type        = bool
  default     = false
}

variable "resolver_client_cidrs" {
  description = "IPv4 CIDRs of the DNS forwarders allowed to query the Resolver inbound endpoint (TCP/UDP 53). Empty = client_cidrs. Never an all-addresses CIDR; prefix /8 or longer."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.resolver_client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0" && can(tonumber(split("/", c)[1])) && try(tonumber(split("/", c)[1]) >= 8, false)])
    error_message = "resolver_client_cidrs must be IPv4 CIDRs with a prefix of /8 or longer. 0.0.0.0/0 and ::/0 are never allowed."
  }
}
