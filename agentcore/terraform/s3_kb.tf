# S3 bucket backing the hybrid knowledge base:
#   - self-learning KB store under prefix  kb/                       (var.kb_prefix)
#   - curated Tier-B overlay object        kb/curated/overlay.json   (var.kb_overlay_key)
#
# Tier-A locked constraints stay baked into the image and are NEVER in this bucket — the
# overlay cannot touch them. The bucket is private, encrypted, and versioned.

# kics-scan ignore-line: "IAM Access Analyzer Not Enabled" is an account/org baseline control
# made opt-in here via enable_access_analyzer (access_analyzer.tf) — docs/SECURITY.md SD-8.
resource "aws_s3_bucket" "kb" {
  # checkov:skip=CKV_AWS_144: cross-region replication is disproportionate for a small,
  #   single-region KB in a starter kit; the bucket is versioned + CMK-encrypted. A fork with a
  #   multi-region DR requirement enables CRR (documented tradeoff — docs/SECURITY.md).
  bucket = local.kb_bucket_name

  # force_destroy is dev-friendly; for production set false so a destroy can't drop the KB.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "kb" {
  bucket                  = aws_s3_bucket.kb.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "kb" {
  bucket = aws_s3_bucket.kb.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "kb" {
  bucket = aws_s3_bucket.kb.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Customer-managed KMS key for the KB bucket (the KB can hold engagement-derived Tier-B
# content, so a key we control + rotate is proportionate). The key policy is defined INLINE as
# an explicit JSON document (not an aws_iam_policy_document data source) using the discrete KMS
# admin/use actions — never the kms:* wildcard action — so it does not trip the IAM-wildcard
# checks. A KMS key policy's Resource is necessarily the key itself (there is no ARN to name
# inside the key's own policy); the discrete-action + service-scoped-condition form is the AWS
# least-privilege baseline.
resource "aws_kms_key" "kb" {
  description             = "${var.name_prefix} KB bucket encryption"
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
        Sid       = "AllowS3Use"
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "s3.${local.region}.amazonaws.com" } }
      },
      {
        Sid       = "AllowRuntimeRoleUse"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.runtime_exec.arn }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
      },
    ]
  })
}

resource "aws_kms_alias" "kb" {
  name          = "alias/${var.name_prefix}-kb"
  target_key_id = aws_kms_key.kb.key_id
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kb" {
  bucket = aws_s3_bucket.kb.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.kb.arn
    }
    bucket_key_enabled = true
  }
}

# HTTPS-only bucket policy (kics "S3 Bucket Policy Accepts HTTP Requests"): deny any request
# not made over TLS. Belt-and-braces with the public-access block.
resource "aws_s3_bucket_policy" "kb" {
  bucket = aws_s3_bucket.kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.kb.arn,
        "${aws_s3_bucket.kb.arn}/*",
      ]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# Event notifications (checkov CKV2_AWS_62 / kics "S3 bucket notifications disabled"): enable
# EventBridge delivery for the bucket. This satisfies the control and gives an audit/event hook
# without wiring a specific consumer — EventBridge is the modern, decoupled default.
resource "aws_s3_bucket_notification" "kb" {
  bucket      = aws_s3_bucket.kb.id
  eventbridge = true
}

# Lifecycle hygiene (checkov CKV2_AWS_61): the bucket is versioned, so expire noncurrent
# versions and abort incomplete multipart uploads to bound storage cost. Current objects are
# kept indefinitely (the KB/overlay is small and long-lived); this only trims stale versions.
resource "aws_s3_bucket_lifecycle_configuration" "kb" {
  bucket = aws_s3_bucket.kb.id

  rule {
    id     = "expire-noncurrent-and-abort-incomplete"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---- Server access logging (checkov CKV_AWS_18 / kics "S3 Bucket Logging Disabled") --------
# Access logging must write to a SEPARATE target bucket (self-logging is discouraged and can
# loop). This dedicated log bucket is private, SSE-S3 encrypted, ownership-enforced, and has
# its own lifecycle so logs don't accumulate unboundedly. Logs are operational metadata, not
# secrets — SSE-S3 (not a CMK) is the proportionate choice here.
resource "aws_s3_bucket" "kb_logs" {
  # checkov:skip=CKV_AWS_145: the access-LOG bucket is SSE-S3 encrypted by design — logs are
  #   operational metadata, not secrets, so a CMK is disproportionate (docs/SECURITY.md SD-3).
  # checkov:skip=CKV_AWS_144: cross-region replication is disproportionate for an access-log
  #   bucket in a starter kit (versioned + lifecycle-expired). A fork enables CRR if required.
  bucket        = "${local.kb_bucket_name}-logs"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "kb_logs" {
  bucket                  = aws_s3_bucket.kb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "kb_logs" {
  bucket = aws_s3_bucket.kb_logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kb_logs" {
  bucket = aws_s3_bucket.kb_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "kb_logs" {
  bucket = aws_s3_bucket.kb_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "kb_logs" {
  bucket = aws_s3_bucket.kb_logs.id
  rule {
    id     = "expire-access-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 365
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# With BucketOwnerEnforced (ACLs disabled), the S3 log-delivery service writes via a bucket
# policy grant scoped to this source bucket + account, rather than the legacy log-delivery ACL.
resource "aws_s3_bucket_policy" "kb_logs" {
  bucket = aws_s3_bucket.kb_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "S3ServerAccessLogsWrite"
        Effect    = "Allow"
        Principal = { Service = "logging.s3.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.kb_logs.arn}/*"
        Condition = {
          ArnLike      = { "aws:SourceArn" = aws_s3_bucket.kb.arn }
          StringEquals = { "aws:SourceAccount" = local.account_id }
          # TLS-only on the Allow too (not just the companion Deny), so the grant itself never
          # authorizes a plaintext PutObject (kics "S3 Bucket Policy Accepts HTTP Requests").
          Bool = { "aws:SecureTransport" = "true" }
        }
      },
      {
        # HTTPS-only (kics "S3 Bucket Policy Accepts HTTP Requests").
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.kb_logs.arn,
          "${aws_s3_bucket.kb_logs.arn}/*",
        ]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
}

resource "aws_s3_bucket_notification" "kb_logs" {
  bucket      = aws_s3_bucket.kb_logs.id
  eventbridge = true
}

resource "aws_s3_bucket_logging" "kb" {
  bucket        = aws_s3_bucket.kb.id
  target_bucket = aws_s3_bucket.kb_logs.id
  target_prefix = "s3-access/"

  # Ensure the destination bucket's write policy exists before logging is enabled.
  depends_on = [aws_s3_bucket_policy.kb_logs]
}

# Optional: seed an empty overlay object so the first Tier-B read resolves cleanly. A human
# replaces it via the curated-overlay promotion flow. `ignore_changes` means Terraform will not
# clobber a real overlay a human later promotes into place.
resource "aws_s3_object" "overlay_seed" {
  count = var.seed_overlay_object ? 1 : 0

  bucket       = aws_s3_bucket.kb.id
  key          = var.kb_overlay_key
  content      = jsonencode({ _comment = "Placeholder Tier-B overlay. Replace via the human-in-loop curated-overlay promotion flow.", entries = {} })
  content_type = "application/json"

  lifecycle {
    ignore_changes = [content, content_type]
  }
}
