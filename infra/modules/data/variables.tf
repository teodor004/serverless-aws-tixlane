variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "account_id" {
  description = "AWS account ID (resolved at plan time from aws_caller_identity; never hard-coded)."
  type        = string
}

variable "region" {
  description = "AWS region."
  type        = string
}

variable "enable_pitr" {
  description = "Enable DynamoDB point-in-time recovery. Off on the emulator (EMULATOR_GAPS G-09)."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "DynamoDB deletion protection. Off: the stack is destroyed after 48 h (TEARDOWN.md)."
  type        = bool
  default     = false
}

variable "kms_deletion_window_days" {
  description = "Waiting period before the CMK is deleted after destroy (AWS minimum is 7)."
  type        = number
  default     = 7

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "KMS deletion window must be between 7 and 30 days."
  }
}
