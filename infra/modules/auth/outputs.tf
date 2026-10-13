output "user_pool_id" {
  description = "Cognito user pool ID."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "Cognito user pool ARN."
  value       = aws_cognito_user_pool.this.arn
}

output "client_id" {
  description = "App client ID; the JWT authorizer audience."
  value       = aws_cognito_user_pool_client.this.id
}

output "issuer" {
  description = "JWT issuer URL for the API Gateway authorizer."
  value       = "${local.issuer_base_url}/${aws_cognito_user_pool.this.id}"
}
