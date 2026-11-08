# Platform (shared layer) inputs. Account-specific values go in a gitignored terraform.tfvars
# (copy terraform.tfvars.example). Nothing here has a default that names an account or customer.

variable "aws_region" {
  type        = string
  description = "AWS Region for the shared platform layer. Required: write it explicitly (when adopting an existing platform state, use the Region that state was created in)."
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to every platform resource through the provider default_tags. Empty by default so adopting an existing platform state plans no tag changes."
  default     = {}
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for remote Terraform state. Keep it customer-agnostic, for example companion-tfstate-<ACCOUNT_ID>-<REGION>."
}

variable "lock_table_name" {
  type        = string
  description = "DynamoDB table name for state locking (the table is created only when the module's enable_lock_table is true; the default uses S3 native locking)."
  default     = "companion-tf-locks"
}

variable "identity_name_prefix" {
  type        = string
  description = "Prefix for the shared Cognito pool (<prefix>-pool) and domain (<prefix>-<account>). Neutral (for example \"companion\") so it serves every companion and does not collide with a single companion's own pool domain. Customer-agnostic."
  default     = "companion"
}

variable "apps" {
  type        = map(string)
  description = "Map of companion key -> display label; one entitlement group <key>-users per entry. Companion names only, never customer identifiers. Required: list every companion that uses the shared pool."

  validation {
    condition     = alltrue([for k in keys(var.apps) : can(regex("^[a-z][a-z0-9-]{1,31}$", k))])
    error_message = "Each app key must be a short lowercase companion name (letters, digits, hyphens), not a customer identifier."
  }
}

# ---- Optional shared private network (module.network, network.tf) -------------------------
# Off by default. With both enable_private_network and enable_resolver_inbound_endpoint false,
# the platform contains only module.backend and module.identity (the adoption shape).

variable "enable_private_network" {
  type        = bool
  description = "Create ONE shared private network (VPC, private subnets, interface endpoints, S3 gateway endpoint, endpoint SGs) that several companions consume through vpc_id and the existing_* inputs. Off by default. One network boundary for several companions (docs/SECURITY.md SD-27); a dedicated companion network is still the default."
  default     = false
}

variable "network_name_prefix" {
  type        = string
  description = "Name prefix for the shared network resources."
  default     = "companion"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.network_name_prefix))
    error_message = "network_name_prefix must be 2-31 chars, start with a letter, and use only lowercase letters, digits, and hyphens."
  }
}

variable "network_vpc_cidr" {
  type        = string
  description = "CIDR of the shared VPC (does not overlap the companion sandbox default 10.80.0.0/16)."
  default     = "10.90.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.network_vpc_cidr)) && can(tonumber(split("/", var.network_vpc_cidr)[1])) && try(tonumber(split("/", var.network_vpc_cidr)[1]) >= 16 && tonumber(split("/", var.network_vpc_cidr)[1]) <= 24, false)
    error_message = "network_vpc_cidr must be an IPv4 CIDR with a prefix between /16 and /24."
  }
}

variable "network_az_ids" {
  type        = list(string)
  description = "Two AZ IDs for the shared subnets. Empty = the first two available AZ IDs that AgentCore Runtime supports in the Region."
  default     = []

  validation {
    condition     = alltrue([for z in var.network_az_ids : can(regex("^[a-z0-9]+-az[0-9]+$", z))])
    error_message = "network_az_ids entries must be AZ IDs such as use1-az1."
  }
}

