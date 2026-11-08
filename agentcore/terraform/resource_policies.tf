# Private network mode: VPC endpoint policies (this section) and the AgentCore resource-based
# policies that make the endpoints the ONLY data path (added with the runtimes' VPC mode).
# ---------------------------------------------------------------------------
# Every document here is gated on local.network_private, so public mode creates none of them.
#
# Endpoint policies (https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-interface-endpoints.html):
# for OAuth (JWT) requests the endpoint-policy principal must be "*"; endpoint policies can only
# restrict IAM principals. They narrow WHAT can be reached through the endpoint; the resource
# policies decide WHO can reach the companion and from WHERE.
#
# ECR, Logs, X-Ray and extra endpoints keep the AWS default policy (docs/SECURITY.md SD-15):
# tightening them could break image pulls made by the AgentCore service on the account's behalf
# (which principal pulls is not documented). With the private defaults the Logs/X-Ray endpoints
# carry metadata log lines and spans without tool content (trace_tool_content = false).

# bedrock-agentcore.gateway: invoke THIS gateway only.
data "aws_iam_policy_document" "vpce_gateway" {
  count = local.network_private && local.gateway_enabled ? 1 : 0

  statement {
    sid       = "InvokeThisGatewayOnly"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.this[0].gateway_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# bedrock-agentcore (data plane): only the actions the bridge and the runtimes need, and only on
# this account's AgentCore resources. Keeps InvokeAgentRuntimeCommand/CommandShell, WebSocket,
# code-interpreter, browser, harness and similar actions off this endpoint. An endpoint policy
# (not a resource policy), so a wildcard ARN inside this account is valid here.
data "aws_iam_policy_document" "vpce_agentcore" {
  count = local.network_private ? 1 : 0

  statement {
    sid       = "ThisAccountAgentCoreDataPlane"
    effect    = "Allow"
    actions   = concat(local.dataplane_base_actions, local.memory_enabled ? local.memory_dataplane_actions : [], var.dataplane_endpoint_extra_actions)
    resources = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# bedrock-runtime (SigV4 callers only): this account's principals only. Resource "*" is
# intentional: cross-Region inference profiles route to foundation-model ARNs in other Regions;
# the account condition is the boundary and IAM still scopes per ARN.
data "aws_iam_policy_document" "vpce_bedrock_runtime" {
  count = local.network_private ? 1 : 0

  statement {
    sid       = "ThisAccountModelUse"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail"]
    resources = ["*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [local.account_id]
    }
  }
}

# comprehend (only created when pii_backend = "comprehend").
data "aws_iam_policy_document" "vpce_comprehend" {
  count = local.network_private ? 1 : 0

  statement {
    sid       = "ThisAccountPiiDetect"
    effect    = "Allow"
    actions   = ["comprehend:DetectPiiEntities"]
    resources = ["*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [local.account_id]
    }
  }
}

# S3 gateway endpoint: the ECR image-layer bucket (agentcore-vpc.html) plus the KB bucket.
data "aws_iam_policy_document" "s3_gateway_endpoint" {
  count = local.network_private ? 1 : 0

  statement {
    sid       = "EcrLayerBucket"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::prod-${local.region}-starport-layer-bucket/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "KbBucketObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.kb.arn}/${var.kb_prefix}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "KbBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.kb.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# ===========================================================================================
# AgentCore resource-based policies (https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html)
# ===========================================================================================
# - For OAuth (JWT) resources the statement principal must be "*"; the token is validated before
#   policy evaluation, then aws:SourceVpce decides. A request from the internet carries no
#   aws:SourceVpce, and StringNotEquals on an absent key is true, so it is DENIED even with a
#   valid JWT.
# - Resource must be the exact ARN of the resource the policy is attached to.
# - Explicit Deny wins over any identity-based Allow.

# Gateway: InvokeGateway only through the approved gateway endpoint(s).
data "aws_iam_policy_document" "gateway_resource" {
  count = local.network_private && local.gateway_enabled ? 1 : 0

  statement {
    sid       = "AllowInvokeViaApprovedGatewayEndpoint"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.this[0].gateway_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceVpce"
      values   = local.gateway_vpce_ids
    }
  }

  statement {
    sid       = "DenyInvokeNotViaApprovedGatewayEndpoint"
    effect    = "Deny"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.this[0].gateway_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceVpce"
      values   = local.gateway_vpce_ids
    }
  }
}

resource "aws_bedrockagentcore_resource_policy" "gateway" {
  count        = local.network_private && local.gateway_enabled ? 1 : 0
  resource_arn = aws_bedrockagentcore_gateway.this[0].gateway_arn
  policy       = data.aws_iam_policy_document.gateway_resource[0].json

  depends_on = [terraform_data.network_guards]
}

# Orchestrator runtime: InvokeAgentRuntime only through the approved data-plane endpoint(s);
# every runtime action denied otherwise. The DEFAULT runtime endpoint gets no separate policy:
# the runtime-level Deny already applies to every endpoint.
data "aws_iam_policy_document" "orchestrator_resource" {
  count = local.network_private ? 1 : 0

  statement {
    sid       = "AllowInvokeViaApprovedDataPlaneEndpoint"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceVpce"
      values   = local.dataplane_vpce_ids
    }

    # auth_mode="none" only: with no JWT authorizer the runtime uses SigV4, so the "*" Allow
    # would admit other accounts' principals through an approved endpoint. Never added in the
    # JWT modes (an OAuth request has no aws:PrincipalAccount, so it would lock builders out).
    dynamic "condition" {
      for_each = local.auth_is_none ? [1] : []
      content {
        test     = "StringEquals"
        variable = "aws:PrincipalAccount"
        values   = [local.account_id]
      }
    }
  }

  statement {
    sid       = "DenyRuntimeActionsNotViaApprovedDataPlaneEndpoint"
    effect    = "Deny"
    actions   = local.runtime_policy_actions
    resources = [aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceVpce"
      values   = local.dataplane_vpce_ids
    }
  }
}

resource "aws_bedrockagentcore_resource_policy" "orchestrator" {
  count        = local.network_private ? 1 : 0
  resource_arn = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
  policy       = data.aws_iam_policy_document.orchestrator_resource[0].json

  depends_on = [terraform_data.network_guards]
}

# MCP runtime, private WITH a gateway: SigV4 inbound, only the gateway execution role may
# invoke it (runtime-oauth.html "Restrict IAM (SigV4) inbound invocation to your gateway").
data "aws_iam_policy_document" "mcp_runtime_resource" {
  count = local.mcp_runtime_iam_inbound ? 1 : 0

  statement {
    sid       = "AllowOnlyGatewayRole"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn]

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.gateway_exec[0].arn]
    }
  }

  statement {
    sid       = "DenyOtherPrincipals"
    effect    = "Deny"
    actions   = local.runtime_policy_actions
    resources = [aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.gateway_exec[0].arn]
    }
  }
}

