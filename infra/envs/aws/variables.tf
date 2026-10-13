# Values come from GitHub environment variables (TF_VAR_*) in deploy.yml / destroy.yml.
# terraform.tfvars.example documents them; a real .tfvars is never committed.

variable "aws_account_id" {
  description = "The dedicated Free-plan account (GitHub variable AWS_ACCOUNT_ID)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit account ID."
  }
}

variable "owner" {
  description = "Owner tag value."
  type        = string
}

variable "permissions_boundary_arn" {
  description = "Workload boundary from bootstrap (GitHub variable WORKLOAD_BOUNDARY_ARN)."
  type        = string
}

variable "cors_allowed_origins" {
  description = "Exact browser origins allowed to call the API."
  type        = list(string)
}

variable "alert_email" {
  description = "Optional e-mail for ops-alerts. Empty disables the subscription."
  type        = string
  default     = ""
}

variable "lambda_package_path" {
  description = "Lambda zip built in the plan job and carried to apply as an artifact."
  type        = string
  default     = "../../../build/lambda.zip"
}

variable "enable_xray" {
  description = "Active X-Ray tracing on every function."
  type        = bool
  default     = true
}

variable "reserved_concurrency" {
  description = "Reserved concurrency per function. Set -1 if the account's concurrency quota is too low to reserve (RISK-01)."
  type        = number
  default     = 5
}

variable "worker_max_concurrency" {
  description = "SQS event source maximum concurrency (minimum 2)."
  type        = number
  default     = 2
}

variable "throttling_rate_limit" {
  description = "HTTP API stage steady-state requests per second."
  type        = number
  default     = 10
}

variable "throttling_burst_limit" {
  description = "HTTP API stage burst."
  type        = number
  default     = 20
}

variable "queue_visibility_timeout_seconds" {
  description = "Tickets queue visibility timeout (>= 6x worker timeout)."
  type        = number
  default     = 60
}

variable "worker_timeout_seconds" {
  description = "Worker timeout."
  type        = number
  default     = 10
}
