terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # Local state on purpose: this root only ever targets the emulator.
  backend "local" {
    path = "terraform.tfstate"
  }
}
