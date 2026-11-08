# Optional private access probe (enable_private_access_probe, default false). TEST STAND-IN.
# ---------------------------------------------------------------------------
# A small Python Lambda function in the private subnets that stands in for a builder workstation
# on AWS Direct Connect or VPN with private DNS. It resolves the gateway hostname, POSTs JSON-RPC
# to the gateway /mcp route (initialize, tools/list, tools/call of gac___companion_kb with a fixed
# question), and optionally calls the orchestrator runtime through the data-plane endpoint like
# the stdio bridge. An opt-in diagram mode ({token, diagram}) calls gac___draw_diagram (fixed in
# GAC_PROBE_DIAGRAM_TOOL_NAME) and returns the artefact envelope for
# `./run.sh diagram-fetch --via-probe`; same role, network and timeout (no IAM change).
# Source: agentcore/probe/private_access_probe.py.
#
# The caller passes their bearer token in the invocation payload at run time; it is never stored,
# logged or returned (enforced by tests/test_private_access_probe.py). No ingress, no function
# URL, no resource-based policy, no internet route. Network reach: HTTPS to the CLIENT endpoint
# security group only (gateway and data-plane endpoints), never to the runtime-only endpoints.
# Lambda delivers function logs and trace segments outside the execution environment, so the
# probe needs no path to the Logs or X-Ray endpoints.
#
# How a real workstation differs, and why to disable this after testing: docs/DEPLOY.md
# "Private access probe". Security notes: docs/SECURITY.md SD-25.
#
# K9: KICS "S3 bucket notifications disabled" (query e39f87f5) flags every Lambda function and
# SQS queue in a file that has no aws_s3_bucket_notification. None of the functions or queues in
# this file is an S3 notification target; the kit's S3 buckets enable EventBridge notifications
# (s3_kb.tf). Disabled for this file by query id; every other KICS query still runs.
# kics-scan disable=e39f87f5-0abf-488b-864c-63ee1f588140

locals {
  probe_enabled = var.enable_private_access_probe && local.network_private && local.gateway_enabled
  probe_name    = "${var.name_prefix}-private-access-probe"
  # Built from parts (not the resource attribute) so the role policy does not depend on the
  # function. Unqualified ARN, as Lambda injects it in lambda:SourceFunctionArn.
  probe_function_arn = "arn:aws:lambda:${local.region}:${local.account_id}:function:${local.probe_name}"
  probe_vpc_cidr     = local.create_vpc ? var.vpc_cidr : try(data.aws_vpc.existing[0].cidr_block, "")
  # Built from parts, like probe_function_arn, so the key policy and the role policy do not
  # depend on the resources they protect.
  probe_log_group_name = "/aws/lambda/${local.probe_name}"
  probe_log_group_arn  = "arn:aws:logs:${local.region}:${local.account_id}:log-group:${local.probe_log_group_name}"
  probe_dlq_name       = "${local.probe_name}-dlq"
  probe_dlq_arn        = "arn:aws:sqs:${local.region}:${local.account_id}:${local.probe_dlq_name}"

  # One statement list shared by the inline policy and the permission boundary, so the ceiling
  # cannot drift from the grant.
  probe_statements = [
    {
      # Writes to the probe's own log group only. lambda:SourceFunctionArn is injected by Lambda
      # for its own log delivery, so leaked role credentials used elsewhere cannot write here. No
      # CreateLogGroup: Terraform creates the group.
      sid       = "ProbeLogs"
      actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      resources = ["${local.probe_log_group_arn}:*"]
      condition = { test = "ArnEquals", variable = "lambda:SourceFunctionArn", values = [local.probe_function_arn] }
    },
    {
      # Required by Lambda for a function in a VPC; these EC2 actions do not support
      # resource-level permissions, so Resource "*" (docs/SECURITY.md SD-25).
      sid = "VpcEni"
      actions = [
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeSubnets",
        "ec2:DeleteNetworkInterface",
        "ec2:AssignPrivateIpAddresses",
        "ec2:UnassignPrivateIpAddresses",
      ]
      resources = ["*"]
      condition = null
    },
    {
      # Active tracing. Not resource-scopable, so Resource "*" (same as the interceptor).
      sid       = "XRayTracing"
      actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
      resources = ["*"]
      condition = null
    },
    {
      # Dead-letter queue (checkov CKV_AWS_116), the probe's own queue only.
      sid       = "ProbeDeadLetterQueue"
      actions   = ["sqs:SendMessage"]
      resources = [local.probe_dlq_arn]
      condition = null
    },
  ]
}

