variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "function_names" {
  description = "Function names for api_events and api_reservations."
  type        = map(string)
}

variable "invoke_arns" {
  description = "Invoke ARNs for api_events and api_reservations."
  type        = map(string)
}

variable "jwt_issuer" {
  description = "Cognito issuer URL."
  type        = string
}

variable "jwt_audience" {
  description = "Cognito app client ID."
  type        = string
}

variable "cors_allowed_origins" {
  description = "Exact origins allowed by CORS. Wildcards are rejected."
  type        = list(string)

  validation {
    condition     = length(var.cors_allowed_origins) > 0 && alltrue([for origin in var.cors_allowed_origins : !strcontains(origin, "*")])
    error_message = "cors_allowed_origins must list explicit origins; \"*\" is not allowed."
  }
}

variable "throttling_rate_limit" {
  description = "Stage steady-state requests per second."
  type        = number
  default     = 10
}

variable "throttling_burst_limit" {
  description = "Stage burst capacity."
  type        = number
  default     = 20
}

variable "kms_key_arn" {
  description = "CMK for the access-log group."
  type        = string
}

variable "log_retention_days" {
  description = "Access-log retention (budget rule: 7)."
  type        = number
  default     = 7
}
