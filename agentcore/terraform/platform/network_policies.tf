# Endpoint policies for the shared network. Account-scoped (they cannot name one companion's
# resources). For OAuth (JWT) requests the endpoint-policy principal must be "*"; the companions'
# resource policies and authorizers decide WHO may reach each companion.
# Every document is counted on var.enable_private_network.

data "aws_caller_identity" "network" {
  count = var.enable_private_network ? 1 : 0
}

data "aws_partition" "network" {
  count = var.enable_private_network ? 1 : 0
}

data "aws_region" "network" {
  count = var.enable_private_network ? 1 : 0
}

locals {
  net_account   = one(data.aws_caller_identity.network[*].account_id)
  net_partition = one(data.aws_partition.network[*].partition)
  net_region    = one(data.aws_region.network[*].region)

  # Copied from the companion locals.tf; tests/test_terraform_modules_static.py asserts the lists
  # stay equal so the shared endpoint admits exactly what a dedicated one would.
  dataplane_base_actions = [
    "bedrock-agentcore:InvokeAgentRuntime",
    "bedrock-agentcore:GetWorkloadAccessToken",
    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    "bedrock-agentcore:GetResourceOauth2Token",
    "bedrock-agentcore:GetResourceApiKey",
  ]
  memory_dataplane_actions = [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:GetEvent",
    "bedrock-agentcore:DeleteEvent",
    "bedrock-agentcore:ListEvents",
    "bedrock-agentcore:ListActors",
    "bedrock-agentcore:ListSessions",
    "bedrock-agentcore:GetMemoryRecord",
    "bedrock-agentcore:ListMemoryRecords",
    "bedrock-agentcore:RetrieveMemoryRecords",
    "bedrock-agentcore:DeleteMemoryRecord",
    "bedrock-agentcore:BatchCreateMemoryRecords",
    "bedrock-agentcore:BatchUpdateMemoryRecords",
    "bedrock-agentcore:BatchDeleteMemoryRecords",
    "bedrock-agentcore:StartMemoryExtractionJob",
    "bedrock-agentcore:ListMemoryExtractionJobs",
  ]

  # Default KB pattern: the companion auto-name <prefix>-kb-<account>-<region>-<suffix>.
  kb_bucket_arn_patterns_effective = length(var.kb_bucket_arn_patterns) > 0 ? var.kb_bucket_arn_patterns : (
    var.enable_private_network ? ["arn:${local.net_partition}:s3:::*-kb-${local.net_account}-${local.net_region}-*"] : []
  )

  network_endpoint_policies = var.enable_private_network ? {
    "bedrock-agentcore.gateway" = data.aws_iam_policy_document.network_gateway[0].json
    "bedrock-agentcore"         = data.aws_iam_policy_document.network_dataplane[0].json
    "bedrock-runtime"           = data.aws_iam_policy_document.network_bedrock_runtime[0].json
    "comprehend"                = data.aws_iam_policy_document.network_comprehend[0].json
  } : {}
}

# bedrock-agentcore.gateway: invoke this account's gateways only.
data "aws_iam_policy_document" "network_gateway" {
  count = var.enable_private_network ? 1 : 0

  statement {
    sid       = "InvokeThisAccountGateways"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = ["arn:${local.net_partition}:bedrock-agentcore:${local.net_region}:${local.net_account}:gateway/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# bedrock-agentcore (data plane): the explicit companion action list on this account's resources.
data "aws_iam_policy_document" "network_dataplane" {
  count = var.enable_private_network ? 1 : 0

  statement {
    sid       = "ThisAccountAgentCoreDataPlane"
    effect    = "Allow"
    actions   = concat(local.dataplane_base_actions, var.allow_memory_dataplane_actions ? local.memory_dataplane_actions : [], var.dataplane_endpoint_extra_actions)
    resources = ["arn:${local.net_partition}:bedrock-agentcore:${local.net_region}:${local.net_account}:*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

# bedrock-runtime (SigV4 callers only): this account's principals only.
data "aws_iam_policy_document" "network_bedrock_runtime" {
  count = var.enable_private_network ? 1 : 0

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
      values   = [local.net_account]
    }
  }
}

# comprehend (only used when enable_comprehend_endpoint).
data "aws_iam_policy_document" "network_comprehend" {
  count = var.enable_private_network ? 1 : 0

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
      values   = [local.net_account]
    }
  }
}

# S3 gateway endpoint: the ECR image-layer bucket plus this account's KB buckets. Bucket names are
# global, so aws:ResourceAccount keeps a matching name in another account out.
data "aws_iam_policy_document" "network_s3_gateway" {
  count = var.enable_private_network ? 1 : 0

  statement {
    sid       = "EcrLayerBucket"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:${local.net_partition}:s3:::prod-${local.net_region}-starport-layer-bucket/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "KbBucketObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = [for p in local.kb_bucket_arn_patterns_effective : "${p}/${var.kb_prefix}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.net_account]
    }
  }

  statement {
    sid       = "KbBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = local.kb_bucket_arn_patterns_effective

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.net_account]
    }
  }
}
