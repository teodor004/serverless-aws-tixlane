locals {
  region       = "eu-central-1"
  state_bucket = "tixlane-tfstate-${var.aws_account_id}-${local.region}"

  tags = {
    Project    = "tixlane"
    Env        = "bootstrap"
    Owner      = var.owner
    CostCenter = "portfolio"
    TTL        = "48h"
    ManagedBy  = "opentofu"
  }
}

provider "aws" {
  region              = local.region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.tags
  }
}

# State CMK: SSE-KMS for the bucket and the key provider for OpenTofu state/plan encryption.
data "aws_iam_policy_document" "state_key" {
  #checkov:skip=CKV_AWS_109:Key policy - "*" resource means "this key"; root statement is the AWS default guard (ADR-0005, ADR-0008)
  #checkov:skip=CKV_AWS_111:Key policy - see CKV_AWS_109 justification (ADR-0005, ADR-0008)
  #checkov:skip=CKV_AWS_356:Key policy - see CKV_AWS_109 justification (ADR-0005, ADR-0008)
  statement {
    sid    = "EnableIamPoliciesForThisAccount"
    effect = "Allow"
    # Key-policy root statement; IAM policies then grant least privilege (ADR-0005, ADR-0008).
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.aws_account_id}:root"]
    }
  }
}

resource "aws_kms_key" "state" {
  description             = "TixLane OpenTofu state (S3 SSE-KMS + OpenTofu state encryption)"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.state_key.json
}

resource "aws_kms_alias" "state" {
  name          = "alias/tixlane-tfstate"
  target_key_id = aws_kms_key.state.key_id
}

#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:Access logging needs a second bucket; state access is covered by CloudTrail management events and object versioning (ADR-0008)
  #checkov:skip=CKV_AWS_144:Cross-region replication conflicts with the single-region budget rule (ADR-0008)
  #checkov:skip=CKV2_AWS_62:No consumer for state-bucket event notifications (ADR-0008)
  # AVD-AWS-0089 above: same justification as CKV_AWS_18.
  bucket = local.state_bucket
  # Never force-destroy state: TEARDOWN.md deletes this bucket by hand, last.
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

module "github_oidc" {
  source = "../infra/modules/iam_oidc"

  account_id                 = var.aws_account_id
  region                     = local.region
  github_owner               = var.github_owner
  github_repo                = var.github_repo
  existing_oidc_provider_arn = var.existing_oidc_provider_arn
  state_bucket_arn           = aws_s3_bucket.state.arn
  state_kms_key_arn          = aws_kms_key.state.arn
}
