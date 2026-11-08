# `modules/backend-bootstrap`: remote Terraform state backend

Creates the remote state backend that the platform stack and every companion stack can use:

- a versioned, SSE-KMS encrypted S3 bucket with all four S3 Block Public Access settings on and a
  TLS-only bucket policy (`DenyInsecureTransport`);
- an access-log bucket (versioned, public-access-blocked, TLS-only) with SSE-S3 default encryption
  and a bucket policy grant to the `logging.s3.amazonaws.com` service principal, scoped to the state
  bucket and the account. S3 server access logging does not deliver to an SSE-KMS target, so this
  bucket cannot use a CMK;
- a lifecycle rule that expires noncurrent state versions after 90 days, and one on the access-log
  bucket that expires access logs 365 days after creation and noncurrent versions after 30 days;
- an optional DynamoDB lock table (`enable_lock_table`, default `false`). Terraform 1.10 and later
  can lock with S3 native locking (`use_lockfile = true`), so the table is only for older Terraform.

Both buckets carry `prevent_destroy`.

> Governance (Tenet 1). A human applies this module (through `../../platform/`) and runs the
> one-time `terraform init -migrate-state`. No agent applies it or migrates live state.

## Bootstrap order

The stack that creates the bucket cannot keep its first state in that bucket:

1. A human applies `../../platform/` with local state. It creates the bucket.
2. The human copies `../../platform/backend.tf.example` to `backend.tf` and runs
   `terraform init -migrate-state` to move the platform state into the bucket.
3. Each companion copies `../../backend.tf.example` to `backend.tf` with its own `key`
   (for example `companions/<name>/terraform.tfstate`) and runs `terraform init -migrate-state`.
   Migrate one stack at a time, and never point two stacks at one key.

## Inputs

| Variable | Purpose |
|---|---|
| `state_bucket_name` | Globally unique bucket name. Keep it customer-agnostic, for example `companion-tfstate-<ACCOUNT_ID>-<REGION>`. |
| `lock_table_name` | DynamoDB lock table name (default `companion-tf-locks`), used only when `enable_lock_table = true`. |
| `enable_lock_table` | Create the DynamoDB lock table (default `false`). |
| `kms_key_arn` | Optional CMK for the state bucket and the lock table. Empty uses the AWS-managed S3 key. The access-log bucket always uses SSE-S3. |

## Outputs

`state_bucket` and `lock_table` (empty when the table is disabled).
