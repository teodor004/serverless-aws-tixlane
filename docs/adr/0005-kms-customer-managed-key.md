# ADR-0005: One customer-managed KMS key per environment

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

The brief requires KMS encryption on the DynamoDB table, the SQS queues and the S3 bucket, and
asks for an ADR on **customer-managed vs AWS-owned keys**. CloudWatch Logs, the SNS topics and
Lambda environment variables can use the same choice. The budget is $0 net (Free-plan credits
may absorb small list-price charges), and the stack lives for 48 hours. OpenTofu state is
encrypted too (ADR-0007).

Which key types do we use, how many, and with what key policy?

## Decision Drivers

- Control: a key policy we own, rotation, the ability to revoke access, and CloudTrail records
  of key usage.
- Compatibility: CloudWatch alarms must be able to publish to an **encrypted** SNS topic
  (ops-alerts). That requires a key policy that grants `cloudwatch.amazonaws.com`.
- Scanner expectations (Checkov/Trivy prefer CMKs for DynamoDB, SQS, SNS, Logs, S3).
- Cost: as close to $0 as possible over 48 h, with credits covering the remainder.
- Clean teardown: keys can't be deleted immediately (7-30-day waiting period).

## Considered Options

1. **One customer-managed key (CMK) per environment** for all workload data, plus a separate
   state CMK in `bootstrap/`.
2. AWS-owned keys (the service default) everywhere.
3. AWS-managed keys (`aws/dynamodb`, `aws/sqs`, `aws/sns`, `aws/s3`, ...).
4. One CMK per service.

## Decision Outcome

Chosen option: **Option 1**. It is the only option that lets CloudWatch alarms publish to an
encrypted SNS topic, gives us a key policy and CloudTrail visibility, and satisfies the
scanners. One key per environment keeps the cost to a single prorated key fee.

Implementation (`infra/modules/data/main.tf`):

- `aws_kms_key.this` + `alias/tixlane-<env>`; `enable_key_rotation = true`;
  `deletion_window_in_days = 7` (the minimum).
- Used by: DynamoDB SSE, SQS (`kms_master_key_id`, 300 s data-key reuse), SNS
  (`kms_master_key_id`), S3 default encryption (`aws:kms` with **S3 Bucket Keys** on, to cut KMS
  requests), CloudWatch log groups (`kms_key_id`), Lambda environment variables
  (`kms_key_arn`).
- Key policy, three statements:
  1. **Account root `kms:*`**: the AWS default statement that hands control to IAM policies
     and prevents an unmanageable key. Function roles then get only `kms:Decrypt` and
     `kms:GenerateDataKey` on this key ARN (`infra/modules/compute/main.tf`). The deploy role
     manages the key only via tag-conditioned actions (ADR-0007).
  2. **CloudWatch Logs** service principal (`logs.<region>.amazonaws.com`), restricted by the
     `kms:EncryptionContext:aws:logs:arn` condition to this account's log groups.
  3. **CloudWatch alarms** (`cloudwatch.amazonaws.com`): `kms:Decrypt` + `kms:GenerateDataKey`,
     with `aws:SourceAccount` set to this account, so alarms can publish to encrypted topics.
- State: `bootstrap/main.tf` creates a separate `alias/tixlane-tfstate` CMK. It encrypts the
  state bucket and backs the OpenTofu `aws_kms` key provider. It outlives the workload stack and
  is scheduled for deletion last (TEARDOWN.md).

### Cost (list price, unverified, VERIFICATION_LOG)

| Item                            | Assumption                                   | 48-hour estimate |
|---------------------------------|----------------------------------------------|------------------|
| Workload CMK                    | ~$1 per key-month, prorated                  | ≈ $0.07          |
| State CMK                       | lives a few days longer than the stack        | ≈ $0.10-0.20     |
| KMS requests                    | inside the monthly free request allowance; S3 Bucket Keys and SQS data-key reuse reduce calls | $0.00 |
| Net after Free-plan credits     | credits absorb list-price charges              | $0.00 (to be confirmed in P7) |

