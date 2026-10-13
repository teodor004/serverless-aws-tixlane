# Consumed by tests/integration and scripts/e2e.py via `tofu output -json`; keep the names in
# sync with envs/aws/outputs.tf.

output "region" {
  description = "Region."
  value       = var.region
}

output "api_endpoint" {
  description = "HTTP API base URL as reported by the emulator (EMULATOR_GAPS G-03)."
  value       = module.api.api_endpoint
}

output "user_pool_id" {
  description = "Cognito user pool ID."
  value       = module.auth.user_pool_id
}

output "user_pool_client_id" {
  description = "Cognito app client ID."
  value       = module.auth.client_id
}

output "table_name" {
  description = "DynamoDB table."
  value       = module.data.table_name
}

output "queue_url" {
  description = "Tickets queue URL."
  value       = module.messaging.queue_url
}

output "dlq_url" {
  description = "DLQ URL."
  value       = module.messaging.dlq_url
}

output "queue_visibility_timeout_seconds" {
  description = "Used by the DLQ test to size its wait."
  value       = var.queue_visibility_timeout_seconds
}

output "bucket_name" {
  description = "Artefacts bucket."
  value       = module.storage.bucket_name
}

output "ticket_topic_arn" {
  description = "SNS ticket-issued topic."
  value       = module.messaging.ticket_topic_arn
}

output "dlq_alarm_name" {
  description = "DLQ-depth alarm."
  value       = module.observability.dlq_alarm_name
}

output "alarm_names" {
  description = "Every alarm."
  value       = module.observability.alarm_names
}

output "dashboard_name" {
  description = "Dashboard."
  value       = module.observability.dashboard_name
}

output "function_names" {
  description = "Lambda functions."
  value       = module.compute.function_names
}
