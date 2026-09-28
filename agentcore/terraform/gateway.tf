# AgentCore Gateway — NATIVE Terraform resources (opt-in via deploy_gateway).
# ---------------------------------------------------------------------------
# The target topology: front door -> AgentCore Gateway (validates the Cognito JWT) ->
# mcp_runtime. The hashicorp/aws provider (v6.66+) ships NATIVE gateway resources, so the
# gateway stays INSIDE Terraform — one IaC system, no CLI dependency.
#
# Three resources, created only when deploy_gateway=true (guards.tf refuses auth_mode="none"):
#   1. aws_bedrockagentcore_gateway              — protocol MCP + CUSTOM_JWT inbound authorizer
#   2. aws_bedrockagentcore_oauth2_credential_provider — the OUTBOUND M2M (Cognito client_credentials)
#   3. aws_bedrockagentcore_gateway_target       — mcp_server -> the mcp_runtime, authenticated
#                                                  to the runtime via the M2M provider
#
# GOVERNANCE (Tenet 1/3): this creates a governed TOOL SURFACE, not a deployment of a customer
# environment. The single target is the read/write-knowledge-only engine; systems-of-record
# targets stay read-only and are added separately.

locals {
  gateway_enabled = var.deploy_gateway

  # The mcp_runtime's MCP endpoint URL (the gateway's upstream MCP server). AgentCore serves
  # the runtime at the invocations URL with the URL-encoded runtime ARN; the MCP runtime speaks
  # streamable-HTTP /mcp there. Qualifier DEFAULT selects the current version.
  mcp_runtime_endpoint = local.gateway_enabled ? format(
    "https://bedrock-agentcore.%s.amazonaws.com/runtimes/%s/invocations?qualifier=DEFAULT",
    local.region,
    urlencode(aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn),
  ) : ""
}

# ---- Gateway execution role (the gateway assumes this to reach its targets) --------------
data "aws_iam_policy_document" "gateway_assume" {
  count = local.gateway_enabled ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:*"]
    }
  }
}

# kics-scan ignore-line: "IAM Access Analyzer Not Enabled" is an account/org baseline control
# made opt-in here via enable_access_analyzer (access_analyzer.tf) — docs/SECURITY.md SD-8.
resource "aws_iam_role" "gateway_exec" {
  count              = local.gateway_enabled ? 1 : 0
  name               = "${var.name_prefix}-gateway-exec-role"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume[0].json
}

# The gateway invokes the mcp_runtime and reads the M2M credential provider's secret.
data "aws_iam_policy_document" "gateway_exec_inline" {
  count = local.gateway_enabled ? 1 : 0

  statement {
    sid     = "InvokeMcpRuntime"
    effect  = "Allow"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = [
      aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn,
      "${aws_bedrockagentcore_agent_runtime.mcp_runtime.agent_runtime_arn}/*",
    ]
  }
  statement {
    # Resource = "*" is REQUIRED here, not lax: GetWorkloadAccessToken / GetResourceOauth2Token
    # are workload-identity token-minting operations that AgentCore does not support scoping to
    # a resource ARN (there is no per-resource ARN to name — the token is minted for the calling
    # workload identity). The AWS MCP-gateway IAM reference grants these the same way. Scoping is
    # instead enforced by (a) this being the gateway's OWN least-privilege exec role, and (b) the
    # role trust policy restricting who can assume it. checkov CKV_AWS_356/CKV_AWS_111 flag the
    # wildcard generically; it is unavoidable for these two actions.
    sid       = "GatewayWorkloadToken"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourceOauth2Token"]
    resources = ["*"]
  }

  # Read the AgentCore Identity SERVICE-MANAGED secret that stores the outbound M2M
  # (oauth2 credential provider) client secret. When the gateway forwards a tools/call it
  # mints the outbound token by reading this secret. AgentCore Identity names these secrets
  # `bedrock-agentcore-identity!default/oauth2/*`; scope the grant to that prefix (the AWS
  # MCP-server-target IAM example grants the same).
  dynamic "statement" {
    for_each = local.auth_is_cognito ? [1] : []
    content {
      sid     = "OutboundOauthSecret"
      effect  = "Allow"
      actions = ["secretsmanager:GetSecretValue"]
      resources = [
        "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:bedrock-agentcore-identity!default/oauth2/*",
      ]
    }
  }

  # Invoke the identity interceptor Lambda (ADR-0001 Phase 2). The gateway calls the interceptor
  # using its OWN execution role (SigV4), so the exec role — not just the Lambda's resource
  # policy — must allow lambda:InvokeFunction on the interceptor. Scoped to the one interceptor
  # function; only when it is enabled.
  dynamic "statement" {
    for_each = local.interceptor_enabled ? [1] : []
    content {
      sid       = "InvokeIdentityInterceptor"
      effect    = "Allow"
      actions   = ["lambda:InvokeFunction"]
      resources = [aws_lambda_function.identity_interceptor[0].arn]
    }
  }
}

resource "aws_iam_role_policy" "gateway_exec_inline" {
  count  = local.gateway_enabled ? 1 : 0
  name   = "${var.name_prefix}-gateway-exec-policy"
  role   = aws_iam_role.gateway_exec[0].id
  policy = data.aws_iam_policy_document.gateway_exec_inline[0].json
}

