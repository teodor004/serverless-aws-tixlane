# Teardown runbook

Destroys every TixLane resource in the real AWS account within the 48-hour window, proves
nothing is left using a tag sweep, and records timestamps as evidence.

> **Who does what.** Claude Code never runs any of these steps against real AWS: the
> PreToolUse hook and deny rules block it (CLAUDE.md). The workload is destroyed **only by
> `destroy.yml`**, which is approval-gated. Steps marked **architect-only** are run by
> the architect from their own workstation or the AWS console. The architect deletes the
> state bucket **last**.
>
> **Status:** not executed yet (offline build). Every timestamp is a placeholder.

## Preconditions

- [ ] P7 evidence captured (screenshots + JSON, see [evidence/README.md](evidence/README.md)).
- [ ] No reviewer is mid-review on a `deploy.yml` run (the `tixlane-production` concurrency group).
- [ ] GitHub environment `production` still holds `AWS_DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`,
      `TF_STATE_KMS_KEY_ARN`, `AWS_ACCOUNT_ID` and the other deploy variables.
- [ ] Deploy start time noted, to confirm the stack lived ≤ 48 h: `YYYY-MM-DDThh:mm:ssZ — fill in`

## Steps

### 1. Final evidence snapshot (workflow)
Run **ops.yml**, scenario **snapshot** (read-only: alarm states, Lambda metrics, reserved
concurrency) and approve the `production` gate. Download the artifact into
`docs/evidence/snapshot.json`.

### 2. Destroy the workload (workflow)
Run **destroy.yml** and enter the typed confirmation it asks for (planned:
`destroy tixlane-prod`).
- Approval 1: the `plan -destroy` job. Review the plan summary, which must list only
  `tixlane-prod` resources.
- Approval 2: the job that applies **that saved destroy plan**.
- Record the plan and apply timestamps from the run log in the table below.

### 3. Workload tag sweep (workflow step, or architect with read-only credentials)
`destroy.yml` ends with `scripts/tag_sweep.sh --env prod --out evidence/tag-sweep.json` (uploaded as
a workflow artifact; redact account IDs, then copy it to `docs/evidence/tag-sweep.json`).
It is read-only and exits non-zero unless the count is **0**. KMS keys in `PendingDeletion` are
listed separately under `kms_pending_deletion`. They are expected (7-day window, RISK-02)
and do not count against the result.

### 4. Check what the tagging API can miss
The same sweep output covers these. Confirm both are empty:
- `iam_roles`: no `tixlane-prod-*` roles (IAM is global; V-042).
- `lambda_log_groups`: no `/aws/lambda/tixlane-prod*` groups recreated by late invocations
  (RISK-03). **Architect-only:** if any appear, delete them in the console or CLI.

### 5. Bootstrap teardown (architect-only, manual)
Do this only after steps 2–4 are clean and every artifact you need has been downloaded.
1. Empty the **versioned** state bucket (`tixlane-tfstate-<account>-eu-central-1`). Either:
   - Console: S3 → bucket → **Empty** (this removes all versions and delete markers), or
   - CLI: list with `aws s3api list-object-versions --bucket <state-bucket> --output json`,
     then remove every `Versions[]` and `DeleteMarkers[]` entry with
     `aws s3api delete-objects --bucket <state-bucket> --delete file://<objects.json>`.
2. `tofu -chdir=bootstrap destroy`. This removes the bucket, the GitHub OIDC provider (if
   bootstrap created it), the deploy role and its policies, and the workload boundary, and
   **schedules** the state CMK for deletion (7-day window).

### 6. Full sweep (architect, read-only)
`scripts/tag_sweep.sh --out docs/evidence/tag-sweep-final.json` (no `--env` filter). Expect
**0**, apart from the data CMK and state CMK in `PendingDeletion`.

### 7. Strict sweep after the key window
At least 7 days after steps 2 and 5:
`STRICT=1 scripts/tag_sweep.sh --out docs/evidence/tag-sweep-strict.json`. This must report **0**.

### 8. Cost Explorer check (architect)
Cost Explorer → group by **Service** and filter by tag **Project=tixlane**. Screenshot both
(`docs/evidence/screenshots/cost-explorer-by-service.png`, `…-by-tag.png`) and fill in the
"Estimate vs actual" table in [COST.md](COST.md), including credits applied.

### 9. Remove local bootstrap state (architect)
Delete `bootstrap/terraform.tfstate*` and any `bootstrap/*.auto.tfvars` from the workstation.
They are git-ignored and must never be committed.

### 10. Revoke GitHub access (architect)
In GitHub → Settings → Environments → `production`, delete the variables and secrets
(`AWS_DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`, `TF_STATE_KMS_KEY_ARN`, `WORKLOAD_BOUNDARY_ARN`,
`AWS_ACCOUNT_ID`, `INFRACOST_API_KEY` if set). The role they referenced no longer exists,
but stale values invite confusion.

## Timestamps

| step | event | timestamp (UTC) | evidence |
|---|---|---|---|
| — | deploy.yml apply finished (stack live) | `YYYY-MM-DDThh:mm:ssZ — fill in` | deploy run URL |
| 1 | snapshot captured | `YYYY-MM-DDThh:mm:ssZ — fill in` | evidence/snapshot.json |
| 2 | destroy plan approved | `YYYY-MM-DDThh:mm:ssZ — fill in` | destroy run URL |
| 2 | destroy apply finished | `YYYY-MM-DDThh:mm:ssZ — fill in` | destroy run URL |
| 3 | workload sweep = 0 | `YYYY-MM-DDThh:mm:ssZ — fill in` | evidence/tag-sweep.json |
| 5 | state bucket emptied | `YYYY-MM-DDThh:mm:ssZ — fill in` | (architect note) |
| 5 | bootstrap destroyed | `YYYY-MM-DDThh:mm:ssZ — fill in` | (architect note) |
| 6 | full sweep = 0 (+ pending keys) | `YYYY-MM-DDThh:mm:ssZ — fill in` | evidence/tag-sweep-final.json |
| 7 | strict sweep = 0 | `YYYY-MM-DDThh:mm:ssZ — fill in` | evidence/tag-sweep-strict.json |
| 8 | Cost Explorer reviewed | `YYYY-MM-DDThh:mm:ssZ — fill in` | screenshots/cost-explorer-*.png |
| 10 | GitHub environment cleared | `YYYY-MM-DDThh:mm:ssZ — fill in` | (architect note) |

Total live time (deploy → destroy apply): _fill in_ (must be ≤ 48 h).
