variable "state_bucket_name" {
  type        = string
  description = "Globally-unique S3 bucket name for remote Terraform state. Keep customer-agnostic (e.g. \"companion-tfstate-<account>-<region>\")."
}

variable "lock_table_name" {
  type        = string
  description = "DynamoDB table name for state locking (only used when enable_lock_table=true)."
  default     = "companion-tf-locks"
}

variable "enable_lock_table" {
  type        = bool
  description = "Create the DynamoDB lock table. Default false: backend.tf.example uses S3-native locking (use_lockfile) on a recent Terraform, so the table is not needed. Set true only for an older Terraform that still requires dynamodb_table locking."
  default     = false
}

variable "kms_key_arn" {
  type        = string
  description = "Optional CMK ARN for SSE-KMS on the state bucket. Empty uses the account's AWS-managed S3 KMS key (no CMK to pre-create)."
  default     = ""
}
