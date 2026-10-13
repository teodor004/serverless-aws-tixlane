# ADR-0008: Scanner suppression policy and register

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

CI gates every pull request on tflint, Trivy (which replaces tfsec) and Checkov. Some findings
conflict with hard project rules, not with good practice. The $0 budget forbids VPC Lambdas,
NAT, cross-region replication and long log retention. A 48-hour portfolio stack doesn't
justify code signing or server access logging. Some IAM statements cannot avoid `"*"`.

The brief requires that "any scanner suppression needs an inline justification and an ADR
reference". So how do we suppress findings so that each one is visible where it applies, has
a reason, can be audited in one place, and disappears once it is no longer needed?

## Decision Drivers

- No unexplained suppressions (Definition of Done).
- A reviewer reading a resource should see why a control is absent, without opening another file.
- One register to audit, with a compensating control and a revisit trigger per entry.
- Suppressions must be *proven* necessary, not anticipated: stale ignores hide future findings.
- The gate must cover what can be deployed. Emulator-only configuration must not force
  weaker defaults into the shared modules.

## Considered Options

1. **Inline suppressions + this register + a gate scoped to deployable code.**
2. Central ignore files (`.trivyignore`, a long `skip-check` list in `.checkov.yaml`).
3. Make the scanners non-blocking (report-only) and triage findings by hand.

## Decision Outcome

Chosen option: **Option 1**.

Rules:

1. Syntax: Checkov `#checkov:skip=<ID>:<justification> (ADR-0008)` inside the resource block.
   Trivy `#trivy:ignore:<ID>` on the line immediately above the resource or attribute, with a
   justification comment next to it. `.trivyignore` stays empty.
2. Config-level skips are allowed only when inline suppression is impossible. They live in
   `.checkov.yaml` with a justification comment (currently only CKV_GHA_7).
3. **Scope.** The gate covers `infra/envs/aws`, `bootstrap/` and `.github/workflows`. The
   shared modules are evaluated through the roots that call them. `infra/envs/local` is
   excluded: it only ever targets the emulator, uses dummy credentials, and intentionally turns
   off X-Ray, PITR and the permissions boundary (EMULATOR_GAPS.md). The first local Checkov run
   (2026-10-13) confirmed why `skip-path` is not enough. CKV_AWS_28 (PITR) fired for
   `module.data` *as called from* `infra/envs/local`, but the finding is reported against the
   module file. So Checkov is pointed at the deployable roots (`.checkov.yaml` `directory:`)
   and Trivy skips the directory (`trivy.yaml` `skip-dirs`).
4. Each entry in the register below states the compensating control and a revisit trigger.
5. Every scanner upgrade, and every PR that touches IaC, reconciles the register with actual
   output. Suppressions that don't fire are deleted, new findings are fixed or added with a
   justification, and the register is updated in the same PR. Use the audit below.

### Register (reconciled with real scanner output, 2026-10-13)

Run locally with Checkov 3.3.29 and Trivy 0.75.0 (no AWS access needed). Every suppression
below was proven necessary: the same scan on a copy of the code with all inline skips
stripped reported exactly these findings. Suppressions that never fired were deleted:
- Trivy AVD-AWS-0057 ×5 (key policies, compute role policy, boundary, deploy policies).
- Checkov CKV_AWS_356/CKV_AWS_111 on the compute role policy (cloudsplaining treats the X-Ray
  write actions as not resource-restrictable).

Results with suppressions in place: Checkov Terraform 492 passed / 0 failed / 57 skipped,
GitHub Actions 620 passed / 0 failed. Trivy 0 misconfigurations. (Skip counts are per
evaluation, so a module resource that is called from two roots is counted twice.)

