# Gateway identity interceptor Lambda — verified caller-identity propagation (ADR-0001 Phase 2).
# ---------------------------------------------------------------------------------------------
# Turns per-builder memory from FAIL-CLOSED (Phase 1 refuses) back ON, safely: a REQUEST
# interceptor on the MCP gateway derives the builder's subject from the ALREADY-VERIFIED inbound
# JWT and injects it (as the X-Gac-Actor-Sub header + params._meta) into the request the gateway
# forwards to the runtime. The runtime reads the header as the authoritative actor. See
# agentcore/interceptor/identity_interceptor.py and ADR-0001.
#
# Created only when deploy_gateway=true AND enable_identity_interceptor=true (default true when
# the gateway is on). Least-privilege by construction: the Lambda has only basic-execution
# logging + X-Ray; its resource policy allows ONLY the gateway service principal (scoped to this
# gateway's ARN) to invoke it, so it is not a general-purpose endpoint.

locals {
  interceptor_enabled = local.gateway_enabled && var.enable_identity_interceptor
}

# ---- Package the single-file Lambda from the repo (no external build step) ----------------
data "archive_file" "identity_interceptor" {
  count       = local.interceptor_enabled ? 1 : 0
  type        = "zip"
  source_file = "${local.repo_root}/agentcore/interceptor/identity_interceptor.py"
  output_path = "${path.module}/.identity_interceptor.zip"
}

# ---- Execution role: basic logging + X-Ray ONLY (no data-plane access) --------------------
data "aws_iam_policy_document" "interceptor_assume" {
  count = local.interceptor_enabled ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "interceptor_exec" {
  count              = local.interceptor_enabled ? 1 : 0
  name               = "${var.name_prefix}-gateway-interceptor-role"
  assume_role_policy = data.aws_iam_policy_document.interceptor_assume[0].json
  # A permission boundary caps this role's maximum privilege (WAF Agentic AI Lens AGENTSEC03) —
  # it can never exceed CloudWatch Logs / X-Ray / DLQ writes even if its inline policy is later
  # widened.
  permissions_boundary = aws_iam_policy.interceptor_boundary[0].arn
}

# Permission boundary: the CEILING for the interceptor role.
resource "aws_iam_policy" "interceptor_boundary" {
  count = local.interceptor_enabled ? 1 : 0
  name  = "${var.name_prefix}-gateway-interceptor-boundary"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "LogsCeiling"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:*"
      },
      {
        Sid      = "XRayCeiling"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Sid      = "DlqCeiling"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = "arn:aws:sqs:${local.region}:${local.account_id}:${var.name_prefix}-gateway-interceptor-dlq"
      }
    ]
  })
}

resource "aws_iam_role_policy" "interceptor_logs" {
  count = local.interceptor_enabled ? 1 : 0
  name  = "${var.name_prefix}-gateway-interceptor-logs"
  role  = aws_iam_role.interceptor_exec[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${var.name_prefix}-gateway-identity-interceptor:*"
      },
      {
        # X-Ray active tracing (CKV_AWS_50). PutTraceSegments/PutTelemetryRecords are not
        # resource-scopable, so "*" is expected and matches the AWS-managed
        # AWSXRayDaemonWriteAccess policy.
        Sid      = "XRayTracing"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      }
    ]
  })
}

# ---- The interceptor Lambda ---------------------------------------------------------------
# Reviewed scanner dispositions (documented tradeoffs — see docs/SECURITY.md SD-6/SD-7 and the
# rationale block at the end of this resource):
#   checkov:skip=CKV_AWS_117: the interceptor makes no data-plane calls (it only reshapes a
#     request header from an already-verified JWT), so a VPC adds ENI cold-start latency for no
#     security gain.
#   checkov:skip=CKV_AWS_272: the function is built from single-file source in THIS repo via
#     archive_file + source_code_hash (integrity already pinned); a Signer profile is
#     disproportionate for a starter kit.
# kics-scan ignore-block: the "S3 bucket notifications disabled" query mis-attributes to this
#   Lambda/SQS pair — neither is an S3 bucket (the actual KB buckets DO enable EventBridge).
resource "aws_lambda_function" "identity_interceptor" {
  count            = local.interceptor_enabled ? 1 : 0
  function_name    = "${var.name_prefix}-gateway-identity-interceptor"
  role             = aws_iam_role.interceptor_exec[0].arn
  runtime          = "python3.12"
  handler          = "identity_interceptor.lambda_handler"
  filename         = data.archive_file.identity_interceptor[0].output_path
  source_code_hash = data.archive_file.identity_interceptor[0].output_base64sha256
  timeout          = 5
  memory_size      = 128
  description      = "Governed companion gateway REQUEST interceptor — injects the verified builder subject (ADR-0001 Phase 2)"
  tags             = var.tags

  # Cheap, zero-tradeoff hardening (checkov CKV_AWS_50 / CKV_AWS_115):
  # X-Ray active tracing for the request-path hop, and a reserved-concurrency cap so a
  # traffic spike on this interceptor cannot exhaust account-wide Lambda concurrency.
  tracing_config {
    mode = "Active"
  }
  reserved_concurrent_executions = var.interceptor_reserved_concurrency

  # Dead-letter target (checkov CKV_AWS_116). The gateway invokes this interceptor
  # SYNCHRONOUSLY, so a DLQ is not on the failure path in practice — but wiring an encrypted
  # SQS DLQ satisfies the control and provides a home for any future async invoke.
  dead_letter_config {
    target_arn = aws_sqs_queue.interceptor_dlq[0].arn
  }

  # Deliberately NOT configured, with rationale (documented rather than cargo-culted):
  #   - VPC (CKV_AWS_117): the interceptor only reshapes a request header/body from an
  #     already-verified JWT; it makes no data-plane calls, so a VPC adds ENI cold-start
  #     latency for no security gain.
  #   - Code signing (CKV_AWS_272): the function is built from single-file source in THIS
  #     repo via archive_file + source_code_hash (integrity already pinned); a Signer profile
  #     is disproportionate for a starter kit.
}

# Encrypted SQS DLQ for the interceptor (CKV_AWS_116). SSE-managed encryption; the exec role
# is granted SendMessage so a failed async invoke can be captured.
# kics-scan ignore-block: the "S3 bucket notifications disabled" query mis-attributes to this
# SQS queue — it is not an S3 bucket (the actual KB buckets DO enable EventBridge notifications).
resource "aws_sqs_queue" "interceptor_dlq" {
  count                     = local.interceptor_enabled ? 1 : 0
  name                      = "${var.name_prefix}-gateway-interceptor-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_iam_role_policy" "interceptor_dlq" {
  count = local.interceptor_enabled ? 1 : 0
  name  = "${var.name_prefix}-gateway-interceptor-dlq"
  role  = aws_iam_role.interceptor_exec[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.interceptor_dlq[0].arn
    }]
  })
}

# Resource policy: ONLY the AgentCore gateway service principal may invoke this Lambda, scoped
# to THIS gateway's ARN (so it is not a callable endpoint for anything else).
resource "aws_lambda_permission" "interceptor_gateway_invoke" {
  count         = local.interceptor_enabled ? 1 : 0
  statement_id  = "AllowAgentCoreGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.identity_interceptor[0].function_name
  principal     = "bedrock-agentcore.amazonaws.com"
  source_arn    = aws_bedrockagentcore_gateway.this[0].gateway_arn
}
