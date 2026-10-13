variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "account_id" {
  description = "AWS account ID, used to make the bucket name globally unique."
  type        = string
}

variable "region" {
  description = "AWS region, used to make the bucket name globally unique."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK used for default bucket encryption."
  type        = string
}

variable "expiration_days" {
  description = "Days after which ticket artefacts expire (budget rule: 7)."
  type        = number
  default     = 7
}

variable "force_destroy" {
  description = "Allow destroy to empty the bucket, including object versions (TEARDOWN.md)."
  type        = bool
  default     = true
}
