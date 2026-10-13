# ADR-0007: CI/CD with GitHub OIDC and approval-gated plan → apply

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

TixLane deploys to a fresh AWS Free-plan account for 48 hours (P6/P7) and is then destroyed.
The rules: real AWS changes happen **only** through the GitHub deploy/destroy workflows after
the architect's approval; no long-lived AWS keys; no real account IDs or `.tfvars` in git; the
implementation engineer (and Claude Code) never run mutating commands against real AWS.

How do the pipelines authenticate, what are they allowed to do, and how is the reviewed plan
the one that gets applied?

## Decision Drivers

- No static credentials anywhere (repository, runner, laptop).
- Trust narrowed to one repository *and* one approval-protected environment.
- What the reviewer approves must be exactly what is applied.
- Least privilege for the deploy role, including protection against privilege escalation via
  roles it creates.
- Encrypted, locked state with no DynamoDB lock table.
- Supply-chain hygiene for third-party actions and tools.
- Defence in depth against an AI assistant or a human slipping.

## Considered Options

1. **GitHub OIDC → IAM role, trust pinned to `environment:production`, two approval-gated jobs
   (plan → apply of the saved, encrypted plan)**.
2. IAM user access keys stored as GitHub secrets.
3. OIDC with trust on branch (`ref:refs/heads/main`) and a single plan-and-apply job.
4. Local `tofu apply` by the engineer with SSO credentials.

## Decision Outcome

Chosen option: **Option 1**.

### Trust (`infra/modules/iam_oidc/main.tf`)

