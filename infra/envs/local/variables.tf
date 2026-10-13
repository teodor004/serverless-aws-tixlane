variable "region" {
  description = "Region (eu-central-1 only, by budget rule)."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = var.region == "eu-central-1"
    error_message = "TixLane runs in eu-central-1 only."
  }
}

variable "owner" {
  description = "Owner tag value."
  type        = string
  default     = "local-dev"
}

variable "emulator_endpoint" {
  description = "Emulator edge endpoint as seen from the host running tofu."
  type        = string
  default     = "http://localhost:4566"

  validation {
    condition     = can(regex("^http://(localhost|127\\.0\\.0\\.1|emulator|floci|ministack)(:[0-9]+)?/?$", var.emulator_endpoint))
    error_message = "emulator_endpoint must be a local emulator URL, never an AWS endpoint."
  }
}

variable "emulator_account_id" {
  description = "Account ID the emulator reports from sts:GetCallerIdentity (V-007)."
  type        = string
  default     = "000000000000"
}

variable "lambda_package_path" {
  description = "Lambda zip built by `make build`."
  type        = string
  default     = "../../../build/lambda.zip"
}

variable "python_runtime" {
  description = "python3.13, or python3.12 if the emulator lacks 3.13 (EMULATOR_GAPS G-10)."
  type        = string
  default     = "python3.13"
}

variable "lambda_aws_endpoint_url" {
  description = "Endpoint the Lambda containers use to reach the emulator (EMULATOR_GAPS G-11)."
  type        = string
  default     = "http://emulator:4566"
}

variable "auth_mode" {
  description = "apigw, or emulator-unverified if the emulator does not populate JWT claims (EMULATOR_GAPS G-01)."
  type        = string
  default     = "apigw"
}

variable "cognito_issuer_base_url" {
  description = "Issuer base URL the emulator puts in tokens (EMULATOR_GAPS G-02). Null = AWS format."
  type        = string
  default     = null
}

variable "enable_xray" {
  description = "X-Ray tracing; unverified on emulators (EMULATOR_GAPS G-04)."
  type        = bool
  default     = false
}

variable "reserved_concurrency" {
  description = "Reserved concurrency per function (EMULATOR_GAPS G-06)."
  type        = number
  default     = 5
}

variable "worker_max_concurrency" {
  description = "SQS event source maximum concurrency; null if unsupported (EMULATOR_GAPS G-07)."
  type        = number
  default     = null
}

variable "enable_dashboard" {
  description = "Create the CloudWatch dashboard (EMULATOR_GAPS G-15)."
  type        = bool
  default     = true
}

variable "cors_allowed_origins" {
  description = "Allowed CORS origins for local development."
  type        = list(string)
  default     = ["http://localhost:3000"]
}

variable "queue_visibility_timeout_seconds" {
  description = "Shorter than on AWS so the DLQ test (3 receives) finishes quickly."
  type        = number
  default     = 30
}

variable "worker_timeout_seconds" {
  description = "Worker timeout; visibility timeout must be >= 6x this."
  type        = number
  default     = 5
}
