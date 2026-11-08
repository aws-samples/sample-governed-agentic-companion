# VPC Flow Logs for the VPC this module creates (create_vpc = true): the companion sandbox VPC
# and the platform's shared network (checkov CKV2_AWS_11). A VPC you bring keeps your own flow-log
# setup. ALL traffic to a CloudWatch Logs group encrypted with its own customer managed key, kept
# for a year; the delivery role can write only to that group.
# https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs-cwl.html
# https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370

data "aws_caller_identity" "current" {
  count = var.create_vpc ? 1 : 0
}

locals {
  flow_log_account_id      = try(data.aws_caller_identity.current[0].account_id, "")
  flow_log_group_name      = "/vpc/${var.name_prefix}-private/flow-logs"
  flow_log_group_arn       = "arn:aws:logs:${local.region}:${local.flow_log_account_id}:log-group:${local.flow_log_group_name}"
  flow_log_source_arn_glob = "arn:aws:ec2:${local.region}:${local.flow_log_account_id}:vpc-flow-log/*"
}

resource "aws_kms_key" "flow_logs" {
  count                   = var.create_vpc ? 1 : 0
  description             = "${var.name_prefix} VPC flow log encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  # In a key policy Resource "*" means this key.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "KeyAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.flow_log_account_id}:root" }
        Action = [
          "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*",
          "kms:Update*", "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*",
          "kms:TagResource", "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
        ]
        Resource = "*"
      },
      {
        Sid       = "CloudWatchLogsUse"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = { ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = local.flow_log_group_arn } }
      },
    ]
  })
}

resource "aws_kms_alias" "flow_logs" {
  count         = var.create_vpc ? 1 : 0
  name          = "alias/${var.name_prefix}-vpc-flow-logs"
  target_key_id = aws_kms_key.flow_logs[0].key_id
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  count             = var.create_vpc ? 1 : 0
  name              = local.flow_log_group_name
  retention_in_days = 365
  kms_key_id        = aws_kms_key.flow_logs[0].arn
}

data "aws_iam_policy_document" "flow_logs_assume" {
  count = var.create_vpc ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }

    # Confused-deputy protection: only flow logs in this account and Region.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.flow_log_account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [local.flow_log_source_arn_glob]
    }
  }
}

data "aws_iam_policy_document" "flow_logs" {
  count = var.create_vpc ? 1 : 0

  statement {
    sid       = "WriteFlowLogGroup"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${local.flow_log_group_arn}:*"]
  }

  statement {
    sid       = "DescribeLogGroups"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${local.region}:${local.flow_log_account_id}:log-group:*"]
  }
}

resource "aws_iam_role" "flow_logs" {
  count              = var.create_vpc ? 1 : 0
  name               = "${var.name_prefix}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume[0].json
}

resource "aws_iam_role_policy" "flow_logs" {
  count  = var.create_vpc ? 1 : 0
  name   = "${var.name_prefix}-vpc-flow-logs"
  role   = aws_iam_role.flow_logs[0].id
  policy = data.aws_iam_policy_document.flow_logs[0].json
}

resource "aws_flow_log" "this" {
  count                    = var.create_vpc ? 1 : 0
  vpc_id                   = aws_vpc.this[0].id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs[0].arn
  iam_role_arn             = aws_iam_role.flow_logs[0].arn
  max_aggregation_interval = 600

  tags = {
    Name = "${var.name_prefix}-private"
  }

  depends_on = [aws_iam_role_policy.flow_logs]
}
