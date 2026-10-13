variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "account_id" {
  description = "AWS account ID, used in resource-policy conditions."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for SQS and SNS server-side encryption."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "Tickets queue visibility timeout. AWS guidance: >= 6x the worker timeout (enforced by a check block in each env)."
  type        = number
  default     = 60
}

variable "max_receive_count" {
  description = "Receives before a message is moved to the DLQ (requirement: 3)."
  type        = number
  default     = 3
}

variable "alert_email" {
  description = "Optional e-mail subscribed to ops-alerts. Empty string disables the subscription."
  type        = string
  default     = ""
}
