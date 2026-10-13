output "dlq_alarm_name" {
  description = "Name of the DLQ-depth alarm (used by the DLQ drill)."
  value       = aws_cloudwatch_metric_alarm.dlq_depth.alarm_name
}

output "alarm_names" {
  description = "Every alarm name."
  value = concat(
    [aws_cloudwatch_metric_alarm.api_5xx_rate.alarm_name, aws_cloudwatch_metric_alarm.dlq_depth.alarm_name],
    [for alarm in aws_cloudwatch_metric_alarm.lambda_errors : alarm.alarm_name],
    [for alarm in aws_cloudwatch_metric_alarm.lambda_throttles : alarm.alarm_name],
  )
}

output "dashboard_name" {
  description = "Dashboard name, or null when disabled."
  value       = one(aws_cloudwatch_dashboard.this[*].dashboard_name)
}
