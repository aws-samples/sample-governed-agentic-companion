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
#
# K9: KICS "S3 bucket notifications disabled" (query e39f87f5) flags every Lambda function and
# SQS queue in a file that has no aws_s3_bucket_notification. None of the functions or queues in
# this file is an S3 notification target; the kit's S3 buckets enable EventBridge notifications
# (s3_kb.tf). Disabled for this file by query id; every other KICS query still runs.
# kics-scan disable=e39f87f5-0abf-488b-864c-63ee1f588140

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

# ---- Customer managed key for the interceptor's environment variables (checkov CKV_AWS_173) --
# The environment carries the entitlement group and claim name (not secrets), encrypted with this
# key rather than suppressing the check (same pattern as the probe key, probe.tf). Lambda uses
# the key only for this function: kms:ViaService plus the aws:lambda:FunctionArn encryption
# context (https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars-encryption.html).
# The function ARN is composed from its name, so the key policy does not depend on the function.
locals {
  interceptor_function_name = "${var.name_prefix}-gateway-identity-interceptor"
  interceptor_function_arn  = "arn:aws:lambda:${local.region}:${local.account_id}:function:${local.interceptor_function_name}"
}

resource "aws_kms_key" "interceptor" {
  count                   = local.interceptor_enabled ? 1 : 0
  description             = "${local.interceptor_function_name} environment encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  # Inline key policy, like the KB, ECR and probe keys. In a key policy Resource "*" means this key.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "KeyAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action = [
          "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*",
          "kms:Update*", "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*",
          "kms:TagResource", "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
        ]
        Resource = "*"
      },
      {
        # The deployer encrypts when configuring the function and the execution role decrypts at
        # init, both through Lambda and only for this function; IAM decides who.
        Sid       = "LambdaCryptoForThisFunction"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService"                               = "lambda.${local.region}.amazonaws.com"
            "kms:EncryptionContext:aws:lambda:FunctionArn" = local.interceptor_function_arn
          }
        }
      },
      {
        # Key lookup and the grant Lambda creates for the function carry no encryption context;
        # both stay limited to calls made through Lambda (grants for an AWS resource only).
        Sid       = "LambdaKeySetupViaService"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = ["kms:CreateGrant", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "lambda.${local.region}.amazonaws.com" }
          BoolIfExists = { "kms:GrantIsForAWSResource" = "true" }
        }
      },
    ]
  })
  tags = var.tags
}

resource "aws_kms_alias" "interceptor" {
  count         = local.interceptor_enabled ? 1 : 0
  name          = "alias/${var.name_prefix}-interceptor"
  target_key_id = aws_kms_key.interceptor[0].key_id
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
      },
      {
        Sid      = "EnvKeyDecryptCeiling"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.interceptor[0].arn
      }
    ]
  })
}

# Decrypt the environment at init: this key only (CKV_AWS_173 fix, see the key above).
resource "aws_iam_role_policy" "interceptor_env_key" {
  count = local.interceptor_enabled ? 1 : 0
  name  = "${var.name_prefix}-gateway-interceptor-env-key"
  role  = aws_iam_role.interceptor_exec[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "EnvKeyDecrypt"
      Effect   = "Allow"
      Action   = ["kms:Decrypt"]
      Resource = aws_kms_key.interceptor[0].arn
    }]
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
# Reviewed scanner dispositions (documented tradeoffs, docs/SECURITY.md SD-6/SD-9, inline below so
# Checkov honors them; see the rationale block at the end of this resource).
resource "aws_lambda_function" "identity_interceptor" {
  # checkov:skip=CKV_AWS_117: the interceptor makes no network calls (it reshapes a header from an already-verified JWT); a VPC adds ENIs, EC2 ENI permissions on Resource "*" and cold-start latency for no security gain (SD-6).
  # checkov:skip=CKV_AWS_272: built from single-file source in this repo via archive_file and pinned by source_code_hash; Lambda code signing needs an AWS Signer profile and signed S3 artifacts, which the zip-upload path does not use (SD-9).
  count            = local.interceptor_enabled ? 1 : 0
  function_name    = local.interceptor_function_name
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
  # Customer managed key for the environment (CKV_AWS_173), set whether or not the entitlement
  # env is present, so the key never flips with require_entitlement_group.
  kms_key_arn = aws_kms_key.interceptor[0].arn

  # Entitlement defence in depth (D26.3): when the group is enforced, the interceptor re-checks
  # the group claim and fails closed (403 "GAC: not entitled"). Unset = today's behaviour.
  dynamic "environment" {
    for_each = local.entitlement_enforced ? [1] : []
    content {
      variables = {
        GAC_REQUIRED_GROUP       = local.entitlement_group
        GAC_REQUIRED_GROUP_CLAIM = var.entitlement_claim_name
      }
    }
  }

  # Dead-letter target (checkov CKV_AWS_116). The gateway invokes this interceptor
  # SYNCHRONOUSLY, so a DLQ is not on the failure path in practice — but wiring an encrypted
  # SQS DLQ satisfies the control and provides a home for any future async invoke.
  dead_letter_config {
    target_arn = aws_sqs_queue.interceptor_dlq[0].arn
  }

  # The role can decrypt the environment before the first invoke.
  depends_on = [aws_iam_role_policy.interceptor_env_key]

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
