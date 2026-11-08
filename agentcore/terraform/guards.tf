# Deploy-time safety interlocks. These fail `terraform plan/apply` early with a clear message
# rather than letting a misconfigured auth mode reach AWS. Preconditions run during plan.

resource "terraform_data" "auth_guards" {
  # No-op resource whose only job is to host validation preconditions.
  input = var.auth_mode

  lifecycle {
    # auth_mode="none" must be explicitly confirmed — it is an unauthenticated front door.
    precondition {
      condition     = !local.auth_is_none || var.allow_unauthenticated
      error_message = "auth_mode=\"none\" is UNAUTHENTICATED (internal test only). Set allow_unauthenticated=true to confirm you intend this. Never use it outside an isolated dev account."
    }

    # entraid requires the discovery URL + at least one front-door client id.
    precondition {
      condition     = !local.auth_is_entraid || (var.entraid_discovery_url != "" && length(var.entraid_frontdoor_client_ids) > 0)
      error_message = "auth_mode=\"entraid\" requires entraid_discovery_url and at least one entraid_frontdoor_client_ids value."
    }

    # A gateway in front of an unauthenticated runtime would publish an open tool surface.
    precondition {
      condition     = !(local.auth_is_none && var.deploy_gateway)
      error_message = "Refusing to deploy a Gateway with auth_mode=\"none\": that would expose an unauthenticated tool surface. Use cognito or entraid when deploy_gateway=true."
    }

    # auth_mode="none" attaches no JWT authorizer (the runtimes then use the default SigV4 IAM
    # inbound authorization; UNVERIFIED until a deploy). Refuse it unless explicitly
    # acknowledged via allow_unauthenticated=true. In public mode network_guards refuses
    # auth_mode="none" outright; in private mode every invoke still needs a VPC endpoint.
    precondition {
      condition     = !local.auth_is_none || var.allow_unauthenticated
      error_message = "Refusing runtimes with auth_mode=\"none\" (no JWT authorizer) without allow_unauthenticated=true (network_access=${var.network_access}). Use auth_mode=cognito|entraid, or set allow_unauthenticated=true only inside an isolated test account with network_access=\"private\"."
    }

    # The Guardrails PII backend needs a guardrail: EITHER Terraform creates one
    # (create_bedrock_guardrail=true) OR you supply an existing guardrail_id. Fail loud at
    # plan time rather than a silent no-op at runtime.
    precondition {
      condition     = var.pii_backend != "guardrails" || var.create_bedrock_guardrail || var.guardrail_id != ""
      error_message = "pii_backend=\"guardrails\" requires a guardrail: set create_bedrock_guardrail=true to have Terraform create one, or supply an existing guardrail_id."
    }

    # G-entitlement-consume. The consume path never creates groups in the shared pool.
    precondition {
      condition     = !(var.manage_entitlement_group == true && !var.manage_identity_pool)
      error_message = "manage_entitlement_group = true needs an owned pool: the shared pool's owner creates the group (platform/ apps map or a manual step)"
    }

    # G-entitlement-public. The public stack always requires the entitlement group.
    precondition {
      condition     = local.network_private || var.require_entitlement_group
      error_message = "require_entitlement_group = false is refused with network_access=\"public\": an internet-reachable companion must require the entitlement group. Keep require_entitlement_group = true (and put entitled users in the group), or use network_access=\"private\"."
    }
  }
}

# Opt-in Bedrock diagram planner interlocks (D11.2). Each message names the variable to set.
resource "terraform_data" "diagram_planner_guards" {
  input = var.diagram_planner

  lifecycle {
    precondition {
      condition     = var.diagram_planner != "bedrock" || var.diagram_planner_model_id != ""
      error_message = "diagram_planner = \"bedrock\" needs diagram_planner_model_id (a Bedrock model or inference-profile id that supports Converse tool use)."
    }
    precondition {
      condition     = var.diagram_planner != "bedrock" || var.create_bedrock_guardrail || var.guardrail_id != ""
      error_message = "diagram_planner = \"bedrock\" needs a guardrail: set create_bedrock_guardrail = true, or supply an existing guardrail_id."
    }
    precondition {
      condition     = var.diagram_planner != "bedrock" || !local.network_private || var.enable_bedrock_runtime_endpoint || contains(keys(var.existing_interface_endpoint_ids), "bedrock-runtime")
      error_message = "diagram_planner = \"bedrock\" in private mode needs the bedrock-runtime endpoint: set enable_bedrock_runtime_endpoint = true, or reuse one via existing_interface_endpoint_ids[\"bedrock-runtime\"]."
    }
  }
}

