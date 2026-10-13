output "deploy_role_arn" {
  description = "Set as the GitHub environment variable AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}

output "permissions_boundary_arn" {
  description = "Boundary every workload role must carry (passed to infra/envs/aws)."
  value       = aws_iam_policy.boundary.arn
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider ARN."
  value       = local.provider_arn
}