| Scanner | ID | Resource / path | Justification | Compensating control | Revisit when |
|---|---|---|---|---|---|
| Checkov | CKV_AWS_109, CKV_AWS_111, CKV_AWS_356 | `data.aws_iam_policy_document.key` (infra/modules/data), `data.aws_iam_policy_document.state_key` (bootstrap) | In a key policy, `"*"` resource means "this key". The root `kms:*` statement is the AWS default guard against an unmanageable key (ADR-0005) | Function roles get only `kms:Decrypt`/`GenerateDataKey` on the key ARN; the deploy role is tag-conditioned | AWS changes default key-policy guidance |
| Checkov | CKV_AWS_117 | `aws_lambda_function.fn` (×3) | VPC Lambdas are forbidden by the budget (they would need NAT for AWS APIs) | Functions call only regional AWS APIs over TLS; no inbound network path | Budget allows VPC endpoints / NAT |
| Checkov | CKV_AWS_116 | `aws_lambda_function.fn` (×3) | API functions are invoked synchronously (a Lambda DLQ is irrelevant); the worker's failure path is the SQS redrive DLQ (ADR-0006) | DLQ-depth alarm, Lambda Errors alarms | Asynchronous invocation is introduced |
| Checkov | CKV_AWS_272 | `aws_lambda_function.fn` (×3) | Code signing (AWS Signer profile + config) deferred for a 48-hour portfolio stack | Package built and deployed only by the approval-gated workflow from a reviewed commit; deterministic zip hash in the plan (ADR-0007) | Stack becomes long-lived or multi-team |
| Checkov | CKV_AWS_338 | `aws_cloudwatch_log_group.fn` (×3), `aws_cloudwatch_log_group.access` | Budget rule mandates 7-day retention, not ≥ 1 year | Logs are CMK-encrypted; evidence exported to `docs/evidence/` before teardown | Retention rule changes |
| Checkov | CKV_AWS_18 | `aws_s3_bucket.artefacts`, `aws_s3_bucket.state` | Server access logging needs another bucket and adds cost for a 48-hour stack | Block public access, BucketOwnerEnforced, TLS-only policy, CloudTrail management events, versioning | Stack becomes long-lived |
| Checkov | CKV_AWS_144 | same buckets | Cross-region replication conflicts with the single-region (eu-central-1) rule | Versioning + 7-day lifecycle; artefacts are reproducible from DynamoDB | Multi-region requirement |
| Checkov | CKV2_AWS_62 | same buckets | No consumer for S3 event notifications; tickets are announced via SNS `ticket-issued` | n/a | A consumer of object events appears |
| Checkov | CKV_GHA_7 | `.checkov.yaml` (config-level; fires on `destroy.yml` and `ops.yml`) | `destroy.yml` needs a typed confirmation input and `ops.yml` a scenario choice; the check forbids any `workflow_dispatch` input | Both workflows still require `production` environment approval; inputs don't alter build artefacts | Inputs can be replaced by separate workflows |
| Trivy | AWS-0342 | `data.aws_iam_policy_document.iam_compute` (infra/modules/iam_oidc) | The deploy role must pass execution roles to Lambda | `iam:PassRole` only on `tixlane-prod-*` roles with `iam:PassedToService = lambda.amazonaws.com`; those roles must carry the workload boundary; explicit deny on the deploy role itself | Deployment moves to a different mechanism |
| Trivy | AVD-AWS-0089 (rule `aws-s3-enable-logging`) | `aws_s3_bucket.artefacts`, `aws_s3_bucket.state` | Same as CKV_AWS_18 | Same as CKV_AWS_18 | Same as CKV_AWS_18 |

Real findings that were **fixed rather than suppressed** in the same pass:
- Checkov CKV_AWS_356 on the deploy role's read-only statement. cloudsplaining flagged
  `cloudwatch:DescribeAlarms` and `DescribeAlarmHistory` as restrictable, so they moved to
  the alarm-ARN-scoped statement.
- `tofu validate` deprecation of the GSI `hash_key`/`range_key`, replaced with `key_schema`
  blocks.

Trivy accepts both ID styles inline. The legacy `AVD-AWS-0089` was honoured (logged as rule
`aws-s3-enable-logging`), and so was the current `AWS-0342`.

### Suppression audit (repeatable, no AWS needed)

1. Copy `infra/` and `bootstrap/` to a scratch directory and delete every `#checkov:skip=`
   line (or `#trivy:ignore:` line for Trivy).
2. Run the scanner on `infra/envs/aws` and `bootstrap` in the copy.
3. The set of failing `(check, resource)` pairs must equal the register above. Anything
   missing from the output is a stale suppression to delete; anything extra is a new
   finding to fix or register.

### Consequences

- Good: every absent control is explained at the resource, with a single auditable register.
- Good: the register reflects real scanner output. Stale and pre-emptive suppressions were
  removed, so a future regression in those places will fail CI.
- Good: emulator-only shortcuts cannot leak into production defaults, because the shared
  modules default to the secure setting and only the excluded local root turns things off.
- Bad: the scope exclusion means `infra/envs/local` is never scanned. That is acceptable
  because it is unreachable from AWS (dummy credentials, emulator-only endpoints,
  `allowed_account_ids` guard), but it must stay that way.
- Bad: scanner upgrades can rename IDs (Trivy moved from `AVD-AWS-xxxx` to `AWS-xxxx`), so
  every upgrade needs the audit above.

## Confirmation

- P4: CI runs the same commands with the pinned versions in `versions.env`; the gate must be
  green with exactly this register. Confirmed locally on 2026-10-13 (Checkov 3.3.29,
  Trivy 0.75.0, tflint 0.64.0 + ruleset 0.49.0). The CI run itself is still pending.
- Any PR that adds a suppression must also add a row here; review rejects it otherwise.

## Pros and Cons of the Options

### Option 1: Inline + register + scoped gate

- Good: reasons are visible at the point of use; the register gives a single audit view.
- Good: the gate stays blocking, so new findings fail CI.
- Bad: two places to update (inline comment and register). The audit procedure catches drift.

### Option 2: Central ignore files

- Good: one file to read.
- Bad: suppressions are detached from the code they excuse and tend to outlive it.
- Bad: `.trivyignore` and `skip-check` suppress an ID everywhere, not just on one resource.

### Option 3: Non-blocking scanners

- Good: no friction.
- Bad: findings are ignored in practice, which contradicts "CI green with all gates enforced".

## More Information

- Config: `.checkov.yaml`, `trivy.yaml`, `.tflint.hcl`; tool versions in `versions.env`.
- Inline suppressions: `infra/modules/{data,storage,compute,api,iam_oidc}/main.tf`,
  `bootstrap/main.tf`.
- Related: ADR-0005 (key policy), ADR-0006 (DLQ design), ADR-0007 (deploy role, supply chain),
  docs/EMULATOR_GAPS.md (why the local root differs).
- Verification: docs/VERIFICATION_LOG.md V-016 (Trivy), V-017 (Checkov), V-014/V-015 (tflint).