# Network-access interlocks (private by default). Every indexed reference to a counted data
# source is wrapped in try()/one(): HCL does not short-circuit && / ||.
resource "terraform_data" "network_guards" {
  input = var.network_access

  lifecycle {
    # 1. Public needs a second, explicit acknowledgement.
    precondition {
      condition     = local.network_private || var.acknowledge_public_endpoint
      error_message = "network_access=\"public\" makes the knowledge plane internet-reachable (JWT only). Set acknowledge_public_endpoint=true for a demo sandbox, or use the default network_access=\"private\"."
    }

    # 2. Public + no JWT is refused outright (no override). allow_unauthenticated stays usable
    #    only in private mode, where every invoke still needs a VPC endpoint.
    precondition {
      condition     = local.network_private || !local.auth_is_none
      error_message = "Refusing network_access=\"public\" with auth_mode=\"none\": that would be an internet-reachable knowledge plane with no JWT authorizer. Use auth_mode=cognito|entraid, or network_access=\"private\"."
    }

    # 3. Existing-VPC inputs.
    precondition {
      condition     = !local.network_private || local.create_vpc || (var.vpc_id != "" && length(var.private_subnet_ids) >= 2 && (length(var.private_route_table_ids) >= 1 || var.existing_s3_gateway_endpoint_id != ""))
      error_message = "network_access=\"private\" needs vpc_id, at least two private_subnet_ids and private_route_table_ids (or existing_s3_gateway_endpoint_id). For a one-apply sandbox set create_vpc=true instead."
    }

    # 4. Client CIDRs (an empty list would deploy an unreachable companion).
    #    Not needed when every client endpoint is reused (a shared-network consumer): the kit then
    #    creates no client SG to put the CIDRs on.
    precondition {
      condition     = !local.network_private || length(local.client_cidrs_effective) > 0 || length(setintersection(toset(local.client_endpoint_keys), local.managed_endpoint_keys)) == 0
      error_message = "network_access=\"private\" needs private_client_cidrs (your VPN/on-prem/desktop ranges); an all-addresses /0 CIDR is never accepted."
    }

    # 5. create_vpc only in private mode.
    precondition {
      condition     = !var.create_vpc || local.network_private
      error_message = "create_vpc=true is only valid with network_access=\"private\"."
    }

    # 6. VPC DNS (private DNS for interface endpoints needs both attributes).
    precondition {
      condition     = !local.network_private || local.create_vpc || var.vpc_id == "" || (try(data.aws_vpc.existing[0].enable_dns_support, false) && try(data.aws_vpc.existing[0].enable_dns_hostnames, false))
      error_message = "vpc_id must have DNS resolution (enableDnsSupport) and DNS hostnames (enableDnsHostnames) enabled for private DNS on the interface endpoints."
    }

    # 7. Subnets belong to vpc_id, span >= 2 AZs, and sit in AgentCore-supported AZ IDs
    #    (local.agentcore_supported_az_ids, from the module; empty for a Region not in its list,
    #    which skips the AZ-ID part).
    precondition {
      condition = !local.network_private || local.create_vpc || (
        alltrue([for s in data.aws_subnet.existing : s.vpc_id == var.vpc_id]) &&
        length(distinct([for s in data.aws_subnet.existing : s.availability_zone_id])) >= 2 &&
        (length(local.agentcore_supported_az_ids) == 0 ||
        alltrue([for s in data.aws_subnet.existing : contains(local.agentcore_supported_az_ids, s.availability_zone_id)]))
      )
      error_message = "private_subnet_ids must belong to vpc_id, cover at least two different AZs, and use AZ IDs that AgentCore Runtime supports in this Region (${jsonencode(local.agentcore_supported_az_ids)})."
    }

    # 8. Sandbox AZs resolvable.
    precondition {
      condition     = !local.create_vpc || length(module.private_network[0].az_ids) == 2
      error_message = "create_vpc=true needs exactly two AgentCore-supported AZ IDs: set private_subnet_az_ids to two AZ IDs, or use a Region where two supported AZs are available."
    }

    # 9. Reused endpoints need their SGs so the runtime egress rule can be created.
    precondition {
      condition     = !local.network_private || length(var.existing_interface_endpoint_ids) == 0 || length(var.existing_endpoint_security_group_ids) > 0
      error_message = "existing_interface_endpoint_ids needs existing_endpoint_security_group_ids (the SGs on those endpoints) so the runtime SG can reach them on TCP 443."
    }

    # 10. Knowledge admins listed.
    precondition {
      condition     = !local.network_private || length(var.knowledge_admin_principal_arns) > 0
      error_message = "network_access=\"private\" needs knowledge_admin_principal_arns: list every principal that runs terraform, every CI role that pushes images, and every curator who promotes the overlay. Unlisted principals cannot read the KB bucket or pull/push the images from outside the VPC."
    }

    # 11. Route tables serve the subnets (the S3 gateway endpoint must be on each subnet's table).
    precondition {
      condition     = !local.network_private || local.create_vpc || alltrue([for s, rt in local.subnet_route_table : contains(local.s3_route_tables, rt)])
      error_message = "Each private subnet's route table must be in private_route_table_ids (or attached to existing_s3_gateway_endpoint_id). Without the S3 gateway endpoint on that table the runtimes cannot fetch ECR layers or the KB and never reach READY. Offending subnet => route table: ${jsonencode({ for s, rt in local.subnet_route_table : s => rt if !contains(local.s3_route_tables, rt) })}"
    }

    # 12. A reused S3 endpoint must be a Gateway endpoint for S3 in this VPC.
    precondition {
      condition     = !local.reuse_s3_endpoint || (try(data.aws_vpc_endpoint.existing_s3[0].vpc_id, "") == var.vpc_id && try(data.aws_vpc_endpoint.existing_s3[0].vpc_endpoint_type, "") == "Gateway" && try(data.aws_vpc_endpoint.existing_s3[0].service_name, "") == "com.amazonaws.${local.region}.s3")
      error_message = "existing_s3_gateway_endpoint_id must be a Gateway-type endpoint for com.amazonaws.<region>.s3 in vpc_id."
    }

    # G-client-sg-subset. A precondition, not a variable validation: cross-variable references in
    # validation need Terraform >= 1.9 and the floor is 1.6.
    precondition {
      condition     = alltrue([for s in var.existing_client_endpoint_security_group_ids : contains(var.existing_endpoint_security_group_ids, s)])
      error_message = "every existing_client_endpoint_security_group_ids entry must also be listed in existing_endpoint_security_group_ids."
    }

    # G-probe-private. The probe tests private access.
    precondition {
      condition     = !var.enable_private_access_probe || local.network_private
      error_message = "the private access probe tests private access; it is refused in public mode (network_access=\"public\")."
    }

    # G-probe-gateway. The probe exercises the gateway /mcp route.
    precondition {
      condition     = !var.enable_private_access_probe || local.gateway_enabled
      error_message = "the private access probe exercises the gateway /mcp route; set deploy_gateway=true."
    }

    # G-probe-client-sg. On reused client endpoints (including the mixed created/reused case) the
    # probe needs the SGs on those endpoints, and only those.
    precondition {
      condition     = !var.enable_private_access_probe || !local.network_private || !local.client_endpoints_reused || length(var.existing_client_endpoint_security_group_ids) > 0
      error_message = "the private access probe on reused endpoints needs existing_client_endpoint_security_group_ids (the SG on the reused gateway/data-plane endpoints)."
    }

    # G-resolver. The Resolver inbound endpoint is a private-mode feature.
    precondition {
      condition     = !var.enable_resolver_inbound_endpoint || local.network_private
      error_message = "the Route 53 Resolver inbound endpoint is a private-mode feature; set network_access=\"private\"."
    }

    # G-none-shared. With auth_mode="none" there is no JWT authorizer, so the runtimes' only
    # separation is the endpoint ID; on a reused or shared data-plane endpoint every other
    # companion's runtime role (bedrock-agentcore:* via BedrockAgentCoreFullAccess) could invoke
    # this companion's runtimes. Refused rather than patched with a per-principal Deny.
    precondition {
      condition     = !(local.auth_is_none && local.dataplane_endpoint_shared)
      error_message = "auth_mode=\"none\" is refused on a reused or shared bedrock-agentcore endpoint (existing_interface_endpoint_ids[\"bedrock-agentcore\"] or additional_dataplane_vpce_ids): with no JWT authorizer, any principal in the account that reaches that endpoint could invoke this companion's runtimes. Use auth_mode=cognito|entraid, or a dedicated network."
    }
  }
}

