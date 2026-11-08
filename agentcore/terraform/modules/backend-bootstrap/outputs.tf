output "state_bucket" {
  description = "Name of the remote-state S3 bucket. Use it as `bucket` in each companion's backend block."
  value       = aws_s3_bucket.state.id
}

output "lock_table" {
  description = "Name of the DynamoDB lock table. Use it as `dynamodb_table` in each companion's backend block (empty when disabled)."
  value       = var.enable_lock_table ? aws_dynamodb_table.locks[0].name : ""
}