# ---- 1. The gateway (protocol MCP + CUSTOM_JWT inbound) ----------------------------------
resource "aws_bedrockagentcore_gateway" "this" {
  count = local.gateway_enabled ? 1 : 0
  # Gateway name regex allows hyphens but NOT underscores (unlike the runtime resources).
  name     = "${var.name_prefix}-gateway"
  role_arn = aws_iam_role.gateway_exec[0].arn
  # MCP is the ONLY valid gateway protocol (GatewayProtocolType enum = ["MCP"]). The gateway
  # brokers to an upstream MCP server target (the mcp_runtime), below.
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"
  description     = "Governed companion gateway — front door -> mcp_runtime, JWT-authorized"

  # Surface the REAL upstream error instead of the sanitized "An internal error occurred."
  # The gateway's DEFAULT (attribute unset) redacts client-facing errors; "DEBUG" makes it
  # return the actual forwarding/tool-call failure reason. The API accepts only "DEBUG" or
  # unset, so we pass null when var.gateway_exception_level is empty (production default).
  exception_level = var.gateway_exception_level != "" ? var.gateway_exception_level : null

  # Constrain the MCP protocol versions the gateway negotiates with the target. Include both
  # observed versions so the gateway accepts whichever the live tools/call hop uses; pinning to
  # one risks "Unsupported protocol version". Leaving unset lets the gateway pick its newest,
  # whose session contract may differ.
  dynamic "protocol_configuration" {
    for_each = length(var.gateway_mcp_supported_versions) > 0 ? [1] : []
    content {
      mcp {
        supported_versions = var.gateway_mcp_supported_versions
      }
    }
  }

  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = local.jwt_discovery_url
      allowed_clients = local.jwt_allowed_clients
    }
  }

  # Identity interceptor (ADR-0001 Phase 2): a REQUEST interceptor Lambda derives the verified
  # builder subject from the inbound JWT and injects it into the forwarded request, so the
  # runtime can attribute per-builder memory to a real actor. pass_request_headers=true so the
  # interceptor receives the inbound Authorization header. Created only when enabled.
  dynamic "interceptor_configuration" {
    for_each = local.interceptor_enabled ? [1] : []
    content {
      interception_points = ["REQUEST"]
      input_configuration {
        pass_request_headers = true
      }
      interceptor {
        lambda {
          arn = aws_lambda_function.identity_interceptor[0].arn
        }
      }
    }
  }

  tags = var.tags

  depends_on = [terraform_data.auth_guards]
}

# ---- 2. Outbound M2M credential provider (Cognito client_credentials) --------------------
# The gateway->runtime hop authenticates with the Cognito M2M client. This native credential
# provider receives the client_id/secret directly and manages the credential in the AgentCore
# Identity Token Vault — so no separate, hand-managed Secrets Manager secret is needed (see
# secrets.tf). Created only for the cognito gateway path.
resource "aws_bedrockagentcore_oauth2_credential_provider" "gateway_m2m" {
  count                      = local.gateway_enabled && local.auth_is_cognito ? 1 : 0
  name                       = "${var.name_prefix}-gateway-m2m"
  credential_provider_vendor = "CustomOauth2"

  oauth2_provider_config {
    custom_oauth2_provider_config {
      client_id     = aws_cognito_user_pool_client.m2m[0].id
      client_secret = aws_cognito_user_pool_client.m2m[0].client_secret

      oauth_discovery {
        discovery_url = local.jwt_discovery_url
      }
    }
  }
}

# ---- 3. Gateway target: mcp_server -> the mcp_runtime, via the M2M credential ------------
resource "aws_bedrockagentcore_gateway_target" "mcp_engine" {
  count              = local.gateway_enabled ? 1 : 0
  gateway_identifier = aws_bedrockagentcore_gateway.this[0].gateway_id
  # Short target name -> gateway tools are namespaced "gac___<Name>". The gateway ALWAYS
  # prefixes tools with <target>___.
  name        = "gac"
  description = "The governed companion engine (mcp_runtime), reached over the JWT-authorized gateway"

  # An MCP gateway brokers to an upstream MCP SERVER by URL. Our mcp_runtime IS a stateless MCP
  # server, so we point an mcp_server target at its /mcp invocations URL.
  target_configuration {
    mcp {
      mcp_server {
        endpoint = local.mcp_runtime_endpoint
      }
    }
  }

  # Forward the interceptor-injected verified-actor header to the runtime (ADR-0001 Phase 2).
  # The identity interceptor sets X-Gac-Actor-Sub from the verified JWT; the runtime reads it
  # as the authoritative builder actor. Only when the interceptor is enabled. (Note: NOT
  # Mcp-Session-Id — that is gateway-managed, not header-propagated.)
  dynamic "metadata_configuration" {
    for_each = local.interceptor_enabled ? [1] : []
    content {
      allowed_request_headers = ["X-Gac-Actor-Sub"]
    }
  }

  # Outbound auth to the runtime: the Cognito M2M client_credentials grant, via the provider.
  # When not on the cognito path, fall back to the gateway's IAM role (SigV4).
  dynamic "credential_provider_configuration" {
    for_each = local.auth_is_cognito ? [1] : []
    content {
      oauth {
        provider_arn = aws_bedrockagentcore_oauth2_credential_provider.gateway_m2m[0].credential_provider_arn
        grant_type   = "CLIENT_CREDENTIALS"
        scopes       = ["${local.m2m_resource_server}/${local.m2m_scope_name}"]
      }
    }
  }
  dynamic "credential_provider_configuration" {
    for_each = local.auth_is_cognito ? [] : [1]
    content {
      gateway_iam_role {}
    }
  }

  # The runtime's authorizer must already allow the M2M client before the target's tool-fetch
  # handshake runs, or the connect fails with "Authorization error". Force that ordering.
  depends_on = [aws_bedrockagentcore_agent_runtime.mcp_runtime]
}

locals {
  # The gateway URL for outputs / front-door config generation.
  gateway_url = local.gateway_enabled ? try(aws_bedrockagentcore_gateway.this[0].gateway_url, "") : ""
}