# Plan-time warnings (never block apply) for consumer-side settings that have no effect, or are
# likely to conflict, on a shared network. Set these where the shared endpoints are managed (the
# platform stack for a shared network).
check "shared_endpoint_extras" {
  assert {
    condition     = !(local.dataplane_endpoint_reused && length(var.dataplane_endpoint_extra_actions) > 0)
    error_message = "dataplane_endpoint_extra_actions has no effect on a reused data-plane endpoint; set it where that endpoint is managed (the platform stack for a shared network)."
  }
  assert {
    condition     = !(local.network_private && !local.create_vpc && length(var.existing_interface_endpoint_ids) > 0 && length(var.extra_interface_endpoint_services) > 0)
    error_message = "extra_interface_endpoint_services creates endpoints in a reused VPC; if the platform already serves a service, the private-DNS conflict fails the apply. Prefer adding extras on the platform."
  }
  assert {
    condition     = !(local.network_private && local.reuse_s3_endpoint && length(var.existing_interface_endpoint_ids) > 0 && var.kb_bucket_name != "")
    error_message = "a custom kb_bucket_name on a shared network must be listed in the platform's kb_bucket_arn_patterns, or the shared S3 gateway endpoint policy denies this companion's KB reads and writes."
  }
}

# Plan-time warning (never blocks apply): the runner is exempt for this apply only.
check "runner_is_knowledge_admin" {
  assert {
    condition     = !local.network_private || local.runner_listed
    error_message = "The principal running terraform (${local.runner_arn}) is not in knowledge_admin_principal_arns. It is exempted for THIS apply only; the next runner who is not listed cannot refresh the KB bucket or push images. Add every terraform/CI/curator principal to knowledge_admin_principal_arns."
  }
}
