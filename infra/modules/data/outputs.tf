output "kms_key_arn" {
  description = "ARN of the data CMK."
  value       = aws_kms_key.this.arn
}

output "table_name" {
  description = "DynamoDB table name."
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "DynamoDB table ARN."
  value       = aws_dynamodb_table.this.arn
}
