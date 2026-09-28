# The two AgentCore runtimes, built from the SAME engine image family:
#   - orchestrator : HTTP /invocations face on 8080 (the governed engine)
#   - mcp_runtime  : stateless streamable-HTTP MCP /mcp face on 8000 (what the gateway targets)
#
# Both use the shared execution role and the same KB env. The inbound CUSTOM_JWT authorizer is
# attached for auth_mode cognito|entraid, omitted for auth_mode="none" (internal test).

# ---- Orchestrator runtime (HTTP /invocations) -------------------------------------------
# kics-scan ignore-line: "IAM Access Analyzer Not Enabled" is an account/org baseline control
# made opt-in here via enable_access_analyzer (access_analyzer.tf) — docs/SECURITY.md SD-8.
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
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  dynamic "authorizer_configuration" {
    for_each = local.inbound_jwt_enabled ? [1] : []
    content {
      custom_jwt_authorizer {
        discovery_url = local.jwt_discovery_url
        # Runtime allow-list = front-door clients PLUS the gateway's outbound M2M client, so
        # the gateway (which calls the runtime with the M2M token) is authorized.
        allowed_clients = local.runtime_allowed_clients
      }
    }
  }

  environment_variables = local.runtime_env

  depends_on = [
    terraform_data.auth_guards,
    null_resource.build_push_orchestrator,
    aws_iam_role_policy.runtime_exec_inline,
    aws_iam_role_policy_attachment.runtime_exec_managed,
    aws_bedrockagentcore_memory_strategy.task_summary,
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
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "MCP"
  }

  dynamic "authorizer_configuration" {
    for_each = local.inbound_jwt_enabled ? [1] : []
    content {
      custom_jwt_authorizer {
        discovery_url = local.jwt_discovery_url
        # Runtime allow-list = front-door clients PLUS the gateway's outbound M2M client, so
        # the gateway (which calls the runtime with the M2M token) is authorized.
        allowed_clients = local.runtime_allowed_clients
      }
    }
  }

  environment_variables = local.runtime_env

  depends_on = [
    terraform_data.auth_guards,
    null_resource.build_push_mcp_runtime,
    aws_iam_role_policy.runtime_exec_inline,
    aws_iam_role_policy_attachment.runtime_exec_managed,
    aws_bedrockagentcore_memory_strategy.task_summary,
  ]
}
