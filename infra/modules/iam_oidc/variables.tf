variable "name_prefix" {
  description = "Prefix for the deploy role and its policies, e.g. tixlane."
  type        = string
  default     = "tixlane"
}

variable "workload_prefix" {
  description = "Name prefix of every workload resource the deploy role may manage, e.g. tixlane-prod. Must not match the deploy role's own name."
  type        = string
  default     = "tixlane-prod"
}

variable "account_id" {
  description = "AWS account ID."
  type        = string
}

variable "region" {
  description = "The only region the deploy role may manage."
  type        = string
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the repository."
  type        = string
}

variable "github_repo" {
  description = "Repository name."
  type        = string
  default     = "serverless-aws-tixlane"
}

variable "github_environment" {
  description = "GitHub environment whose jobs may assume the role (required reviewers configured there)."
  type        = string
  default     = "production"
}

variable "existing_oidc_provider_arn" {
  description = "ARN of an existing token.actions.githubusercontent.com provider; null creates one."
  type        = string
  default     = null
}

variable "state_bucket_arn" {
  description = "ARN of the OpenTofu state bucket."
  type        = string
}

variable "state_key_prefix" {
  description = "Object prefix inside the state bucket that the role may read and write."
  type        = string
  default     = "tixlane/"
}

variable "state_kms_key_arn" {
  description = "CMK used for S3 SSE-KMS of the state bucket and for OpenTofu state encryption."
  type        = string
}

variable "max_session_seconds" {
  description = "Maximum role session duration."
  type        = number
  default     = 3600
}