- OIDC provider `token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.
- Role trust: `StringEquals` on `token.actions.githubusercontent.com:aud = sts.amazonaws.com`
  **and** `:sub = repo:teodor004/serverless-aws-tixlane:environment:production`. Only jobs that declare
  `environment: production` can assume the role. The GitHub environment requires the
  architect's approval before such a job starts.
- `aws-actions/configure-aws-credentials@v6` (pinned to a SHA) with `id-token: write` on those
  jobs only. Sessions last at most 1 h.

### Workflows

- `ci.yml` (PRs, push to main): no AWS credentials at all. Static gates + emulator apply +
  integration tests + k6 smoke on the emulator (ADR-0001, ADR-0008).
- `deploy.yml` (`workflow_dispatch` only): **plan** job (approval 1): build the Lambda zip,
  `tofu plan -out`, publish the plan text to the job summary, and upload the encrypted plan +
  zip as short-lived artifacts. **apply** job (approval 2): download the same artifacts,
  `tofu apply <saved plan>`, then smoke + k6 smoke, and upload evidence. A stale plan (state
  changed since planning) fails rather than silently re-planning.
- `destroy.yml` (`workflow_dispatch`, typed confirmation): `plan -destroy` → approval →
  apply the saved destroy plan → `scripts/tag_sweep.sh`.
- `ops.yml` (`workflow_dispatch`, approval-gated): bounded k6 spike (≤ 50 VUs, ≤ 5 min), DLQ
  drill, read-only evidence snapshot. This is an **extra workflow beyond the brief**. These are
  data-plane actions on real AWS, so they must also go through approval. It can be folded into
  `deploy.yml` if the architect prefers.

### State

- S3 backend in the bootstrap bucket, partial configuration (`bucket` passed at init from a
  GitHub variable), `encrypt = true`, **`use_lockfile = true`** (S3-native locking; V-010).
- **OpenTofu state and plan encryption** (`aws_kms` key provider → `aes_gcm`, `enforced = true`;
  V-011) in `infra/envs/aws/encryption.tofu`. The `.tofu` extension keeps the OpenTofu-only
  block away from Terraform-only scanners, which would otherwise fail to parse it. Plan
  artifacts are encrypted at rest as a result.
- `bootstrap/` (state bucket, state CMK, OIDC provider, deploy role, workload boundary) is
  applied **manually by the architect** with local state, once, after the day-1 account safety
  setup. Its state file is git-ignored.

### Deploy role permissions

- Four managed policies (state-and-read, iam-compute, data-plane, edge). Every action is
  listed explicitly and scoped to `tixlane-prod-*` ARNs, with tag conditions where ARNs are
  unknown in advance (KMS) and `"*"` only for actions without resource-level permissions.
- **Permissions boundary**: `iam:CreateRole`/`PutRolePolicy`/`AttachRolePolicy`/... are
  allowed only when `iam:PermissionsBoundary` equals the workload boundary, which caps what any
  function role can do. `iam:PassRole` is restricted to `lambda.amazonaws.com`.
- An explicit **Deny** stops the role from removing or replacing boundaries, editing the
  boundary policy, or modifying its own trust and policies.
- `allowed_account_ids` in the provider refuses any other account.

### Supply chain and versions

- Every `uses:` must be pinned to a 40-character commit SHA (`scripts/check_action_pins.sh`
  gate). Tool versions live in `versions.env` (`scripts/check_versions.sh` gate). Both were
  pinned and verified on 2026-10-13: actions to the SHAs of their newest releases, tools to
  checksum-verified releases (V-016, V-021). Both gates pass.

### Second line of defence: Claude Code guardrails

`.claude/settings.json` deny rules plus a PreToolUse hook (`.claude/hooks/guard.py`) block
`tofu apply/destroy/import` outside `infra/envs/local`, non-read-only `aws` CLI calls without a
local `--endpoint-url`, `--allow-real-aws`, commits that gitleaks flags, and staging of state,
`.tfvars` or `.env` files.

### Consequences

- Good: no long-lived credentials; trust is bound to an environment that requires approval.
- Good: the reviewed plan is byte-for-byte the applied plan; state and plans are encrypted.
- Good: the boundary closes the "create a more powerful role and pass it" escalation path.
- Bad: two approvals per deploy (plan, then apply). That's deliberate friction.
- Bad: **"Prevent self-review" must stay off** because the architect is the only reviewer and
  also dispatches the workflows. This is an accepted risk for a solo project, mitigated by OIDC
  pinning, the boundary and short sessions. Revisit if a second reviewer joins.
- Bad: the explicit action lists will probably need iteration after the first real apply
  (missing read actions surface as AccessDenied). Each fix is a reviewed bootstrap change.
- Bad: the bootstrap state is local, so the architect's machine holds it until teardown.

## Confirmation

- P4: `ci.yml` green on a PR with SHA pins and pinned versions. *To be confirmed in P4.*
- P6: first `deploy.yml` run: approval 1 → plan summary reviewed → approval 2 → apply of the
  saved plan → smoke evidence. CloudTrail shows `AssumeRoleWithWebIdentity` with the expected
  `sub`. *To be confirmed in P6.*
- P7: `destroy.yml` + tag sweep = 0 (KMS pending deletion handled per ADR-0005). *To be
  confirmed in P7.*
- Negative check (P6): a job **without** `environment: production` fails to assume the role.
  *To be confirmed in P6.*

## Pros and Cons of the Options

### Option 1: OIDC + environment-pinned trust + saved plan
- Good: no secrets; approval gate is also the trust boundary; reviewed plan is applied.
- Bad: two approvals; explicit policies need maintenance.

### Option 2: Access keys in secrets
- Good: simplest to set up.
- Bad: long-lived credentials, rotation burden, leak risk; violates the brief.

### Option 3: Branch-pinned trust, single job
- Good: one approval.
- Bad: any workflow on `main` could assume the role, and plan/apply in one job means
  approving before seeing the plan.

### Option 4: Local apply
- Good: fastest feedback.
- Bad: forbidden by the rules; no audit trail of approval; credentials on a laptop.

## More Information

- Code: `.github/workflows/{ci,deploy,destroy,ops}.yml`, `infra/modules/iam_oidc/`,
  `bootstrap/`, `infra/envs/aws/{versions.tf,encryption.tofu,providers.tf}`,
  `scripts/check_action_pins.sh`, `scripts/check_versions.sh`, `versions.env`,
  `.claude/settings.json`, `.claude/hooks/guard.py`.
- Related: ADR-0005 (state CMK), ADR-0008 (suppressions on deploy-role `"*"` statements).
- VERIFICATION_LOG: V-008 (OpenTofu version), V-010 (`use_lockfile`), V-011 (state
  encryption), V-021 (action pins), V-036 (OIDC thumbprint optional) and the
  configure-aws-credentials v6 inputs are all unverified (offline build).
