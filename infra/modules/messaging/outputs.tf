output "queue_url" {
  description = "Tickets queue URL."
  value       = aws_sqs_queue.tickets.id
}

output "queue_arn" {
  description = "Tickets queue ARN."
  value       = aws_sqs_queue.tickets.arn
}

output "queue_name" {
  description = "Tickets queue name."
  value       = aws_sqs_queue.tickets.name
}

output "dlq_url" {
  description = "Dead-letter queue URL."
  value       = aws_sqs_queue.dlq.id
}

output "dlq_name" {
  description = "Dead-letter queue name."
  value       = aws_sqs_queue.dlq.name
}

output "ticket_topic_arn" {
  description = "SNS ticket-issued topic ARN."
  value       = aws_sns_topic.ticket_issued.arn
}

output "ops_topic_arn" {
  description = "SNS ops-alerts topic ARN."
  value       = aws_sns_topic.ops_alerts.arn
}
