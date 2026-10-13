# Emulator-only provider. Every endpoint points at the emulator and the credentials are the
# emulator's dummy pair, so this root cannot reach real AWS. allowed_account_ids is a second
# guard: real AWS never answers GetCallerIdentity with the emulator account (V-006, V-007).
provider "aws" {
  region     = var.region
  access_key = "test"
  secret_key = "test"

  allowed_account_ids = [var.emulator_account_id]

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
  s3_use_path_style           = true

  endpoints {
    apigatewayv2             = var.emulator_endpoint
    cloudwatch               = var.emulator_endpoint
    cognitoidp               = var.emulator_endpoint
    dynamodb                 = var.emulator_endpoint
    iam                      = var.emulator_endpoint
    kms                      = var.emulator_endpoint
    lambda                   = var.emulator_endpoint
    logs                     = var.emulator_endpoint
    resourcegroupstaggingapi = var.emulator_endpoint
    s3                       = var.emulator_endpoint
    sns                      = var.emulator_endpoint
    sqs                      = var.emulator_endpoint
    sts                      = var.emulator_endpoint
  }

  default_tags {
    tags = local.tags
  }
}
