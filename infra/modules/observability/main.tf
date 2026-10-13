# 8 alarms / 9 alarm metrics in total, inside the CloudWatch free tier of 10 (COST.md).

locals {
  actions = [var.ops_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "api_5xx_rate" {
  alarm_name          = "${var.name_prefix}-api-5xx-rate"
  alarm_description   = "More than ${var.api_5xx_rate_threshold_percent}% of API requests returned 5xx over 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.api_5xx_rate_threshold_percent
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions

  metric_query {
    id          = "rate"
    expression  = "IF(requests > 0, 100 * errors / requests, 0)"
    label       = "5xx rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"

    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "5xx"
      period      = 300
      stat        = "Sum"
      dimensions = {
        ApiId = var.api_id
        Stage = var.api_stage
      }
    }
  }

  metric_query {
    id = "requests"

    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "Count"
      period      = 300
      stat        = "Sum"
      dimensions = {
        ApiId = var.api_id
        Stage = var.api_stage
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = var.function_names

  alarm_name          = "${each.value}-errors"
  alarm_description   = "${each.value} reported invocation errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = var.function_names

  alarm_name          = "${each.value}-throttles"
  alarm_description   = "${each.value} was throttled (reserved concurrency reached)."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.name_prefix}-dlq-depth"
  alarm_description   = "Poison messages are waiting in ${var.dlq_name}. Inspect, fix, then redrive or purge."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

locals {
  function_list = values(var.function_names)

  alarm_arns = concat(
    [aws_cloudwatch_metric_alarm.api_5xx_rate.arn, aws_cloudwatch_metric_alarm.dlq_depth.arn],
    [for alarm in aws_cloudwatch_metric_alarm.lambda_errors : alarm.arn],
    [for alarm in aws_cloudwatch_metric_alarm.lambda_throttles : alarm.arn],
  )

  api_dims = ["ApiId", var.api_id, "Stage", var.api_stage]

  dashboard = {
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = "# TixLane (${var.env})\nHTTP API -> Lambda -> DynamoDB + SQS -> worker -> S3 + SNS ticket-issued. Every alarm notifies **ops-alerts**."
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6
        properties = {
          title   = "API requests and errors"
          region  = var.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 60
          metrics = [for name in ["Count", "4xx", "5xx"] : concat(["AWS/ApiGateway", name], local.api_dims)]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 2
        width  = 12
        height = 6
        properties = {
          title  = "API latency"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            for stat in ["p50", "p95", "p99"] :
            concat(["AWS/ApiGateway", "Latency"], local.api_dims, [{ stat = stat, label = "Latency ${stat}" }])
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Lambda invocations, errors, throttles"
          region = var.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60
          metrics = flatten([
            for fn in local.function_list : [
              for name in ["Invocations", "Errors", "Throttles"] :
              ["AWS/Lambda", name, "FunctionName", fn, { label = "${fn} ${name}" }]
            ]
          ])
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Lambda duration p95"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            for fn in local.function_list :
            ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "p95", label = fn }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 12
        height = 6
        properties = {
          title  = "Queues"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.queue_name, { stat = "Maximum" }],
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.queue_name, { stat = "Maximum" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name, { stat = "Maximum", label = "DLQ depth" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 14
        width  = 12
        height = 6
        properties = {
          title  = "DynamoDB"
          region = var.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 60
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", var.table_name],
            ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", var.table_name],
            ["AWS/DynamoDB", "ConditionalCheckFailedRequests", "TableName", var.table_name, { label = "Conditional check failed (sold out / replay)" }],
          ]
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 20
        width  = 24
        height = 4
        properties = {
          title  = "Alarms"
          alarms = local.alarm_arns
        }
      },
    ]
  }
}

resource "aws_cloudwatch_dashboard" "this" {
  count = var.enable_dashboard ? 1 : 0

  dashboard_name = var.name_prefix
  dashboard_body = jsonencode(local.dashboard)
}
