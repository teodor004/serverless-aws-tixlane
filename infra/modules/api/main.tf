locals {
  # Route key => function that serves it. Every route requires a valid Cognito access token;
  # group membership is enforced in the handlers from the token's cognito:groups claim.
  routes = {
    "POST /events"                   = "api_events"
    "GET /events/{id}"               = "api_events"
    "POST /events/{id}/reservations" = "api_reservations"
    "GET /reservations/{id}"         = "api_reservations"
  }

  api_functions = toset(distinct(values(local.routes)))
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name_prefix}-http"
  description   = "TixLane public HTTP API"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins     = var.cors_allowed_origins
    allow_methods     = ["GET", "POST", "OPTIONS"]
    allow_headers     = ["authorization", "content-type", "idempotency-key"]
    expose_headers    = ["location", "idempotent-replayed", "retry-after"]
    allow_credentials = false
    max_age           = 300
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.this.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = var.jwt_issuer
    audience = [var.jwt_audience]
  }
}

resource "aws_apigatewayv2_integration" "fn" {
  for_each = local.api_functions

  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.invoke_arns[each.key]
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "this" {
  for_each = local.routes

  api_id             = aws_apigatewayv2_api.this.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.fn[each.value].id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_cloudwatch_log_group" "access" {
  #checkov:skip=CKV_AWS_338:Budget rule mandates 7-day retention, not 1 year (ADR-0008)
  name              = "/aws/apigateway/${var.name_prefix}-http"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
    # Per-route metrics are billed as custom metrics; stage-level metrics are enough (COST.md).
    detailed_metrics_enabled = false
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      requestTime        = "$context.requestTime"
      sourceIp           = "$context.identity.sourceIp"
      routeKey           = "$context.routeKey"
      status             = "$context.status"
      protocol           = "$context.protocol"
      responseLength     = "$context.responseLength"
      responseLatency    = "$context.responseLatency"
      integrationLatency = "$context.integration.latency"
      integrationError   = "$context.integrationErrorMessage"
      authorizerError    = "$context.authorizer.error"
      sub                = "$context.authorizer.claims.sub"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  for_each = local.api_functions

  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.function_names[each.key]
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
