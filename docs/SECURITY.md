# Security policy

## Supported scope

TixLane is a portfolio reference architecture. Its intended lifecycle is about 48 hours on real
AWS, then a full teardown. In scope:

- the application code in `src/tixlane/`;
- the infrastructure code in `infra/` and `bootstrap/`;
- the workflows in `.github/workflows/`;
- the developer guardrails in `.claude/`.

Only the `main` branch is supported.

## Reporting a vulnerability

Open a **private security advisory** on the GitHub repository (Security → Advisories → Report a
vulnerability). Do not open a public issue. Contact for follow-up: `<security-contact-placeholder>`.
Acknowledgement is targeted within 5 working days. No bounty is offered.

## Controls summary

The detail for each control is in [THREAT_MODEL.md](THREAT_MODEL.md). Nothing has been
executed yet, so every control is implemented but unverified until the phase gates.

| Area | Control | Where |
|---|---|---|
| Authentication | Cognito user pool, admin-create-only, 14-character complex passwords, optional TOTP MFA; HTTP API JWT authorizer on every route | `infra/modules/auth/main.tf`, `infra/modules/api/main.tf` |
| Authorisation | Groups `organisers` / `attendees` from `cognito:groups`, enforced per route; access tokens only; "not yours" returns 404 | `src/tixlane/auth.py`, `src/tixlane/services.py` |
| Least privilege | One role per Lambda, explicit actions, ARN-scoped; deploy role split into 4 scoped policies | `infra/modules/compute/main.tf`, `infra/modules/iam_oidc/main.tf` |
| Permissions boundary | Workload roles must carry `tixlane-prod-workload-boundary`; the deploy role cannot create roles without it, remove it, or edit itself | `infra/modules/iam_oidc/main.tf` |
| Federation | GitHub OIDC only; trust `sub` pinned to `repo:teodor004/serverless-aws-tixlane:environment:production`; no long-lived AWS keys | `infra/modules/iam_oidc/main.tf`, [ADR-0007](adr/0007-cicd-oidc-approval-gated-deploys.md) |
| Encryption at rest | Customer-managed KMS key (rotation on) for DynamoDB, SQS, SNS, S3 (with bucket keys), CloudWatch Logs and Lambda env; a separate state CMK for S3 SSE-KMS and OpenTofu state + plan encryption | `infra/modules/data/main.tf`, `bootstrap/main.tf`, `infra/envs/aws/encryption.tofu`, [ADR-0005](adr/0005-kms-customer-managed-key.md) |
| Encryption in transit | Deny when `aws:SecureTransport = false` on S3, SQS and SNS; S3 also denies TLS < 1.2 | `infra/modules/storage/main.tf`, `infra/modules/messaging/main.tf` |
| Exposure | S3 Block Public Access + BucketOwnerEnforced; CORS explicit allow-list (`*` rejected); stage throttling | `infra/modules/storage/main.tf`, `infra/modules/api/` |
| Input validation | Pydantic with bounds and `extra="forbid"`; UUID path normalisation; parameterised DynamoDB expressions | `src/tixlane/models.py`, `src/tixlane/http.py` |
| Static analysis | tflint, Trivy (misconfig), Checkov (terraform + github_actions), ruff (incl. bandit rules), mypy strict; suppressions inline with justification + ADR | CI, [ADR-0008](adr/0008-scanner-suppressions.md) |

## Secrets policy

- Never in git: secrets, tokens, real AWS account ids, real `.tfvars`, state files, `.env`.
  `.gitignore` blocks them. Only `*.example` files with AWS documentation placeholder values
  (`123456789012`) are committed.
- Account-specific values (account id, state bucket, role ARNs, boundary ARN, state CMK ARN)
  live in GitHub **environment variables** of `production`, not in code.
- `gitleaks` runs as a pre-commit hook and in CI. It uses the default rules plus a custom rule
  that flags 12-digit account ids inside ARNs, with an allow-list for documentation placeholders
  (`.gitleaks.toml`).
- Test users get random passwords that are masked in CI logs, and they are deleted after every
  run (`scripts/e2e.py`).

## Claude Code guardrails

Defined in `.claude/settings.json` and `.claude/hooks/guard.py`:

- **Deny rules** for clearly dangerous commands, such as applying the real-AWS roots, force-pushing,
  and reading real variable or state files and credentials.
- A **PreToolUse hook** on shell commands. It blocks:
  - `tofu`/`terraform` `apply`, `destroy` and `import` (and state-mutating sub-commands) unless
    the target is `infra/envs/local`;
  - `aws` CLI operations that are not read-only, unless `--endpoint-url` points at the local
    emulator;
  - any use of `--allow-real-aws`;
  - commits that skip hooks (`--no-verify`) or force-add ignored files;
  - commits whose staged changes fail gitleaks or include `.tfvars`, state, `.env` or key files.
    This check fails closed if gitleaks is missing.
- The same hook on file writes and edits blocks secret-like file names and content: AWS access
  key ids, private-key headers, GitHub tokens, and real account ids in ARNs.

These are defence in depth. The primary control is that real AWS changes happen only through
approval-gated workflows, and workstations hold no write credentials for the account.

## Supply chain

- Every third-party GitHub Action is pinned to a full commit SHA, with its tag in a comment. The
  CI preflight (`scripts/check_action_pins.sh`) fails on mutable references.
- Tool versions live in `versions.env`. Each was verified against its official release page
  (checksums checked for downloaded binaries) and logged in
  [VERIFICATION_LOG.md](VERIFICATION_LOG.md) on 2026-10-13. The preflight
  (`scripts/check_versions.sh`) fails if a `PIN_ME` placeholder is reintroduced.
- Trivy runs from a pinned container image rather than a wrapper action. Before pinning a Trivy
  version, check the project's security advisories and release-integrity notes for reported
  compromises of its distribution channels. This is recalled, not verified, and is tracked as
  V-016.
- Python dependencies are locked with `uv.lock` (generated in P0) and installed with
  `--only-binary`. Emulator images are pinned by digest once verified.

## Accepted risks

| Risk | Why accepted | Revisit when |
|---|---|---|
| "Prevent self-review" is off on environment `production` | There is a single maintainer, who is both dispatcher and required reviewer | A second maintainer joins |
| Docker socket mounted into the emulator | Required for emulators to run Lambdas in containers; bound to `127.0.0.1`, verified images only | An emulator offers a socket-less mode |
| `USER_PASSWORD_AUTH` enabled on the app client (THREAT_MODEL S-3) | Scripted sign-in for tests, smoke and k6 without an SRP client library | Scripted sign-in is no longer needed; set `allow_user_password_auth = false` |
| No WAF | HTTP APIs cannot attach WAF, and WAF is excluded by the $0 budget; stage throttling and reserved concurrency limit blast radius | The API moves to REST API or CloudFront |
| No VPC for Lambdas | VPC Lambdas would need NAT or endpoints (excluded by budget); functions call only regional AWS APIs | Private resources are introduced |
| No S3 access logs / CloudTrail data events | Cost and extra buckets; management events and app logs cover the 48-hour window | Longer-lived deployment |
| 7-day log retention | Budget rule | Longer-lived deployment |
