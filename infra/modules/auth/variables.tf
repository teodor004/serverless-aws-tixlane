variable "name_prefix" {
  description = "Prefix for every resource name, e.g. tixlane-prod."
  type        = string
}

variable "region" {
  description = "AWS region, used to build the JWT issuer URL."
  type        = string
}

variable "issuer_base_url" {
  description = "Override for the JWT issuer base URL (emulator only, EMULATOR_GAPS G-02). Null = https://cognito-idp.<region>.amazonaws.com."
  type        = string
  default     = null
}

variable "allow_user_password_auth" {
  description = "Enable USER_PASSWORD_AUTH for scripted sign-in by tests and smoke runs (THREAT_MODEL S-3)."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "User pool deletion protection. Off: the stack is destroyed after 48 h."
  type        = bool
  default     = false
}