# ---- Customer managed key for the probe's environment variables and log group --------------
# (checkov CKV_AWS_173 / CKV_AWS_158). Lambda creates a grant on the key with the deployer's
# permissions, so the execution role needs no KMS permission
# (https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars-encryption.html).
# CloudWatch Logs uses the key only for this log group (encryption-context condition,
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html).
resource "aws_kms_key" "probe" {
  count                   = local.probe_enabled ? 1 : 0
  description             = "${local.probe_name} environment and log encryption (test stand-in)"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  # Inline key policy, like the KB and ECR keys. In a key policy Resource "*" means this key.
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
        # The deployer configures the key on the function through Lambda; IAM decides who.
        Sid       = "LambdaUseViaIam"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:CreateGrant", "kms:DescribeKey"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "lambda.${local.region}.amazonaws.com" } }
      },
      {
        Sid       = "CloudWatchLogsUse"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = { ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = local.probe_log_group_arn } }
      },
    ]
  })
}

resource "aws_kms_alias" "probe" {
  count         = local.probe_enabled ? 1 : 0
  name          = "alias/${local.probe_name}"
  target_key_id = aws_kms_key.probe[0].key_id
}

# Encrypted SQS dead-letter queue (checkov CKV_AWS_116). The probe is invoked synchronously, so
# the queue stays empty in practice; it captures a failed asynchronous invoke if one is ever made.
resource "aws_sqs_queue" "probe_dlq" {
  count                     = local.probe_enabled ? 1 : 0
  name                      = local.probe_dlq_name
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

# ---- Package the single-file function from the repo ----------------------------------------
data "archive_file" "private_access_probe" {
  count       = local.probe_enabled ? 1 : 0
  type        = "zip"
  source_file = "${local.repo_root}/agentcore/probe/private_access_probe.py"
  output_path = "${path.module}/.private_access_probe.zip"
}

# ---- IAM: logs on its own group, VPC ENI and X-Ray only, under a permission boundary ----------
data "aws_iam_policy_document" "probe_assume" {
  count = local.probe_enabled ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "probe" {
  count = local.probe_enabled ? 1 : 0

  dynamic "statement" {
    for_each = local.probe_statements
    content {
      sid       = statement.value.sid
      effect    = "Allow"
      actions   = statement.value.actions
      resources = statement.value.resources

      dynamic "condition" {
        for_each = statement.value.condition == null ? [] : [statement.value.condition]
        content {
          test     = condition.value.test
          variable = condition.value.variable
          values   = condition.value.values
        }
      }
    }
  }
}

data "aws_iam_policy_document" "probe_boundary" {
  count = local.probe_enabled ? 1 : 0

  dynamic "statement" {
    for_each = local.probe_statements
    content {
      sid       = "${statement.value.sid}Ceiling"
      effect    = "Allow"
      actions   = statement.value.actions
      resources = statement.value.resources

      dynamic "condition" {
        for_each = statement.value.condition == null ? [] : [statement.value.condition]
        content {
          test     = condition.value.test
          variable = condition.value.variable
          values   = condition.value.values
        }
      }
    }
  }
}

resource "aws_iam_policy" "probe_boundary" {
  count  = local.probe_enabled ? 1 : 0
  name   = "${local.probe_name}-boundary"
  policy = data.aws_iam_policy_document.probe_boundary[0].json
}

resource "aws_iam_role" "probe" {
  count                = local.probe_enabled ? 1 : 0
  name                 = "${local.probe_name}-role"
  assume_role_policy   = data.aws_iam_policy_document.probe_assume[0].json
  permissions_boundary = aws_iam_policy.probe_boundary[0].arn
}

resource "aws_iam_role_policy" "probe" {
  count  = local.probe_enabled ? 1 : 0
  name   = "${local.probe_name}-policy"
  role   = aws_iam_role.probe[0].id
  policy = data.aws_iam_policy_document.probe[0].json
}

# ---- Log group (created by Terraform, so the role needs no CreateLogGroup) -----------------
resource "aws_cloudwatch_log_group" "probe" {
  count             = local.probe_enabled ? 1 : 0
  name              = local.probe_log_group_name
  retention_in_days = 365 # checkov CKV_AWS_338; the probe logs only step names, statuses and timings
  kms_key_id        = aws_kms_key.probe[0].arn
}

# ---- Network: HTTPS egress to the client endpoint SG(s) only; no ingress -------------------
resource "aws_security_group" "probe" {
  count       = local.probe_enabled ? 1 : 0
  name        = local.probe_name
  description = "Private access probe (test stand-in): HTTPS egress to the AgentCore client endpoints only"
  vpc_id      = local.vpc_id_effective

  tags = {
    Name = local.probe_name
  }
}

# Kit-managed client endpoint SG (gateway and data-plane endpoints created by the module).
resource "aws_vpc_security_group_egress_rule" "probe_to_client_endpoints" {
  count                        = local.probe_enabled && contains(keys(local.vpce_sg_ids), "client") ? 1 : 0
  security_group_id            = aws_security_group.probe[0].id
  referenced_security_group_id = local.vpce_sg_ids["client"]
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Probe to AgentCore client endpoints HTTPS"
}

resource "aws_vpc_security_group_ingress_rule" "vpce_client_from_probe" {
  count                        = local.probe_enabled && contains(keys(local.vpce_sg_ids), "client") ? 1 : 0
  security_group_id            = local.vpce_sg_ids["client"]
  referenced_security_group_id = aws_security_group.probe[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Endpoint HTTPS from the private access probe (test only)"
}

# Reused client endpoints (shared network): only the CLIENT SG list, never the full
# existing_endpoint_security_group_ids (which also holds the runtime-only SG).
resource "aws_vpc_security_group_egress_rule" "probe_to_existing_endpoints" {
  for_each                     = local.probe_enabled ? toset(var.existing_client_endpoint_security_group_ids) : toset([])
  security_group_id            = aws_security_group.probe[0].id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Probe to reused AgentCore client endpoints HTTPS"
}

resource "aws_vpc_security_group_ingress_rule" "existing_endpoint_from_probe" {
  for_each                     = local.probe_enabled && var.manage_existing_endpoint_ingress ? toset(var.existing_client_endpoint_security_group_ids) : toset([])
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.probe[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Endpoint HTTPS from the private access probe (test only)"
}

# ---- The probe function ---------------------------------------------------------------------
resource "aws_lambda_function" "private_access_probe" {
  # checkov:skip=CKV_AWS_272: built from single-file source in this repo via archive_file and pinned by source_code_hash; Lambda code signing needs an AWS Signer profile and signed S3 artifacts, which the zip-upload path does not use (SD-9).
  count            = local.probe_enabled ? 1 : 0
  function_name    = local.probe_name
  role             = aws_iam_role.probe[0].arn
  runtime          = "python3.12"
  handler          = "private_access_probe.lambda_handler"
  filename         = data.archive_file.private_access_probe[0].output_path
  source_code_hash = data.archive_file.private_access_probe[0].output_base64sha256
  timeout          = 120
  memory_size      = 256
  description      = "Governed companion private access probe (TEST STAND-IN for a DX/VPN workstation; disable after testing)"

  reserved_concurrent_executions = var.private_access_probe_reserved_concurrency
  kms_key_arn                    = aws_kms_key.probe[0].arn

  dead_letter_config {
    target_arn = aws_sqs_queue.probe_dlq[0].arn
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
    log_group             = aws_cloudwatch_log_group.probe[0].name
  }

  vpc_config {
    subnet_ids         = local.private_subnet_ids_effective
    security_group_ids = [aws_security_group.probe[0].id]
  }

  environment {
    variables = {
      GAC_PROBE_GATEWAY_URL = local.gateway_url
      GAC_PROBE_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.orchestrator.agent_runtime_arn
      GAC_PROBE_REGION      = local.region
      GAC_PROBE_VPC_CIDR    = local.probe_vpc_cidr
      GAC_PROBE_TOOL_NAME   = "gac___companion_kb"
      # Diagram mode ({token, diagram}): the only tool it calls, fixed here, never caller-chosen.
      GAC_PROBE_DIAGRAM_TOOL_NAME = "gac___draw_diagram"
    }
  }

  # Not created before its log group, its permissions, its network rules, and every private-mode
  # resource policy.
  depends_on = [
    aws_cloudwatch_log_group.probe,
    aws_iam_role_policy.probe,
    aws_kms_alias.probe,
    aws_vpc_security_group_egress_rule.probe_to_client_endpoints,
    aws_vpc_security_group_ingress_rule.vpce_client_from_probe,
    aws_vpc_security_group_egress_rule.probe_to_existing_endpoints,
    aws_vpc_security_group_ingress_rule.existing_endpoint_from_probe,
    terraform_data.private_access_policies,
  ]
}

locals {
  # The token is read by jq from the environment (env.GAC_GATEWAY_TOKEN), so it never appears in
  # argv or in Terraform output. umask 077 + mktemp keeps the result file (answer text, no token)
  # private, and it is removed afterwards.
  # K2: KICS "Generic Token" matches the jq field name `token` and the environment variable name
  # GAC_GATEWAY_TOKEN; no token value is in this file.
  # kics-scan ignore-line
  private_access_probe_invoke_command = local.probe_enabled ? "( umask 077; out=\"$(mktemp)\"; jq -nc '{token: env.GAC_GATEWAY_TOKEN, call_runtime: true}' | aws lambda invoke --region ${local.region} --function-name ${local.probe_name} --cli-binary-format raw-in-base64-out --payload file:///dev/stdin \"$out\" >/dev/null && jq . \"$out\"; rm -f \"$out\" )" : ""
}
