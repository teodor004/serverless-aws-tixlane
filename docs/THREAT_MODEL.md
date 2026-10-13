# Threat model (STRIDE per trust boundary)

Scope: the TixLane workload, its delivery pipeline, and the developer and emulator environment.
Status values:

- **implemented-unverified**: the control exists in code, but no test, scan or deploy has run
  yet (offline build).
- **planned**: not implemented yet.

Each control is expected to be confirmed in the phase noted in
[WELL_ARCHITECTED.md](WELL_ARCHITECTED.md).

## Assets

| Asset | Why it matters |
|---|---|
| Event capacity / `remaining` | Overselling is the core business failure |
| Reservations and ticket artefacts | Proof of purchase; tampering or forgery means fraud |
| Cognito identities and tokens | Grant API access by group |
| Deploy role and OpenTofu state | Control of the whole AWS account footprint |
| Data CMK and state CMK | Loss means data loss; misuse means disclosure |
| Repository and CI secrets (none long-lived by design) | Supply-chain entry point |
| Developer workstation and emulator host | Holds the repo; the Docker socket gives root-equivalent access |

## Assumptions

- The account is dedicated to TixLane, never joined to an AWS Organization, and `eu-central-1` only.
- Root MFA, the admin user and budget alerts are set up by the architect on day 1 (P6).
- No long-lived AWS access keys exist for CI. Workstations hold no AWS credentials for this
  account, or read-only ones at most.
- GitHub environment `production` has the architect as required reviewer.
- Ticket artefacts and notifications contain identifiers only, never names, e-mail addresses
  or payment data.

## Trust boundaries

```
TB1 Internet ──► API Gateway (HTTP API + JWT authorizer)
TB2 API Gateway ──► Lambda (api_events, api_reservations)
TB3 Lambda ──► DynamoDB / S3 / KMS
TB4 SQS ──► worker_tickets (asynchronous)
TB5 SNS ticket-issued / ops-alerts ──► subscribers
TB6 GitHub Actions ──► AWS (OIDC → STS → deploy role)
TB7 Developer workstation / Claude Code ──► repository and AWS
TB8 Emulator host (Docker socket mounted into the emulator)
```

## Threats

### TB1 — Internet ↔ API Gateway

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| S-1 | Forged, expired or wrong-audience JWT | JWT authorizer checks issuer, audience = app client, signature, expiry (`infra/modules/api/main.tf`) | Depends on API Gateway's validation | implemented-unverified |
| S-2 | ID token used as a bearer token (it also passes the authorizer) | Handlers accept only `token_use = access` (`src/tixlane/auth.py`) | — | implemented-unverified |
| S-3 | `USER_PASSWORD_AUTH` is enabled on the app client for scripted sign-in (tests, smoke, k6), so the password is sent to Cognito (over TLS) instead of an SRP proof | No self sign-up (`allow_admin_create_user_only`), 14-character complex password policy, test users are ephemeral (random passwords, deleted after each run), MFA available; disable with `allow_user_password_auth = false` once scripted sign-in is not needed (`infra/modules/auth/main.tf`, `scripts/e2e.py`) | A phished password works without SRP; low because no real users exist | implemented-unverified (accepted, see SECURITY.md) |
| T-1 | Malicious payloads: injection, oversized values, unexpected fields | Pydantic models with bounds and `extra="forbid"`; UUID-normalised path ids; DynamoDB expression values are parameterised, never concatenated (`src/tixlane/models.py`, `src/tixlane/http.py`, `src/tixlane/repository.py`) | — | implemented-unverified |
| R-1 | Caller denies having made a reservation | Reservation stores `attendee_id` (`sub`); JSON access logs record request id, route, status, `sub`; idempotency record keeps the request hash | Logs kept for only 7 days by budget rule | implemented-unverified |
| I-1 | Enumerating other users' reservations | UUIDv4 ids; "not yours" returns 404, the same as "missing" (`src/tixlane/services.py`) | — | implemented-unverified |
| I-2 | Cross-origin abuse from a hostile site | CORS allow-list of explicit origins; `*` rejected by variable validation; no credentials mode (`infra/modules/api/variables.tf`) | CORS is not authentication; tokens still matter | implemented-unverified |
| D-1 | Request flood exhausts Lambda or budget | Stage throttling 10 rps / burst 20; reserved concurrency 5 per function; budget alerts | No WAF (unsupported on HTTP APIs, disallowed by budget); throttling also affects legitimate users | implemented-unverified |
| D-2 | One attendee hoards seats with many reservations | `quantity` ≤ 10 per request | No per-user cap or payment step | planned (per-user cap) |
| E-1 | Attendee calls organiser-only routes | Groups enforced from `cognito:groups` in every handler (`src/tixlane/handlers/*.py`); unit tests for 403 | Group membership is administered manually | implemented-unverified |

