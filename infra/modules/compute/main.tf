locals {
  functions = {
    api_events = {
      handler     = "tixlane.handlers.api_events.lambda_handler"
      description = "TixLane HTTP: POST /events, GET /events/{id}"
      timeout     = var.api_timeout_seconds
    }
    api_reservations = {
      handler     = "tixlane.handlers.api_reservations.lambda_handler"
      description = "TixLane HTTP: POST /events/{id}/reservations, GET /reservations/{id}"
      timeout     = var.api_timeout_seconds
    }
    worker_tickets = {
      handler     = "tixlane.handlers.worker_tickets.lambda_handler"
      description = "TixLane SQS worker: confirm, write ticket artefact, publish ticket-issued"
      timeout     = var.worker_timeout_seconds
    }
  }

  names = { for key in keys(local.functions) : key => "${var.name_prefix}-${replace(key, "_", "-")}" }

  common_env = merge(
    {
      TABLE_NAME                   = var.table_name
      TIXLANE_ENV                  = var.env
      TIXLANE_AUTH_MODE            = var.auth_mode
      POWERTOOLS_SERVICE_NAME      = "tixlane"
      POWERTOOLS_METRICS_NAMESPACE = "TixLane"
      POWERTOOLS_LOG_LEVEL         = var.log_level
      POWERTOOLS_TRACE_DISABLED    = var.enable_xray ? "false" : "true"
    },
    var.aws_endpoint_url == null ? {} : { TIXLANE_AWS_ENDPOINT_URL = var.aws_endpoint_url },
  )

  function_env = {
    api_events       = {}
    api_reservations = { QUEUE_URL = var.queue_url, IDEMPOTENCY_TTL_SECONDS = "86400" }
    worker_tickets   = { BUCKET_NAME = var.bucket_name, TICKET_TOPIC_ARN = var.ticket_topic_arn }
  }

  # Least privilege, explicit actions only. Every function also writes its own log group and
  # uses the data CMK; DynamoDB transactions need the underlying Put/Update item actions.
  specific_statements = {
    api_events = [
      {
        sid       = "EventItems"
        actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        resources = [var.table_arn]
      },
    ]
    api_reservations = [
      {
        sid       = "ReservationItems"
        actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        resources = [var.table_arn]
      },
      {
        sid       = "EnqueueTickets"
        actions   = ["sqs:SendMessage"]
        resources = [var.queue_arn]
      },
    ]
    worker_tickets = [
      {
        sid       = "ReservationItems"
        actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        resources = [var.table_arn]
      },
      {
        sid       = "ConsumeTickets"
        actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        resources = [var.queue_arn]
      },
      {
        sid       = "WriteTicketArtefacts"
        actions   = ["s3:PutObject"]
        resources = ["${var.bucket_arn}/tickets/*"]
      },
      {
        sid       = "PublishTicketIssued"
        actions   = ["sns:Publish"]
        resources = [var.ticket_topic_arn]
      },
    ]
  }

  statements = {
    for key in keys(local.functions) : key => concat(
      [
        {
          sid       = "WriteOwnLogs"
          actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          resources = ["${aws_cloudwatch_log_group.fn[key].arn}:*"]
        },
        {
          sid       = "UseDataKey"
          actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
          resources = [var.kms_key_arn]
        },
      ],
      # X-Ray has no resource-level permissions, so "*" is the only valid resource.
      var.enable_xray ? [
        {
          sid       = "WriteTraces"
          actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
          resources = ["*"]
        },
      ] : [],
      local.specific_statements[key],
    )
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  #checkov:skip=CKV_AWS_338:Budget rule mandates 7-day retention, not 1 year (ADR-0008)
  for_each = local.functions

  name              = "/aws/lambda/${local.names[each.key]}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each = local.functions

  name                 = local.names[each.key]
  description          = "Execution role for ${local.names[each.key]} only"
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "fn" {
  for_each = local.functions

  dynamic "statement" {
    for_each = local.statements[each.key]

    content {
      sid       = statement.value.sid
      effect    = "Allow"
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions

  name   = "least-privilege"
  role   = aws_iam_role.fn[each.key].id
  policy = data.aws_iam_policy_document.fn[each.key].json
}

resource "aws_lambda_function" "fn" {
  #checkov:skip=CKV_AWS_117:VPC Lambdas are disallowed by the budget rule (no NAT); functions reach only regional AWS APIs (ADR-0008)
  #checkov:skip=CKV_AWS_116:API functions are invoked synchronously; the worker's failure path is the SQS redrive DLQ (ADR-0006, ADR-0008)
  #checkov:skip=CKV_AWS_272:Code signing deferred; the package is built and deployed only by the approval-gated workflow (ADR-0007, ADR-0008)
  for_each = local.functions

  function_name    = local.names[each.key]
  description      = each.value.description
  role             = aws_iam_role.fn[each.key].arn
  runtime          = var.python_runtime
  architectures    = [var.architecture]
  handler          = each.value.handler
  filename         = var.package_path
  source_code_hash = filebase64sha256(var.package_path)
  memory_size      = var.memory_mb
  timeout          = each.value.timeout
  kms_key_arn      = var.kms_key_arn

  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = merge(local.common_env, local.function_env[each.key])
  }

  tracing_config {
    mode = var.enable_xray ? "Active" : "PassThrough"
  }

  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn        = var.queue_arn
  function_name           = aws_lambda_function.fn["worker_tickets"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  dynamic "scaling_config" {
    for_each = var.worker_max_concurrency == null ? [] : [var.worker_max_concurrency]

    content {
      maximum_concurrency = scaling_config.value
    }
  }
}
