# OpenTofu client-side state and plan encryption (V-011), on top of SSE-KMS on the bucket.
#
# Kept in a .tofu file deliberately: OpenTofu reads it, while Terraform-only tools (tflint,
# Checkov, Trivy) skip it instead of failing on the OpenTofu-specific `encryption` block.
# The key is the state CMK created by bootstrap/ and outlives this stack (TEARDOWN.md).

variable "state_kms_key_id" {
  description = "ARN of the state CMK from bootstrap (GitHub environment variable TF_STATE_KMS_KEY_ARN)."
  type        = string
}

terraform {
  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id = var.state_kms_key_id
      region     = "eu-central-1"
      key_spec   = "AES_256"
    }

    method "aes_gcm" "state" {
      keys = key_provider.aws_kms.state
    }

    state {
      method   = method.aes_gcm.state
      enforced = true
    }

    # Saved plans travel between workflow jobs as artifacts, so they are encrypted too.
    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }
}
