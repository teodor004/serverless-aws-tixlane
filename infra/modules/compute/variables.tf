variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "env" {
  description = "Environment name passed to the code as TIXLANE_ENV."
  type        = string
}

variable "package_path" {
  description = "Path to the deterministic Lambda zip built by scripts/build_lambda.py."
  type        = string
}

variable "python_runtime" {
  description = "Lambda runtime. python3.12 is the documented emulator fallback (EMULATOR_GAPS G-10)."
  type        = string
  default     = "python3.13"

  validation {
    condition     = contains(["python3.12", "python3.13"], var.python_runtime)
    error_message = "python_runtime must be python3.13 (target) or python3.12 (emulator fallback)."
  }
}

variable "architecture" {
  description = "Lambda instruction set; must match the wheels in the package."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64."
  }
}

variable "reserved_concurrency" {
  description = "Reserved concurrency per function (requirement: 5). -1 removes the reservation; see RISK-01 in docs/COST.md for new-account quotas."
  type        = number
  default     = 5
}

variable "worker_max_concurrency" {
  description = "SQS event source maximum concurrency (min 2). Null omits scaling_config (EMULATOR_GAPS G-07)."
  type        = number
  default     = 2
  nullable    = true
}

variable "api_timeout_seconds" {
  description = "Timeout for the API functions."
  type        = number
  default     = 10
}

variable "worker_timeout_seconds" {
  description = "Timeout for the worker function."
  type        = number
  default     = 10
}

variable "memory_mb" {
  description = "Memory for every function."
  type        = number
  default     = 256
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention (budget rule: 7)."
  type        = number
  default     = 7
}

variable "log_level" {
  description = "POWERTOOLS_LOG_LEVEL."
  type        = string
  default     = "INFO"
}

variable "enable_xray" {
  description = "Active X-Ray tracing. Off on the emulator (EMULATOR_GAPS G-04)."
  type        = bool
  default     = true
}

variable "auth_mode" {
  description = "apigw (claims from the JWT authorizer) or emulator-unverified (local only, EMULATOR_GAPS G-01)."
  type        = string
  default     = "apigw"

  validation {
    condition     = var.auth_mode == "apigw" || (var.auth_mode == "emulator-unverified" && var.env == "local")
    error_message = "auth_mode=emulator-unverified is only permitted when env = \"local\"."
  }
}

variable "aws_endpoint_url" {
  description = "Endpoint the functions use for AWS calls (emulator only, EMULATOR_GAPS G-11). Null on real AWS."
  type        = string
  default     = null
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary for the function roles (required by the deploy role on real AWS)."
  type        = string
  default     = null
}

variable "kms_key_arn" {
  description = "Data CMK (log groups, env vars, and decrypt/generate for table, queue, topic, bucket)."
  type        = string
}

variable "table_name" {
  description = "DynamoDB table name."
  type        = string
}

variable "table_arn" {
  description = "DynamoDB table ARN."
  type        = string
}

variable "queue_url" {
  description = "Tickets queue URL."
  type        = string
}

variable "queue_arn" {
  description = "Tickets queue ARN."
  type        = string
}

variable "bucket_name" {
  description = "Artefacts bucket name."
  type        = string
}

variable "bucket_arn" {
  description = "Artefacts bucket ARN."
  type        = string
}

variable "ticket_topic_arn" {
  description = "SNS ticket-issued topic ARN."
  type        = string
}
