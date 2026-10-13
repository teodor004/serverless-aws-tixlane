locals {
  groups = {
    organisers = "Create and read events; read reservations for own events"
    attendees  = "Read events; create and read own reservations"
  }

  issuer_base_url = coalesce(var.issuer_base_url, "https://cognito-idp.${var.region}.amazonaws.com")
}

resource "aws_cognito_user_pool" "this" {
  name                     = var.name_prefix
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = var.deletion_protection ? "ACTIVE" : "INACTIVE"
  mfa_configuration        = "OPTIONAL"

  # No self sign-up: accounts are provisioned by an administrator or the smoke workflow.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 1
  }

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  username_configuration {
    case_sensitive = false
  }
}

resource "aws_cognito_user_group" "this" {
  for_each = local.groups

  name         = each.key
  description  = each.value
  user_pool_id = aws_cognito_user_pool.this.id
}

# Public client (no secret) for browser/CLI callers. Access tokens carry `cognito:groups`.
resource "aws_cognito_user_pool_client" "this" {
  name            = "${var.name_prefix}-client"
  user_pool_id    = aws_cognito_user_pool.this.id
  generate_secret = false

  explicit_auth_flows = concat(
    ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    var.allow_user_password_auth ? ["ALLOW_USER_PASSWORD_AUTH"] : [],
  )

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 1

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}
