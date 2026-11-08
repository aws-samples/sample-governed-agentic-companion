# The two AgentCore runtimes, built from the SAME engine image family:
#   - orchestrator : HTTP /invocations face on 8080 (the governed engine)
#   - mcp_runtime  : stateless streamable-HTTP MCP /mcp face on 8000 (what the gateway targets)
#
# Both use the shared execution role and the same KB env. The inbound CUSTOM_JWT authorizer is
# attached for auth_mode cognito|entraid, omitted for auth_mode="none" (internal test).
#
# Network mode follows var.network_access:
#   private (DEFAULT): network_mode="VPC" in your private subnets with the runtime SG (HTTPS
#     egress to the VPC endpoints and the S3 prefix list only; no ingress, no internet route).
#     Invocations are accepted only through the AgentCore data-plane endpoint (orchestrator) or
#     from the gateway's execution role (MCP runtime); see resource_policies.tf.
#   public (DEMO opt-in): network_mode="PUBLIC", today's behaviour. Inbound is gated only by the
#     CUSTOM_JWT authorizer, reachable from the internet. guards.tf refuses public with
#     auth_mode="none" and requires acknowledge_public_endpoint=true.
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370

# ---- Orchestrator runtime (HTTP /invocations) -------------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "orchestrator" {
  agent_runtime_name = replace("${var.name_prefix}_orchestrator", "-", "_")
  description        = "Governed companion orchestrator — HTTP /invocations face"
  role_arn           = aws_iam_role.runtime_exec.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = local.orchestrator_image_uri
    }
  }

  network_configuration {
    # "VPC" in private mode (default), "PUBLIC" only for the acknowledged demo opt-in.
    network_mode = local.runtime_network_mode

    dynamic "network_mode_config" {
      for_each = local.network_private ? [1] : []
      content {
        subnets         = local.private_subnet_ids_effective
        security_groups = [aws_security_group.runtime[0].id]
      }
    }
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  dynamic "authorizer_configuration" {
    for_each = local.inbound_jwt_enabled ? [1] : []
    content {
      custom_jwt_authorizer {
        discovery_url = local.jwt_discovery_url
        # Front-door clients only (the gateway's M2M client never calls the orchestrator).
        allowed_clients = local.orchestrator_allowed_clients

        # Entitlement (cognito.tf): the token's group claim must contain the companion's group.
        dynamic "custom_claim" {
          for_each = local.entitlement_enforced ? [1] : []
          content {
            inbound_token_claim_name       = local.entitlement_claim["name"]
            inbound_token_claim_value_type = local.entitlement_claim["value_type"]
            authorizing_claim_match_value {
              claim_match_operator = "CONTAINS"
              claim_match_value {
                match_value_string = local.entitlement_group
              }
            }
          }
        }
      }
    }
  }

  environment_variables = local.runtime_env

  # The runtime must not start before ECR, S3 and Logs are reachable (otherwise it never reaches
  # READY), and the image pull must already run under the private-mode ECR repository policy.
  depends_on = [
    terraform_data.auth_guards,
    terraform_data.network_guards,
    null_resource.build_push_orchestrator,
    aws_iam_role_policy.runtime_exec_inline,
    aws_iam_role_policy_attachment.runtime_exec_managed,
    aws_bedrockagentcore_memory_strategy.task_summary,
    module.private_network,
    aws_vpc_security_group_egress_rule.runtime_to_vpce,
    aws_vpc_security_group_egress_rule.runtime_to_s3,
    aws_vpc_security_group_egress_rule.runtime_to_existing_endpoints,
    aws_vpc_security_group_ingress_rule.vpce_from_runtime,
    aws_vpc_security_group_ingress_rule.existing_endpoint_from_runtime,
    aws_ecr_repository_policy.orchestrator,
    aws_ecr_repository_policy.mcp_runtime,
  ]
}

# ---- MCP runtime (stateless MCP /mcp, port 8000) ----------------------------------------
resource "aws_bedrockagentcore_agent_runtime" "mcp_runtime" {
  agent_runtime_name = replace("${var.name_prefix}_mcp_runtime", "-", "_")
  description        = "Governed companion MCP runtime — stateless streamable-HTTP /mcp face (gateway target)"
  role_arn           = aws_iam_role.runtime_exec.arn

  agent_runtime_artifact {
    container_configuration {
      container_uri = local.mcp_runtime_image_uri
    }
  }

  network_configuration {
    # "VPC" in private mode (default), "PUBLIC" only for the acknowledged demo opt-in.
    network_mode = local.runtime_network_mode

    dynamic "network_mode_config" {
      for_each = local.network_private ? [1] : []
      content {
        subnets         = local.private_subnet_ids_effective
        security_groups = [aws_security_group.runtime[0].id]
      }
    }
  }

  protocol_configuration {
    server_protocol = "MCP"
  }

  # Private mode with a gateway: NO JWT authorizer. The runtime uses the default SigV4 (IAM)
  # inbound authorization, and its resource policy allows only the gateway execution role
  # (resource_policies.tf), so builders cannot bypass the gateway with a front-door token.
  # Public mode (and private without a gateway) keeps the JWT authorizer.
  dynamic "authorizer_configuration" {
    for_each = local.inbound_jwt_enabled && !local.mcp_runtime_iam_inbound ? [1] : []
    content {
      custom_jwt_authorizer {
        discovery_url = local.jwt_discovery_url
        # Public mode with the gateway: the gateway's M2M client ONLY (client-credentials tokens
        # carry no groups, so the gateway is the entitlement point and user tokens are refused).
        # Without a gateway: the front-door clients, with the same claim rule as the orchestrator.
        allowed_clients = local.mcp_runtime_allowed_clients

        dynamic "custom_claim" {
          for_each = local.entitlement_enforced && !local.gateway_outbound_oauth ? [1] : []
          content {
            inbound_token_claim_name       = local.entitlement_claim["name"]
            inbound_token_claim_value_type = local.entitlement_claim["value_type"]
            authorizing_claim_match_value {
              claim_match_operator = "CONTAINS"
              claim_match_value {
                match_value_string = local.entitlement_group
              }
            }
          }
        }
      }
    }
  }

  environment_variables = local.runtime_env

  depends_on = [
    terraform_data.auth_guards,
    terraform_data.network_guards,
    null_resource.build_push_mcp_runtime,
    aws_iam_role_policy.runtime_exec_inline,
    aws_iam_role_policy_attachment.runtime_exec_managed,
    aws_bedrockagentcore_memory_strategy.task_summary,
    module.private_network,
    aws_vpc_security_group_egress_rule.runtime_to_vpce,
    aws_vpc_security_group_egress_rule.runtime_to_s3,
    aws_vpc_security_group_egress_rule.runtime_to_existing_endpoints,
    aws_vpc_security_group_ingress_rule.vpce_from_runtime,
    aws_vpc_security_group_ingress_rule.existing_endpoint_from_runtime,
    aws_ecr_repository_policy.orchestrator,
    aws_ecr_repository_policy.mcp_runtime,
  ]
}
