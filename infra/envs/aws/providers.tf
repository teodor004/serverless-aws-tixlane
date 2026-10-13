provider "aws" {
  region = "eu-central-1"

  # Refuses to run against any account except the dedicated portfolio account.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.tags
  }
}
