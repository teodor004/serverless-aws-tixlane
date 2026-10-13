# One customer-managed key encrypts the table, queues, topics, bucket, log groups and Lambda
# environment variables. Rationale and cost in docs/adr/0005-kms-customer-managed-key.md.

data "aws_iam_policy_document" "key" {
  #checkov:skip=CKV_AWS_109:Key policy - "*" resource means "this key" and the root statement is the AWS default guard against an unmanageable key (ADR-0005, ADR-0008)
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
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
  }

  statement {
    sid    = "AllowCloudWatchLogsForThisAccount"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:DescribeKey",
    ]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }

    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.region}:${var.account_id}:log-group:*"]
    }
  }

  # CloudWatch alarms publish to the encrypted ops-alerts topic.
  statement {
    sid       = "AllowCloudWatchAlarmsToUseEncryptedTopics"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "${var.name_prefix} data key (DynamoDB, SQS, SNS, S3, Logs, Lambda env)"
  enable_key_rotation     = true
  deletion_window_in_days = var.kms_deletion_window_days
  policy                  = data.aws_iam_policy_document.key.json
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name_prefix}"
  target_key_id = aws_kms_key.this.key_id
}

# Single-table design (ADR-0002):
#   EVENT#<id>            / META   event, carries `remaining`
#   RES#<id>              / META   reservation; GSI1PK=EVENT#<event_id>, GSI1SK=RES#<created_at>#<id>
#   IDEMP#<sub>#<key>     / META   idempotency record, expires via TTL on `expires_at`
# tests/unit/factories.py mirrors this definition for Moto.
resource "aws_dynamodb_table" "this" {
  name                        = var.name_prefix
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  range_key                   = "SK"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  # key_schema blocks replace the GSI-level hash_key/range_key deprecated in provider 6.x (V-043).
  global_secondary_index {
    name            = "GSI1"
    projection_type = "ALL"

    key_schema {
      attribute_name = "GSI1PK"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "GSI1SK"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_pitr
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.this.arn
  }
}
