output "bucket_name" {
  description = "Artefacts bucket name."
  value       = aws_s3_bucket.artefacts.id
}

output "bucket_arn" {
  description = "Artefacts bucket ARN."
  value       = aws_s3_bucket.artefacts.arn
}