### TB2 — API Gateway ↔ Lambda

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| S-4 | Something other than this API invokes the API functions | `aws_lambda_permission` scoped to this API's execution ARN (`infra/modules/api/main.tf`) | Principals in the account with `lambda:InvokeFunction` (only admin) | implemented-unverified |
| E-2 | Emulator-only `emulator-unverified` auth mode (decodes the bearer token without verifying it) reaches real AWS | Three guards: `Settings.from_env` raises unless `TIXLANE_ENV=local` and an emulator endpoint is set (`src/tixlane/config.py`); tofu validation rejects the mode unless `env = "local"` (`infra/modules/compute/variables.tf`); the aws root hard-codes `auth_mode = "apigw"` (`infra/envs/aws/main.tf`); unit tests cover the refusal | A future refactor could remove a guard; keep the tests | implemented-unverified |
| T-2 | Claims injected through client headers | In `apigw` mode, claims are read only from `requestContext.authorizer.jwt.claims`, which API Gateway populates | — | implemented-unverified |

### TB3 — Lambda ↔ DynamoDB / S3 / KMS

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| T-3 | Concurrent requests oversell an event | Single conditional `TransactWriteItems` (`remaining >= q`), idempotency claimed in the same transaction ([ADR-0003](adr/0003-conditional-capacity-write.md), [ADR-0004](adr/0004-idempotency-in-transaction.md)) | — | implemented-unverified |
| T-4 | Tampering with ticket artefacts | Only the worker role has `s3:PutObject` on `tickets/*`; BPA, BucketOwnerEnforced, versioning, TLS-only policy (`infra/modules/storage/main.tf`) | Admin principals can still write | implemented-unverified |
| E-3 | A compromised function reaches other data | One role per function, explicit actions, ARN-scoped resources; permissions boundary on AWS (`infra/modules/compute/main.tf`, `infra/modules/iam_oidc/main.tf`) | X-Ray actions need `*` (ADR-0008) | implemented-unverified |
| I-3 | Disclosure of data at rest | CMK on table, queues, topics, bucket, log groups and Lambda environment ([ADR-0005](adr/0005-kms-customer-managed-key.md)) | Root key-policy statement delegates to IAM (AWS default) | implemented-unverified |
| I-4 | Disclosure in transit | `aws:SecureTransport` deny on S3/SQS/SNS; S3 also denies TLS < 1.2 | — | implemented-unverified |
| D-3 | CMK disabled or deleted, making data unrecoverable | 7-day deletion window; only the deploy role (tag-scoped) and admin can schedule deletion | Intentional at teardown | implemented-unverified |
| R-2 | Data-plane changes cannot be attributed | CloudTrail management events (default event history); application logs carry `sub` and ids | CloudTrail data events are not enabled (cost) | planned (optional) |

### TB4 — SQS ↔ worker (asynchronous)

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| S-5 | Forged queue message confirms a reservation that was never accepted | Only `api_reservations` (and the deploy role, for the DLQ drill) can `sqs:SendMessage`; the worker requires a stored reservation whose `event_id` matches, and confirms only `PENDING` (`src/tixlane/services.py`) | A forged message can at most confirm an already-accepted reservation early | implemented-unverified |
| T-5 | Duplicate delivery double-confirms | Conditional transitions (`status = PENDING`, `attribute_not_exists(notified_at)`); deterministic ticket codes | Duplicate SNS publish possible (T-6) | implemented-unverified |
| D-4 | Poison message loops forever | `maxReceiveCount = 3` → DLQ; partial batch failures; DLQ-depth alarm ([ADR-0006](adr/0006-async-confirmation-sqs-dlq.md)) | Operator must triage the DLQ | implemented-unverified |
| D-5 | Worker throttled; confirmations stall | ESM max concurrency 2; throttles alarm; visibility timeout ≥ 6 × timeout | Confirmation latency grows under load ([COST.md](COST.md) RISK-01) | implemented-unverified |