variable "private_client_cidrs" {
  type        = list(string)
  description = "IPv4 CIDRs of the developer/IDE networks (VPN, Direct Connect, in-VPC desktops) allowed HTTPS to the shared client endpoints (gateway, data plane). Required when enable_private_network = true. Never an all-addresses CIDR; prefix /8 or longer."
  default     = []

  validation {
    condition     = alltrue([for c in var.private_client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0" && can(tonumber(split("/", c)[1])) && try(tonumber(split("/", c)[1]) >= 8, false)])
    error_message = "private_client_cidrs must be IPv4 CIDRs with a prefix of /8 or longer. 0.0.0.0/0 and ::/0 are never allowed."
  }
}

variable "enable_gateway_endpoint" {
  type        = bool
  description = "Create the bedrock-agentcore.gateway interface endpoint (needed by companions that deploy a gateway)."
  default     = true
}

variable "enable_bedrock_runtime_endpoint" {
  type        = bool
  description = "Create the bedrock-runtime interface endpoint (model calls and Bedrock Guardrails on PrivateLink)."
  default     = true
}

variable "bedrock_runtime_client_access" {
  type        = bool
  description = "Also allow private_client_cidrs to reach the bedrock-runtime endpoint (IDE assistants that use Amazon Bedrock in this account)."
  default     = false
}

variable "enable_xray_endpoint" {
  type        = bool
  description = "Create the X-Ray interface endpoint used by the ADOT tracer."
  default     = true
}

variable "enable_comprehend_endpoint" {
  type        = bool
  description = "Create the Comprehend interface endpoint (companions with pii_backend = \"comprehend\")."
  default     = false
}

variable "extra_interface_endpoint_services" {
  type        = list(string)
  description = "Extra interface endpoints by short service name (for example sts). Default endpoint policy. bedrock-agentcore-control is rejected (docs/SECURITY.md SD-21). Extra services are not passed to companions through existing_interface_endpoint_ids."
  default     = []

  validation {
    condition     = alltrue([for s in var.extra_interface_endpoint_services : can(regex("^[a-z0-9.-]+$", s))])
    error_message = "extra_interface_endpoint_services entries must be short service names such as sts or monitoring."
  }

  validation {
    condition     = alltrue([for s in var.extra_interface_endpoint_services : !contains(["bedrock-agentcore", "bedrock-agentcore.gateway", "bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "xray", "comprehend"], s)])
    error_message = "extra_interface_endpoint_services must not repeat an endpoint the platform already manages."
  }

  validation {
    condition     = alltrue([for s in var.extra_interface_endpoint_services : !can(regex("bedrock-agentcore-control", s))])
    error_message = "bedrock-agentcore-control must not be reachable from the companion runtimes (docs/SECURITY.md SD-21)."
  }
}

variable "allow_memory_dataplane_actions" {
  type        = bool
  description = "Allow the AgentCore Memory data-plane actions through the shared data-plane endpoint (any consuming companion may enable Memory)."
  default     = true
}

variable "dataplane_endpoint_extra_actions" {
  type        = list(string)
  description = "Extra bedrock-agentcore actions to allow through the shared data-plane endpoint. On a shared network this is where a consumer's extra actions belong."
  default     = []

  validation {
    condition     = alltrue([for a in var.dataplane_endpoint_extra_actions : can(regex("^bedrock-agentcore:[A-Za-z]+$", a))])
    error_message = "dataplane_endpoint_extra_actions entries must be single bedrock-agentcore:<Action> names (no wildcards, no other service)."
  }
}

variable "kb_bucket_arn_patterns" {
  type        = list(string)
  description = "KB bucket ARNs (or patterns) the shared S3 gateway endpoint policy admits for kb_prefix object reads/writes and listing, always limited to this account by aws:ResourceAccount. Empty = the companion auto-name pattern arn:<partition>:s3:::*-kb-<account>-<region>-*. List exact ARNs to narrow it (recommended; SD-27), including any companion with a custom kb_bucket_name."
  default     = []

  validation {
    # Exact bucket ARNs of any valid name, or wildcard patterns that contain -kb- and do not start
    # with a wildcard. try() because && does not short-circuit: split() of a malformed value has
    # no element [1].
    condition     = alltrue([for p in var.kb_bucket_arn_patterns : can(regex("^arn:aws[a-z-]*:s3:::[a-z0-9.*-]{3,63}$", p)) && try(!startswith(split(":::", p)[1], "*"), false) && (!strcontains(p, "*") || strcontains(p, "-kb-"))])
    error_message = "kb_bucket_arn_patterns must be exact S3 bucket ARNs, or wildcard patterns that contain -kb- and do not start with a wildcard."
  }
}

variable "kb_prefix" {
  type        = string
  description = "KB key prefix admitted for object reads/writes by the shared S3 gateway endpoint policy (matches the companions' kb_prefix)."
  default     = "kb"
}

variable "enable_resolver_inbound_endpoint" {
  type        = bool
  description = "Create a Route 53 Resolver inbound endpoint in the shared network (needs enable_private_network). Off by default; billed per IP-hour and per query; answers any name the VPC resolves for the approved CIDRs (docs/SECURITY.md SD-26)."
  default     = false
}

variable "resolver_inbound_client_cidrs" {
  type        = list(string)
  description = "IPv4 CIDRs of the on-premises DNS forwarders allowed to query the Resolver inbound endpoint. Empty = private_client_cidrs. Never an all-addresses CIDR; prefix /8 or longer."
  default     = []

  validation {
    condition     = alltrue([for c in var.resolver_inbound_client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0" && can(tonumber(split("/", c)[1])) && try(tonumber(split("/", c)[1]) >= 8, false)])
    error_message = "resolver_inbound_client_cidrs must be IPv4 CIDRs with a prefix of /8 or longer. 0.0.0.0/0 and ::/0 are never allowed."
  }
}
