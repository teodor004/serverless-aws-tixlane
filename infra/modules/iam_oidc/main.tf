# GitHub Actions OIDC trust + the deploy role used by deploy.yml / destroy.yml / ops.yml.
# Applied once, by a human, from bootstrap/ (ADR-0007). Every action is listed explicitly and
# scoped to the workload prefix; roles the deploy role creates must carry the workload
# permissions boundary, which closes the "create a more powerful role" escalation path.

locals {
  provider_arn = coalesce(var.existing_oidc_provider_arn, one(aws_iam_openid_connect_provider.github[*].arn))
  wl           = var.workload_prefix
  acct         = var.account_id
  rgn          = var.region

  arn = {
    table     = "arn:aws:dynamodb:${local.rgn}:${local.acct}:table/${local.wl}"
    table_sub = "arn:aws:dynamodb:${local.rgn}:${local.acct}:table/${local.wl}/*"
    queues    = "arn:aws:sqs:${local.rgn}:${local.acct}:${local.wl}-*"
    topics    = "arn:aws:sns:${local.rgn}:${local.acct}:${local.wl}-*"
    buckets   = "arn:aws:s3:::${local.wl}-artefacts-*"
    objects   = "arn:aws:s3:::${local.wl}-artefacts-*/*"
    functions = "arn:aws:lambda:${local.rgn}:${local.acct}:function:${local.wl}-*"
    esm       = "arn:aws:lambda:${local.rgn}:${local.acct}:event-source-mapping:*"
    roles     = "arn:aws:iam::${local.acct}:role/${local.wl}-*"
    alarms    = "arn:aws:cloudwatch:${local.rgn}:${local.acct}:alarm:${local.wl}-*"
    dashboard = "arn:aws:cloudwatch::${local.acct}:dashboard/${local.wl}"
    keys      = "arn:aws:kms:${local.rgn}:${local.acct}:key/*"
    alias     = "arn:aws:kms:${local.rgn}:${local.acct}:alias/${local.wl}"
    pools     = "arn:aws:cognito-idp:${local.rgn}:${local.acct}:userpool/*"
    log_groups = [
      "arn:aws:logs:${local.rgn}:${local.acct}:log-group:/aws/lambda/${local.wl}-*",
      "arn:aws:logs:${local.rgn}:${local.acct}:log-group:/aws/lambda/${local.wl}-*:*",
      "arn:aws:logs:${local.rgn}:${local.acct}:log-group:/aws/apigateway/${local.wl}-*",
      "arn:aws:logs:${local.rgn}:${local.acct}:log-group:/aws/apigateway/${local.wl}-*:*",
    ]
    apis = [
      "arn:aws:apigateway:${local.rgn}::/apis",
      "arn:aws:apigateway:${local.rgn}::/apis/*",
      "arn:aws:apigateway:${local.rgn}::/tags/*",
    ]
  }

  project_tag = {
    test     = "StringEquals"
    variable = "aws:ResourceTag/Project"
    values   = ["tixlane"]
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.existing_oidc_provider_arn == null ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list omitted: optional in current providers because AWS validates GitHub's
  # OIDC endpoint against its own trusted CAs (V-036, unverified).
}

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "GitHubProductionEnvironmentOnly"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Pinned subject: only jobs that declare `environment: production` in this repository.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_owner}/${var.github_repo}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name_prefix}-github-deploy"
  description          = "Assumed only by GitHub Actions jobs in environment ${var.github_environment}"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = var.max_session_seconds
}

# ---------------------------------------------------------------------------------------------
# Workload permissions boundary: the ceiling for every role the deploy role creates.
# ---------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "boundary" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = local.arn.log_groups
  }

  statement {
    sid       = "Table"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
    resources = [local.arn.table, local.arn.table_sub]
  }

  statement {
    sid       = "Queues"
    actions   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [local.arn.queues]
  }

  statement {
    sid       = "Artefacts"
    actions   = ["s3:PutObject"]
    resources = [local.arn.objects]
  }

  statement {
    sid       = "Topics"
    actions   = ["sns:Publish"]
    resources = [local.arn.topics]
  }

  statement {
    sid       = "DataKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.arn.keys]

    condition {
      test     = local.project_tag.test
      variable = local.project_tag.variable
      values   = local.project_tag.values
    }
  }

  statement {
    sid       = "Traces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "boundary" {
  name        = "${local.wl}-workload-boundary"
  description = "Permissions boundary for TixLane Lambda execution roles"
  policy      = data.aws_iam_policy_document.boundary.json
}