resource "aws_bedrockagentcore_resource_policy" "mcp_runtime" {
  count        = local.mcp_runtime_iam_inbound ? 1 : 0
  resource_arn = aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn
  policy       = data.aws_iam_policy_document.mcp_runtime_resource[0].json

  depends_on = [terraform_data.network_guards]
}

# MCP runtime, private WITHOUT a gateway: the orchestrator-style data-plane endpoint policy.
data "aws_iam_policy_document" "mcp_runtime_vpce_resource" {
  count = local.network_private && !local.gateway_enabled ? 1 : 0

  statement {
    sid       = "AllowInvokeViaApprovedDataPlaneEndpoint"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceVpce"
      values   = local.dataplane_vpce_ids
    }

    dynamic "condition" {
      for_each = local.auth_is_none ? [1] : []
      content {
        test     = "StringEquals"
        variable = "aws:PrincipalAccount"
        values   = [local.account_id]
      }
    }
  }

  statement {
    sid       = "DenyRuntimeActionsNotViaApprovedDataPlaneEndpoint"
    effect    = "Deny"
    actions   = local.runtime_policy_actions
    resources = [aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceVpce"
      values   = local.dataplane_vpce_ids
    }
  }
}

resource "aws_bedrockagentcore_resource_policy" "mcp_runtime_vpce" {
  count        = local.network_private && !local.gateway_enabled ? 1 : 0
  resource_arn = aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn
  policy       = data.aws_iam_policy_document.mcp_runtime_vpce_resource[0].json

  depends_on = [terraform_data.network_guards]
}

