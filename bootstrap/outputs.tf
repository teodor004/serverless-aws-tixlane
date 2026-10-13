# Copy into GitHub: Settings -> Environments -> production -> Variables, as the UPPER_CASE
# name in each description (README "Real deploy").

output "tf_state_bucket" {
  description = "GitHub variable TF_STATE_BUCKET."
  value       = aws_s3_bucket.state.id
}

output "tf_state_kms_key_arn" {
  description = "GitHub variable TF_STATE_KMS_KEY_ARN."
  value       = aws_kms_key.state.arn
}

output "aws_deploy_role_arn" {
  description = "GitHub variable AWS_DEPLOY_ROLE_ARN."
  value       = module.github_oidc.deploy_role_arn
}

output "workload_boundary_arn" {
  description = "GitHub variable WORKLOAD_BOUNDARY_ARN."
  value       = module.github_oidc.permissions_boundary_arn
}
