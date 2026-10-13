output "function_names" {
  description = "Function names keyed by logical name."
  value       = { for key, fn in aws_lambda_function.fn : key => fn.function_name }
}

output "invoke_arns" {
  description = "Invoke ARNs keyed by logical name (API Gateway integrations)."
  value       = { for key, fn in aws_lambda_function.fn : key => fn.invoke_arn }
}

output "role_arns" {
  description = "Execution role ARNs keyed by logical name."
  value       = { for key, role in aws_iam_role.fn : key => role.arn }
}
