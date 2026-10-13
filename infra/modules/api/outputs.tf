output "api_id" {
  description = "HTTP API ID."
  value       = aws_apigatewayv2_api.this.id
}

output "api_endpoint" {
  description = "Base invoke URL ($default stage, so no stage path segment)."
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "stage_name" {
  description = "Stage name (metric dimension)."
  value       = aws_apigatewayv2_stage.default.name
}
