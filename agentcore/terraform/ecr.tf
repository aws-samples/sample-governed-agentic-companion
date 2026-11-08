# ECR repositories for the two runtime images (orchestrator /invocations, mcp_runtime /mcp).
# Both are built from the SAME repo-root build context; only the Dockerfile/entrypoint differ.
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370

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
        # PRODUCTION HARDENING: these service-scoped wildcards (particularly the destructive
        # kms:Delete*, kms:Disable*, and kms:Create*) are convenient for a self-contained
        # sandbox kit, but for a production deployment you SHOULD replace them with the exact
        # KMS actions your key administrators actually need, and restrict this statement to a
        # narrow key-admin principal rather than the account root. See the AWS KMS docs for the
        # specific actions, and apply least privilege (docs/SECURITY.md).
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
#
# The images are a KNOWLEDGE STORE: both Dockerfiles COPY knowledge/ into the image, so pulling
# an image discloses the corpus. AllowRuntimePull alone restricts nobody (same-account
# principals with identity-based ECR rights are allowed regardless), so private mode (with
# ecr_pull_restriction=true, the default) adds an explicit Deny on pulls that do not come
# through the ecr endpoints, unless the caller is a listed knowledge admin, the runtime
# execution role (assumable only by bedrock-agentcore.amazonaws.com), or an AWS service
# principal. Pushing also needs ecr:BatchGetImage, so CI/terraform runners must be listed.
# There is deliberately no via-AWS-service (forward access session) exemption: it would admit
# pulls made for any principal through any AWS service, from anywhere. Fallback order if a runtime cannot pull: docs/DEPLOY.md.
# https://docs.aws.amazon.com/AmazonECR/latest/userguide/repository-policy-examples.html
# Public mode renders exactly the single AllowRuntimePull statement (today's policy).
locals {
  ecr_repo_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Sid       = "AllowRuntimePull"
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.runtime_exec.arn }
      Action = [
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:BatchCheckLayerAvailability",
      ]
      }],
      [for _ in range(local.network_private && var.ecr_pull_restriction ? 1 : 0) : {
        Sid       = "DenyPullNotViaEcrEndpointsOrAdmins"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Condition = {
          StringNotEquals = { "aws:SourceVpce" = local.ecr_vpce_ids }
          ArnNotLike      = { "aws:PrincipalArn" = concat(local.knowledge_admin_arns_effective, [aws_iam_role.runtime_exec.arn]) }
          BoolIfExists    = { "aws:PrincipalIsAWSService" = "false" }
        }
      }],
    )
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