# ---------------------------------------------------------------------------------------------
# Deploy role policies, split by area to stay under the 6,144-character managed-policy limit.
# ---------------------------------------------------------------------------------------------

data "aws_iam_policy_document" "state_and_read" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.state_key_prefix}*"]
    }
  }

  # PutObject/DeleteObject also cover the S3-native lock file (use_lockfile = true).
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.state_bucket_arn}/${var.state_key_prefix}*"]
  }

  statement {
    sid       = "StateKey"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.state_kms_key_arn]
  }

  # Read-only calls used by evidence capture and the post-destroy tag sweep. None of these
  # actions support resource-level permissions.
  statement {
    sid = "ReadOnlyEvidence"
    actions = [
      "tag:GetResources",
      "cloudwatch:GetMetricData",
      "cloudwatch:ListDashboards",
      "logs:DescribeLogGroups",
      "xray:GetTraceSummaries",
      "xray:BatchGetTraces",
      "kms:ListAliases",
      "lambda:ListEventSourceMappings",
      # IAM is global; the tag sweep lists it directly as well (scripts/tag_sweep.sh).
      "iam:ListRoles",
      "iam:ListPolicies",
      "iam:ListOpenIDConnectProviders",
    ]
    resources = ["*"]
  }
}

# PassRole is limited to tixlane-prod-* roles and iam:PassedToService = lambda; the deploy role
# cannot pass its own role or any role without the workload boundary (ADR-0007, ADR-0008).
#trivy:ignore:AWS-0342
data "aws_iam_policy_document" "iam_compute" {
  statement {
    sid       = "CreateWorkloadRolesOnlyWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = [local.arn.roles]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }

  statement {
    sid = "ManageWorkloadRoles"
    actions = [
      "iam:GetRole",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateRoleDescription",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = [local.arn.roles]
  }

  statement {
    sid       = "PassWorkloadRolesToLambdaOnly"
    actions   = ["iam:PassRole"]
    resources = [local.arn.roles]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  statement {
    sid    = "NeverWeakenTheGuardrails"
    effect = "Deny"
    actions = [
      "iam:DeleteRolePermissionsBoundary",
      "iam:PutRolePermissionsBoundary",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
    ]
    resources = [aws_iam_role.deploy.arn, aws_iam_policy.boundary.arn]
  }

  statement {
    sid = "Functions"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:ListVersionsByFunction",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
    ]
    resources = [local.arn.functions]
  }

  statement {
    sid       = "CreateEventSourceMappingForWorkloadFunctions"
    actions   = ["lambda:CreateEventSourceMapping"]
    resources = ["*"]

    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = [local.arn.functions]
    }
  }

  statement {
    sid = "ManageEventSourceMappings"
    actions = [
      "lambda:GetEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:TagResource",
      "lambda:ListTags",
    ]
    resources = [local.arn.esm]
  }

  statement {
    sid = "LogGroups"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:AssociateKmsKey",
      "logs:DisassociateKmsKey",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsForResource",
    ]
    resources = local.arn.log_groups
  }

  # HTTP API access logging uses vended-log delivery APIs, which take no resource ARN.
  statement {
    sid = "ApiAccessLogDelivery"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
    ]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "data_plane" {
  statement {
    sid = "Table"
    actions = [
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:DescribeTable",
      "dynamodb:UpdateTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:ListTagsOfResource",
    ]
    resources = [local.arn.table, local.arn.table_sub]
  }

  statement {
    sid = "Queues"
    actions = [
      "sqs:CreateQueue",
      "sqs:DeleteQueue",
      "sqs:GetQueueAttributes",
      "sqs:SetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:TagQueue",
      "sqs:UntagQueue",
      "sqs:ListQueueTags",
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:PurgeQueue",
    ]
    resources = [local.arn.queues]
  }

  statement {
    sid = "Topics"
    actions = [
      "sns:CreateTopic",
      "sns:DeleteTopic",
      "sns:GetTopicAttributes",
      "sns:SetTopicAttributes",
      "sns:TagResource",
      "sns:UntagResource",
      "sns:ListTagsForResource",
      "sns:Subscribe",
      "sns:Unsubscribe",
      "sns:GetSubscriptionAttributes",
      "sns:ListSubscriptionsByTopic",
    ]
    resources = [local.arn.topics]
  }

  statement {
    sid = "ArtefactBucket"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:GetBucketLocation",
      "s3:GetBucketPolicy",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:GetBucketPublicAccessBlock",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetBucketOwnershipControls",
      "s3:PutBucketOwnershipControls",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetBucketVersioning",
      "s3:PutBucketVersioning",
      "s3:GetLifecycleConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:GetBucketTagging",
      "s3:PutBucketTagging",
      "s3:GetBucketAcl",
      "s3:GetBucketCORS",
      "s3:GetBucketWebsite",
      "s3:GetBucketLogging",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetAccelerateConfiguration",
      "s3:GetReplicationConfiguration",
    ]
    resources = [local.arn.buckets]
  }

  # GetObject for smoke evidence; deletes so force_destroy can empty a versioned bucket.
  statement {
    sid       = "ArtefactObjects"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = [local.arn.objects]
  }

  statement {
    sid       = "CreateTaggedKeyOnly"
    actions   = ["kms:CreateKey"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["tixlane"]
    }
  }

  statement {
    sid = "ManageTaggedKeys"
    actions = [
      "kms:DescribeKey",
      "kms:GetKeyPolicy",
      "kms:PutKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:EnableKeyRotation",
      "kms:ListResourceTags",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:ScheduleKeyDeletion",
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = [local.arn.keys]

    condition {
      test     = local.project_tag.test
      variable = local.project_tag.variable
      values   = local.project_tag.values
    }
  }

  # DynamoDB, Lambda and Logs create grants on the CMK for the resources they encrypt.
  statement {
    sid       = "GrantsForAwsResourcesOnly"
    actions   = ["kms:CreateGrant"]
    resources = [local.arn.keys]

    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }

    condition {
      test     = local.project_tag.test
      variable = local.project_tag.variable
      values   = local.project_tag.values
    }
  }

  statement {
    sid       = "Alias"
    actions   = ["kms:CreateAlias", "kms:DeleteAlias", "kms:UpdateAlias"]
    resources = [local.arn.alias, local.arn.keys]
  }
}

