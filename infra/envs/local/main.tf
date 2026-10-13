locals {
  env         = "local"
  name_prefix = "tixlane-${local.env}"

  tags = {
    Project    = "tixlane"
    Env        = local.env
    Owner      = var.owner
    CostCenter = "portfolio"
    TTL        = "48h"
    ManagedBy  = "opentofu"
  }
}

data "aws_caller_identity" "current" {}

module "data" {
  source = "../../modules/data"

  name_prefix         = local.name_prefix
  account_id          = data.aws_caller_identity.current.account_id
  region              = var.region
  enable_pitr         = false
  deletion_protection = false
}

module "storage" {
  source = "../../modules/storage"

  name_prefix = local.name_prefix
  account_id  = data.aws_caller_identity.current.account_id
  region      = var.region
  kms_key_arn = module.data.kms_key_arn
}

module "messaging" {
  source = "../../modules/messaging"

  name_prefix                = local.name_prefix
  account_id                 = data.aws_caller_identity.current.account_id
  kms_key_arn                = module.data.kms_key_arn
  visibility_timeout_seconds = var.queue_visibility_timeout_seconds
}

module "auth" {
  source = "../../modules/auth"

  name_prefix     = local.name_prefix
  region          = var.region
  issuer_base_url = var.cognito_issuer_base_url
}

module "compute" {
  source = "../../modules/compute"

  name_prefix            = local.name_prefix
  env                    = local.env
  package_path           = var.lambda_package_path
  python_runtime         = var.python_runtime
  reserved_concurrency   = var.reserved_concurrency
  worker_max_concurrency = var.worker_max_concurrency
  worker_timeout_seconds = var.worker_timeout_seconds
  enable_xray            = var.enable_xray
  auth_mode              = var.auth_mode
  aws_endpoint_url       = var.lambda_aws_endpoint_url
  log_level              = "DEBUG"

  kms_key_arn      = module.data.kms_key_arn
  table_name       = module.data.table_name
  table_arn        = module.data.table_arn
  queue_url        = module.messaging.queue_url
  queue_arn        = module.messaging.queue_arn
  bucket_name      = module.storage.bucket_name
  bucket_arn       = module.storage.bucket_arn
  ticket_topic_arn = module.messaging.ticket_topic_arn
}

module "api" {
  source = "../../modules/api"

  name_prefix          = local.name_prefix
  function_names       = module.compute.function_names
  invoke_arns          = module.compute.invoke_arns
  jwt_issuer           = module.auth.issuer
  jwt_audience         = module.auth.client_id
  cors_allowed_origins = var.cors_allowed_origins
  kms_key_arn          = module.data.kms_key_arn
}

module "observability" {
  source = "../../modules/observability"

  name_prefix      = local.name_prefix
  env              = local.env
  region           = var.region
  api_id           = module.api.api_id
  api_stage        = module.api.stage_name
  function_names   = module.compute.function_names
  queue_name       = module.messaging.queue_name
  dlq_name         = module.messaging.dlq_name
  table_name       = module.data.table_name
  ops_topic_arn    = module.messaging.ops_topic_arn
  enable_dashboard = var.enable_dashboard
}

check "visibility_timeout_covers_worker" {
  assert {
    condition     = var.queue_visibility_timeout_seconds >= 6 * var.worker_timeout_seconds
    error_message = "SQS visibility timeout should be at least 6x the worker timeout."
  }
}
