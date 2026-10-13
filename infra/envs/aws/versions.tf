terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Partial configuration: the account-specific bucket name is passed at init time
  # (`-backend-config="bucket=..."`, from a GitHub environment variable) and never committed.
  # use_lockfile = S3-native locking, no DynamoDB lock table (V-010).
  backend "s3" {
    key          = "tixlane/prod/terraform.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