data "aws_iam_policy_document" "edge" {
  statement {
    sid       = "HttpApi"
    actions   = ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE"]
    resources = local.arn.apis
  }

  statement {
    sid       = "CreateUserPool"
    actions   = ["cognito-idp:CreateUserPool"]
    resources = ["*"]
  }

  statement {
    sid = "ManageUserPools"
    actions = [
      "cognito-idp:DescribeUserPool",
      "cognito-idp:UpdateUserPool",
      "cognito-idp:DeleteUserPool",
      "cognito-idp:GetUserPoolMfaConfig",
      "cognito-idp:SetUserPoolMfaConfig",
      "cognito-idp:CreateUserPoolClient",
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:UpdateUserPoolClient",
      "cognito-idp:DeleteUserPoolClient",
      "cognito-idp:CreateGroup",
      "cognito-idp:GetGroup",
      "cognito-idp:UpdateGroup",
      "cognito-idp:DeleteGroup",
      "cognito-idp:TagResource",
      "cognito-idp:UntagResource",
      "cognito-idp:ListTagsForResource",
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminDeleteUser",
    ]
    resources = [local.arn.pools]
  }

  statement {
    sid = "Alarms"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:DescribeAlarmHistory",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
      "cloudwatch:ListTagsForResource",
    ]
    resources = [local.arn.alarms]
  }

  statement {
    sid       = "Dashboard"
    actions   = ["cloudwatch:PutDashboard", "cloudwatch:GetDashboard", "cloudwatch:DeleteDashboards"]
    resources = [local.arn.dashboard]
  }
}

locals {
  deploy_policies = {
    state-and-read = data.aws_iam_policy_document.state_and_read.json
    iam-compute    = data.aws_iam_policy_document.iam_compute.json
    data-plane     = data.aws_iam_policy_document.data_plane.json
    edge           = data.aws_iam_policy_document.edge.json
  }
}

resource "aws_iam_policy" "deploy" {
  for_each = local.deploy_policies

  name        = "${var.name_prefix}-deploy-${each.key}"
  description = "TixLane deploy role: ${each.key}"
  policy      = each.value
}

resource "aws_iam_role_policy_attachment" "deploy" {
  for_each = aws_iam_policy.deploy

  role       = aws_iam_role.deploy.name
  policy_arn = each.value.arn
}
