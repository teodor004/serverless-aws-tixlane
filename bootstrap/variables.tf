variable "aws_account_id" {
  description = "The dedicated Free-plan account. Supplied via bootstrap.auto.tfvars (git-ignored) or TF_VAR_aws_account_id."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit account ID."
  }
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the tixlane repository."
  type        = string
}

variable "owner" {
  description = "Owner tag value."
  type        = string
}

variable "existing_oidc_provider_arn" {
  description = "Set if the account already has a token.actions.githubusercontent.com provider."
  type        = string
  default     = null
}