### TB5 — SNS → subscribers

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| S-6 | Unauthorised publish to `ticket-issued` | Only the worker role has `sns:Publish`; TLS-only topic policy (`infra/modules/messaging/main.tf`) | — | implemented-unverified |
| T-6 | Duplicate `ticket-issued` notifications | At-least-once by design; `reservation_id` is the dedupe key (ADR-0006) | Subscribers must deduplicate | implemented-unverified |
| I-5 | PII leaks through notifications or alarm e-mails | Payload carries ids, quantity and object key only; topics encrypted with the CMK | Alarm e-mails contain resource names | implemented-unverified |

### TB6 — GitHub Actions ↔ AWS (OIDC)

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| S-7 | Fork, other branch or other repo assumes the deploy role | Trust policy requires `aud = sts.amazonaws.com` and `sub = repo:teodor004/serverless-aws-tixlane:environment:production` (`infra/modules/iam_oidc/main.tf`) | Anyone with write access who can trigger an environment job still needs reviewer approval | implemented-unverified |
| E-4 | Deploy role escalates by creating a more powerful role | `CreateRole`/`PutRolePolicy`/`AttachRolePolicy` only with the workload boundary; deny on removing boundaries, editing the boundary policy, or modifying the deploy role itself; the workload prefix `tixlane-prod-*` excludes the deploy role's name | The policy surface is large; review with Access Analyzer in P6 | implemented-unverified |
| T-7 | Compromised third-party action or tool release | Actions pinned to full commit SHAs (CI preflight `scripts/check_action_pins.sh`); tool versions `PIN_ME` until verified (`versions.env`); minimal `permissions:` per job | SHA pins still trust the pinned code | implemented-unverified |
| T-8 | Plan swapped or edited between plan and apply | Saved plan encrypted with OpenTofu plan encryption (state CMK, `infra/envs/aws/encryption.tofu`); short artifact retention; separate approval before apply; tofu rejects stale plans | — | implemented-unverified |
| R-3 | Unattributed deploy | Environment approval history; role session name includes the run id; CloudTrail `AssumeRoleWithWebIdentity` events | — | implemented-unverified |
| I-6 | Tokens or passwords in workflow logs | `::add-mask::` for generated passwords and tokens (`scripts/e2e.py`); no long-lived secrets; evidence files never contain tokens | Masking depends on correct use | implemented-unverified |
| D-6 | State corruption or stuck lock | S3 versioning on the state bucket; `use_lockfile`; force-unlock is a manual human step | — | implemented-unverified |

### TB7 — Developer workstation / Claude Code ↔ repository and AWS

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| E-5 | The coding agent (or a tired human) runs a real-AWS mutation | `.claude/settings.json` deny rules + PreToolUse hook `.claude/hooks/guard.py`; local root uses dummy credentials and `allowed_account_ids = ["000000000000"]`; `scripts/e2e.py` refuses real AWS outside GitHub Actions | Command parsing is best-effort; the strongest control is having no AWS credentials on the workstation | implemented-unverified |
| I-7 | Secrets or real account ids committed | gitleaks pre-commit + CI with a custom ARN account-id rule (`.gitleaks.toml`); `.gitignore` for `.tfvars`/state/`.env`; the hook blocks secret-like writes and unsafe staging | Novel secret formats | implemented-unverified |
| T-9 | Hooks bypassed (`--no-verify`) | The guard hook blocks `--no-verify`; CI gitleaks is the backstop | A human can bypass the local hooks | implemented-unverified |

### TB8 — Emulator host (Docker socket)

| ID | Threat | Mitigation (file) | Residual risk | Status |
|---|---|---|---|---|
| E-6 | The emulator container has root-equivalent host access through the Docker socket | Only verified, pinned images (`.env.example` placeholders until V-001/V-003); WSL2 isolation from Windows | Inherent to emulators that run Lambdas in containers | implemented-unverified (accepted) |
| I-8 | Emulator API reachable from the LAN | Port bound to `127.0.0.1` only (`docker-compose.yml`) | — | implemented-unverified |
| T-10 | Malicious or tampered emulator image | Image left unset until verified; pin by digest | Trust in the emulator project | planned (digest pin in P0) |

## Out of scope

- Payment processing and refunds; seat maps; event cancellation.
- Abuse-prevention products (WAF, bot control, Shield Advanced): excluded by the budget, and WAF
  cannot be attached to HTTP APIs.
- Multi-region resilience and DR.
- AWS-side compromise, or compromise of the architect's own GitHub or AWS root credentials.

## Review triggers

Revisit this model when a route, a trust policy, an IAM statement, or the emulator auth mode
changes, and at every STOP — REVIEW from P3 onwards.
