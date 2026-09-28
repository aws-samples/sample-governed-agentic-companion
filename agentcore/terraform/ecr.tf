# ECR repositories for the two runtime images (orchestrator /invocations, mcp_runtime /mcp).
# Both are built from the SAME repo-root build context; only the Dockerfile/entrypoint differ.

# Image tag mutability (checkov CKV_AWS_51): IMMUTABLE is the safe default — an image tag,
# once pushed, cannot be overwritten, so a deployed digest is reproducible. This is compatible
# with a versioned-tag deploy pattern (every release bumps the version tag); we never re-push
# the same tag. Set var.ecr_immutable_tags=false only if you need to overwrite a tag in place
# (e.g. a scratch "latest" during local iteration).
locals {
  ecr_tag_mutability = var.ecr_immutable_tags ? "IMMUTABLE" : "MUTABLE"
}

# Customer-managed KMS key for ECR image encryption. Policy defined INLINE (not an
# aws_iam_policy_document data source) with discrete KMS actions — never kms:* — so it does not
# trip the IAM-wildcard checks. A key policy's Resource is necessarily the key itself.
# kics-scan ignore-line: "IAM Access Analyzer Not Enabled" is an ACCOUNT/ORG baseline control a
# landing zone normally owns; this per-workload kit makes it opt-in via enable_access_analyzer
# (access_analyzer.tf) to avoid colliding with a delegated org analyzer (docs/SECURITY.md SD-8).
resource "aws_kms_key" "ecr" {
  description             = "${var.name_prefix} ECR image encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true
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
        Sid       = "AllowEcrUse"
        Effect    = "Allow"
        Principal = { Service = "ecr.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ecr.${local.region}.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_kms_alias" "ecr" {
  name          = "alias/${var.name_prefix}-ecr"
  target_key_id = aws_kms_key.ecr.key_id
}

resource "aws_ecr_repository" "orchestrator" {
  name                 = "${var.name_prefix}-orchestrator"
  image_tag_mutability = local.ecr_tag_mutability

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  # force_delete lets `terraform destroy` remove the repo even if images remain (dev-friendly).
  # For production, consider setting this to false and cleaning images explicitly.
  force_delete = true
}

resource "aws_ecr_repository" "mcp_runtime" {
  name                 = "${var.name_prefix}-mcp-runtime"
  image_tag_mutability = local.ecr_tag_mutability

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true
}

# Keep the repos tidy — expire all but the most recent images.
resource "aws_ecr_lifecycle_policy" "orchestrator" {
  repository = aws_ecr_repository.orchestrator.name
  policy     = local.ecr_lifecycle_policy
}

resource "aws_ecr_lifecycle_policy" "mcp_runtime" {
  repository = aws_ecr_repository.mcp_runtime.name
  policy     = local.ecr_lifecycle_policy
}

# Repository policy (kics "ECR Repository Without Policy"): least-privilege pull for the
# runtime execution role (the only in-account consumer). Push is done by the local build via
# the caller's own IAM identity, so it needs no repo-policy grant. Single-account, so no
# cross-account principals.
locals {
  ecr_repo_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowRuntimePull"
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.runtime_exec.arn }
      Action = [
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:BatchCheckLayerAvailability",
      ]
    }]
  })
}

resource "aws_ecr_repository_policy" "orchestrator" {
  repository = aws_ecr_repository.orchestrator.name
  policy     = local.ecr_repo_policy
}

resource "aws_ecr_repository_policy" "mcp_runtime" {
  repository = aws_ecr_repository.mcp_runtime.name
  policy     = local.ecr_repo_policy
}