# Memory (only with enable_memory): data-plane actions only through the data-plane endpoint, so
# Terraform's control-plane Get/Update/DeleteMemory from a workstation are not blocked.
# aws:ViaAWSService=false follows the AWS DenyAllExceptVPC example so AgentCore's own strategy
# processing (forward access sessions) is not caught.
data "aws_iam_policy_document" "memory_resource" {
  count = local.network_private && local.memory_enabled ? 1 : 0

  statement {
    sid       = "DenyDataPlaneNotViaEndpoint"
    effect    = "Deny"
    actions   = local.memory_dataplane_actions
    resources = [aws_bedrockagentcore_memory.this[0].arn]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceVpce"
      values   = local.dataplane_vpce_ids
    }

    condition {
      test     = "Bool"
      variable = "aws:ViaAWSService"
      values   = ["false"]
    }
  }

  # Shared data-plane endpoint only (a reused bedrock-agentcore endpoint or
  # additional_dataplane_vpce_ids): the endpoint ID no longer separates companions, and every
  # runtime role in the account holds bedrock-agentcore:* (SD-21). Deny every principal except
  # this companion's runtime role, its memory role and the knowledge admins. aws:PrincipalArn of
  # an assumed-role session is the role ARN. A fork that adds another caller role must list it
  # here (docs/SECURITY.md SD-27).
  dynamic "statement" {
    for_each = local.dataplane_endpoint_shared ? [1] : []
    content {
      sid       = "DenyOtherPrincipalsOnSharedEndpoint"
      effect    = "Deny"
      actions   = local.memory_dataplane_actions
      resources = [aws_bedrockagentcore_memory.this[0].arn]

      principals {
        type        = "AWS"
        identifiers = ["*"]
      }

      condition {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = concat([aws_iam_role.runtime_exec.arn, aws_iam_role.memory_exec[0].arn], local.knowledge_admin_arns_effective)
      }

      condition {
        test     = "Bool"
        variable = "aws:ViaAWSService"
        values   = ["false"]
      }
    }
  }
}

resource "aws_bedrockagentcore_resource_policy" "memory" {
  count        = local.network_private && local.memory_enabled ? 1 : 0
  resource_arn = aws_bedrockagentcore_memory.this[0].arn
  policy       = data.aws_iam_policy_document.memory_resource[0].json

  depends_on = [terraform_data.network_guards]
}

# Readiness aggregator: every front-door file (frontdoor_config.tf) waits for every private-mode
# policy, so nothing is written for distribution before the boundary is attached. Zero instances
# in public mode.
resource "terraform_data" "private_access_policies" {
  count = local.network_private ? 1 : 0
  input = "attached"

  depends_on = [
    aws_bedrockagentcore_resource_policy.gateway,
    aws_bedrockagentcore_resource_policy.orchestrator,
    aws_bedrockagentcore_resource_policy.mcp_runtime,
    aws_bedrockagentcore_resource_policy.mcp_runtime_vpce,
    aws_bedrockagentcore_resource_policy.memory,
    aws_s3_bucket_policy.kb,
    aws_ecr_repository_policy.orchestrator,
    aws_ecr_repository_policy.mcp_runtime,
  ]
}

locals {
  # Endpoint key -> policy JSON. Keys without an entry use the AWS default policy.
  endpoint_policies = local.network_private ? merge(
    local.gateway_enabled ? { "bedrock-agentcore.gateway" = data.aws_iam_policy_document.vpce_gateway[0].json } : {},
    { "bedrock-agentcore" = data.aws_iam_policy_document.vpce_agentcore[0].json },
    { "bedrock-runtime" = data.aws_iam_policy_document.vpce_bedrock_runtime[0].json },
    { "comprehend" = data.aws_iam_policy_document.vpce_comprehend[0].json },
  ) : {}
}
