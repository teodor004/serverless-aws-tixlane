#trivy:ignore:AVD-AWS-0089
resource "aws_s3_bucket" "artefacts" {
  #checkov:skip=CKV_AWS_18:Server access logging needs a second bucket and adds cost; CloudTrail data events are the documented alternative (ADR-0008)
  #checkov:skip=CKV_AWS_144:Cross-region replication conflicts with the single-region budget rule (ADR-0008)
  #checkov:skip=CKV2_AWS_62:No consumer for S3 event notifications; tickets are announced via SNS ticket-issued (ADR-0008)
  # AVD-AWS-0089 above: same justification as CKV_AWS_18.
  bucket        = "${var.name_prefix}-artefacts-${var.account_id}-${var.region}"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "artefacts" {
  bucket                  = aws_s3_bucket.artefacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artefacts" {
  bucket = aws_s3_bucket.artefacts.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artefacts" {
  bucket = aws_s3_bucket.artefacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    # S3 Bucket Keys cut KMS request volume (and cost) per object.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "artefacts" {
  bucket = aws_s3_bucket.artefacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artefacts" {
  bucket = aws_s3_bucket.artefacts.id

  rule {
    id     = "expire-artefacts"
    status = "Enabled"

    filter {}

    expiration {
      days = var.expiration_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.artefacts]
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artefacts.arn,
      "${aws_s3_bucket.artefacts.arn}/*",
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

  statement {
    sid     = "DenyTlsOlderThan12"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artefacts.arn,
      "${aws_s3_bucket.artefacts.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "NumericLessThan"
      variable = "s3:TlsVersion"
      values   = ["1.2"]
    }
  }
}

resource "aws_s3_bucket_policy" "artefacts" {
  bucket = aws_s3_bucket.artefacts.id
  policy = data.aws_iam_policy_document.tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.artefacts]
}
