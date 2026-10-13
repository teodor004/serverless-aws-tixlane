variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "env" {
  description = "Environment name shown on the dashboard."
  type        = string
}

variable "region" {
  description = "AWS region for dashboard widgets."
  type        = string
}

variable "api_id" {
  description = "HTTP API ID."
  type        = string
}

variable "api_stage" {
  description = "HTTP API stage name."
  type        = string
}

variable "function_names" {
  description = "Function names keyed by logical name."
  type        = map(string)
}

variable "queue_name" {
  description = "Tickets queue name."
  type        = string
}

variable "dlq_name" {
  description = "Dead-letter queue name."
  type        = string
}

variable "table_name" {
  description = "DynamoDB table name."
  type        = string
}

variable "ops_topic_arn" {
  description = "SNS ops-alerts topic that every alarm notifies."
  type        = string
}

variable "api_5xx_rate_threshold_percent" {
  description = "Alarm when 5xx responses exceed this share of requests over 5 minutes."
  type        = number
  default     = 5
}

variable "enable_dashboard" {
  description = "Create the CloudWatch dashboard (EMULATOR_GAPS G-15)."
  type        = bool
  default     = true
}
