# modules/backend-bootstrap: the remote Terraform state backend. A versioned, encrypted,
# public-access-blocked S3 bucket for state, an access-log bucket, and an optional DynamoDB lock
# table. A human applies this ONCE with local state, then points every companion stack at the
# bucket (see README.md and ../../backend.tf.example).
#
# Governance (Tenet 1): a human applies this and runs `terraform init -migrate-state`. No agent
# applies it or migrates live state.
#
# Resource names are kept equal to the original shared-platform module so that an existing
# platform state can be adopted by this code with no replacement (docs/DEPLOY.md "Adopting an
# existing platform state"). The 3.2.2 lifecycle and notification resources below are additions
# under new addresses. The 3.2.2 access-log delivery fix changes two existing resources in place:
# the state_logs default encryption (SSE-KMS to SSE-S3) and the state_logs bucket policy (adds
# the logging.s3.amazonaws.com grant).
# KICS "IAM Access Analyzer Not Enabled" (K8) fires once per file with any aws_ resource and no
# analyzer. The analyzer is an account or organization baseline control that a landing zone
# normally owns; this kit makes it opt-in (enable_access_analyzer, access_analyzer.tf;
# docs/SECURITY.md SD-8), so the per-file query does not apply to this file.
# KICS "S3 Bucket Policy Accepts HTTP Requests" (K4) cannot evaluate a jsonencode() bucket
# policy whose Condition values are Terraform references (the same policy with literal values
# passes). Every bucket policy in this file has DenyInsecureTransport (Deny s3:* when
# aws:SecureTransport is "false"), asserted by the static tests. Only that query is dropped;
# every other KICS bucket-policy query still runs on these policies.
# kics-scan disable=e592a0c5-5bdb-414c-9066-5dba7cdea370,4bc4dd4c-7d8d-405e-a0fb-57fa4c31b4d9
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # within v6 (data.aws_region.current.region is v6-only); the caller root pins further
      version = ">= 6.0.0, < 7.0.0"
    }
  }
}

resource "aws_s3_bucket" "state" {
  # checkov:skip=CKV_AWS_144: cross-region replication of Terraform state is a per-organization DR decision (a second-Region bucket, KMS key and role); the bucket is versioned with noncurrent-version retention, which covers a bad apply (docs/SECURITY.md SD-10).
  bucket = var.state_bucket_name
  # The state bucket is durable shared infrastructure holding every companion's state (with
  # secrets); guard it from an accidental destroy.
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning: keep prior state revisions so a bad apply can be rolled back.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt at rest. SSE-KMS with a bucket key by default; aws:kms with the account's S3 key keeps
# this module dependency-free (no CMK to pre-create). Supply var.kms_key_arn for a CMK.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn == "" ? null : var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

# Block ALL public access: state must never be internet-reachable.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enforce TLS-only access to the state bucket.
resource "aws_s3_bucket_policy" "state_tls_only" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.state.arn,
        "${aws_s3_bucket.state.arn}/*",
      ]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# Access-logging target bucket for the state bucket (checkov CKV_AWS_18 / kics S3 logging).
# Versioned, SSE-S3 encrypted and public-access-blocked. A logs bucket does not itself get access
# logging (that would recurse), which is the accepted pattern.
resource "aws_s3_bucket" "state_logs" {
  # checkov:skip=CKV_AWS_145: S3 server access logging delivers only to a target bucket with SSE-S3 default encryption, so SSE-KMS is not an option for this bucket; the logs are operational metadata, not secrets (docs/SECURITY.md SD-3).
  # checkov:skip=CKV_AWS_144: replicating an access-log bucket to a second Region is disproportionate; the logs are operational metadata with a 365-day lifecycle (docs/SECURITY.md SD-10).
  bucket = "${var.state_bucket_name}-logs"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state_logs" {
  bucket = aws_s3_bucket.state_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3, not SSE-KMS: server access logging does not deliver to a target bucket whose default
# encryption is SSE-KMS (Amazon S3 User Guide, "Enabling Amazon S3 server access logging",
# permissions for log delivery). Same as the companion's KB access-log bucket.
resource "aws_s3_bucket_server_side_encryption_configuration" "state_logs" {
  bucket = aws_s3_bucket.state_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state_logs" {
  bucket                  = aws_s3_bucket.state_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_caller_identity" "current" {}

# TLS-only, plus the write grant for the S3 log-delivery service. Buckets created with ACLs
# disabled (BucketOwnerEnforced, the S3 default) take log delivery only through a bucket policy
# grant to logging.s3.amazonaws.com, scoped to the state bucket and this account. The resource
# address keeps its original name so an adopted state updates it in place.
resource "aws_s3_bucket_policy" "state_logs_tls_only" {
  bucket = aws_s3_bucket.state_logs.id
  # K4 (file-header disable, query 4bc4dd4c only): this policy denies s3:* when
  # aws:SecureTransport is "false" (statement DenyInsecureTransport; tests/test_terraform_modules_static.py).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "S3ServerAccessLogsWrite"
        Effect    = "Allow"
        Principal = { Service = "logging.s3.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.state_logs.arn}/state-access-logs/*"
        Condition = {
          ArnLike      = { "aws:SourceArn" = aws_s3_bucket.state.arn }
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          Bool         = { "aws:SecureTransport" = "true" }
        }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.state_logs.arn, "${aws_s3_bucket.state_logs.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

resource "aws_s3_bucket_logging" "state" {
  bucket        = aws_s3_bucket.state.id
  target_bucket = aws_s3_bucket.state_logs.id
  target_prefix = "state-access-logs/"
  # The destination bucket's write grant exists before logging is enabled.
  depends_on = [aws_s3_bucket_policy.state_logs_tls_only]
}

# Lifecycle: expire noncurrent state versions after 90 days so the versioned bucket does not
# grow unbounded (checkov CKV_AWS_300 / kics lifecycle). Current versions are kept.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-noncurrent-state-versions"
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

# Lifecycle for the access-log bucket (checkov CKV2_AWS_61): keep a year of logs, then expire;
# same rule as the companion's KB access-log bucket.
resource "aws_s3_bucket_lifecycle_configuration" "state_logs" {
  bucket = aws_s3_bucket.state_logs.id
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

# Event notifications to Amazon EventBridge (checkov CKV2_AWS_62), as on the companion's KB
# buckets. Nothing consumes them until a rule is added; a team can alert on state writes.
resource "aws_s3_bucket_notification" "state" {
  bucket      = aws_s3_bucket.state.id
  eventbridge = true
}

resource "aws_s3_bucket_notification" "state_logs" {
  bucket      = aws_s3_bucket.state_logs.id
  eventbridge = true
}

# Optional DynamoDB lock table. On recent Terraform, S3 native locking (use_lockfile) replaces
# it; the table is kept for older Terraform. LockID is the required hash key name.
resource "aws_dynamodb_table" "locks" {
  count        = var.enable_lock_table ? 1 : 0
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
  # Encrypt at rest. SSE is on by default with the AWS-owned key; set a CMK via
  # var.kms_key_arn for a customer-managed key (checkov CKV_AWS_119 / kics).
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn == "" ? null : var.kms_key_arn
  }
  # Point-in-time recovery so a corrupted/lost lock table can be restored (checkov CKV_AWS_28).
  point_in_time_recovery {
    enabled = true
  }
}
