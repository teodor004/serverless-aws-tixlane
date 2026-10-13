resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name_prefix}-tickets-dlq"
  message_retention_seconds         = 1209600 # 14 days: outlives the source queue's retention
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
}

resource "aws_sqs_queue" "tickets" {
  name                              = "${var.name_prefix}-tickets"
  visibility_timeout_seconds        = var.visibility_timeout_seconds
  message_retention_seconds         = 345600 # 4 days
  receive_wait_time_seconds         = 20
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.tickets.arn]
  })
}

data "aws_iam_policy_document" "queue_tls_only" {
  for_each = {
    tickets = aws_sqs_queue.tickets.arn
    dlq     = aws_sqs_queue.dlq.arn
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [each.value]

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

resource "aws_sqs_queue_policy" "tickets" {
  queue_url = aws_sqs_queue.tickets.id
  policy    = data.aws_iam_policy_document.queue_tls_only["tickets"].json
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.queue_tls_only["dlq"].json
}

resource "aws_sns_topic" "ticket_issued" {
  name              = "${var.name_prefix}-ticket-issued"
  kms_master_key_id = var.kms_key_arn
}

resource "aws_sns_topic" "ops_alerts" {
  name              = "${var.name_prefix}-ops-alerts"
  kms_master_key_id = var.kms_key_arn
}

data "aws_iam_policy_document" "ticket_issued" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ticket_issued.arn]

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

data "aws_iam_policy_document" "ops_alerts" {
  statement {
    sid       = "AllowCloudWatchAlarmsFromThisAccount"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]

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

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]

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

resource "aws_sns_topic_policy" "ticket_issued" {
  arn    = aws_sns_topic.ticket_issued.arn
  policy = data.aws_iam_policy_document.ticket_issued.json
}

resource "aws_sns_topic_policy" "ops_alerts" {
  arn    = aws_sns_topic.ops_alerts.arn
  policy = data.aws_iam_policy_document.ops_alerts.json
}

resource "aws_sns_topic_subscription" "ops_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