Open questions, logged in VERIFICATION_LOG and to be confirmed in P0/P7:

- Whether a key in **PendingDeletion** still accrues the monthly fee during its 7-day window.
- Whether the Resource Groups Tagging API still lists keys in PendingDeletion. If it does, the
  post-destroy tag sweep would never reach a literal 0 inside the window. `scripts/tag_sweep.sh`
  reports such keys separately (`STRICT=1` counts them), and TEARDOWN.md schedules a final sweep
  after the window.
- Exact IAM requirements for DynamoDB and Lambda use of a CMK (grants vs caller permissions).
  The deploy role allows `kms:CreateGrant` only with `kms:GrantIsForAWSResource = true`.

### Consequences

- Good: one place to revoke access to all workload data; rotation on; usage visible in
  CloudTrail.
- Good: CloudWatch → encrypted SNS works by design rather than by accident.
- Good: scanner checks for CMK encryption are satisfied without suppressions; the root
  statement needs a documented suppression instead (ADR-0008).
- Bad: a small, non-zero list price (credit-covered) and a mandatory 7-day deletion window that
  extends teardown evidence beyond 48 h.
- Bad: one key for all services couples their blast radius. That's acceptable for one
  environment with one team; per-service keys are the upgrade path.
- Bad: a key-policy mistake can lock out the key's users, which the emulator doesn't
  enforce (EMULATOR_GAPS G-08). It is proven only on real AWS in P6.

## Confirmation

- P2: the key, alias and every encrypted resource create on Floci (policy not enforced; G-08).
  *To be confirmed in P2.*
- P4: Checkov/Trivy raise no CMK findings for DynamoDB/SQS/SNS/S3/Logs; the root-statement
  suppressions match the ADR-0008 register. *To be confirmed in P4.*
- P6: smoke passes end to end on AWS, and a DLQ drill (P7) shows the alarm publishing to the
  encrypted ops-alerts topic. *To be confirmed in P6/P7.*
- P7: COST.md records actual KMS charges vs the estimate; TEARDOWN.md records the
  PendingDeletion state and the final sweep after the window.

## Pros and Cons of the Options

### Option 1: One CMK per environment
- Good: full control, alarms → encrypted SNS, CloudTrail usage, scanner-friendly.
- Bad: ~$1/key-month prorated; 7-day deletion window.

### Option 2: AWS-owned keys
- Good: free, zero management, deleted with the resource.
- Bad: no key policy, no CloudTrail key-usage events, can't grant CloudWatch alarms access
  for encrypted SNS. Scanners flag every resource.

### Option 3: AWS-managed keys
- Good: free key storage, visible in the account.
- Bad: key policies can't be edited. The AWS-managed SNS key can't be used by CloudWatch
  alarms, so ops-alerts would have to be unencrypted. Scanners still prefer CMKs.

### Option 4: CMK per service
- Good: smallest blast radius per key.
- Bad: 5-6× the key fees and policies for no benefit in a single-team, single-environment
  portfolio stack.

## More Information

- Code: `infra/modules/data/main.tf` (key, alias, policy), `infra/modules/compute/main.tf`
  (`UseDataKey` statement), `infra/modules/storage/main.tf` (bucket key),
  `infra/modules/messaging/main.tf`, `bootstrap/main.tf` (state key),
  `infra/envs/aws/encryption.tofu`, `scripts/tag_sweep.sh`.
- Related: ADR-0007 (state encryption, deploy-role KMS permissions), ADR-0008 (root-statement
  suppressions: CKV_AWS_109/111/356; the Trivy wildcard check did not fire).
- Docs: `docs/COST.md`, `docs/TEARDOWN.md`, `docs/VERIFICATION_LOG.md` (KMS pricing, deletion
  window, Free-plan credits, tagging-API behaviour; all unverified).
