terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Local state by design: a human applies this root once (`tofu -chdir=bootstrap apply`)
  # before any workflow can run, and it creates the bucket the other states live in.
  # bootstrap/terraform.tfstate is git-ignored; keep it until TEARDOWN.md step 9.
}
