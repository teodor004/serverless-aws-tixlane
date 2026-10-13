# ADR-0001: Emulator strategy: Floci primary, MiniStack cross-check, Moto for units

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

TixLane must run end to end without an AWS account (goal 1), and CI must apply the full stack
and run integration tests on every pull request (goal 2). The budget is $0, and the workstation
is Windows 11 + WSL2 with Docker Engine. Lambda functions have to execute for real (in
containers) so that the SQS → worker → S3/SNS path is exercised. That means the emulator needs
the Docker socket.

Which emulator(s) do we target, how do we cope with the features they do not implement, and
where do unit tests get their AWS doubles?

## Decision Drivers

- Zero licence cost, usable in CI without secrets or sign-up.
- Coverage of every service in the stack: API Gateway v2 (HTTP API + JWT authorizer), Cognito,
  Lambda (container execution), DynamoDB (transactions, GSI, TTL), SQS (redrive), SNS, S3, KMS,
  CloudWatch (alarms, dashboards), CloudWatch Logs, IAM, STS.
- The OpenTofu AWS provider must be able to point every service at one endpoint.
- Gaps must be visible and switchable. They must not be papered over silently, and no
  workaround may weaken the real-AWS configuration.
- Fast, hermetic unit tests that don't need Docker.

## Considered Options

1. **Floci as primary, MiniStack as cross-check, Moto for unit tests** (both emulators on port
   4566; health at `/_floci/health` and `/_ministack/health`).
2. LocalStack (Community or with an auth token) as primary.
3. Moto server mode as the only emulator.
4. No emulator: integration tests only against a real AWS sandbox.

## Decision Outcome

Chosen option: **Option 1**. Floci is free, needs no auth token, and listens on the familiar
4566 edge port. Running MiniStack as a second, independent implementation tells us whether a
failure comes from TixLane or from the emulator. Moto keeps the unit suite in-process and fast.
LocalStack stays optional: the current images need an auth token, and the free plan is
non-commercial. Neither fits a public portfolio CI pipeline without secrets.

Implementation:

- `docker-compose.yml` defines `floci` and `ministack` services behind compose **profiles**, so
  exactly one runs at a time (`make up` / `make up EMULATOR=ministack`). Both bind
  `127.0.0.1:4566` only and share the network alias `emulator`.
- `scripts/wait_for_emulator.sh` polls the emulator-specific health path.
- `infra/envs/local/providers.tf` points every provider endpoint at `var.emulator_endpoint`,
  uses dummy credentials and sets `allowed_account_ids = [var.emulator_account_id]`, so this
  root cannot reach real AWS.
- Image names are not hard-coded: `.env` supplies `FLOCI_IMAGE` / `MINISTACK_IMAGE` after they
  have been verified (V-001..V-005). The compose placeholders fail to pull, on purpose.
- Unit tests use Moto (`tests/unit/conftest.py`) with a table definition that mirrors
  `infra/modules/data/main.tf`.

### Gap handling: feature flags, never silent workarounds

Each known or suspected gap has an ID in `docs/EMULATOR_GAPS.md` and a flag whose safe
default is the real-AWS behaviour:

| Gap  | Area                                   | Flag / workaround                                                     |
|------|----------------------------------------|-----------------------------------------------------------------------|
| G-01 | JWT authorizer does not populate claims | `TIXLANE_AUTH_MODE=emulator-unverified`. Accepted only when `TIXLANE_ENV=local` **and** an emulator endpoint is set (`src/tixlane/config.py`); rejected by the `auth_mode` validation in `infra/modules/compute/variables.tf` for any other env |
| G-02 | Issuer URL in emulator tokens           | `cognito_issuer_base_url` → `module.auth.issuer_base_url`             |
| G-03 | API invoke URL format                   | `TIXLANE_API_URL` / `--api-url` override in tests and `scripts/e2e.py` |
| G-04 | X-Ray                                   | `enable_xray = false` locally                                          |
| G-05 | CloudWatch alarm evaluation             | DLQ test falls back to asserting the alarm definition when the emulator does not evaluate alarms |
| G-06 | Reserved concurrency                    | `reserved_concurrency` (may be a no-op locally)                        |
| G-07 | ESM `scaling_config`                    | `worker_max_concurrency = null` omits the block                        |
| G-08 | KMS key policies not enforced           | none. Documented; enforced only on AWS                                 |
| G-09 | DynamoDB PITR                           | `enable_pitr = false` locally                                          |
| G-10 | Python 3.13 runtime missing             | `PY_VERSION=3.12` → `python_runtime = "python3.12"` + 3.12 wheels      |
| G-11 | Lambda containers reaching the emulator | `lambda_aws_endpoint_url = "http://emulator:4566"` (compose network alias) → `TIXLANE_AWS_ENDPOINT_URL` |
| G-12 | IAM policies / permissions boundary     | none locally (`permissions_boundary_arn = null`); enforced on AWS      |
| G-13 | HTTP API access logs                    | documented; logging stays configured                                   |
| G-14 | S3 lifecycle / TLS-only policy          | documented; configured identically, enforced only on AWS               |
| G-15 | CloudWatch dashboard                    | `enable_dashboard`                                                     |

Every gap is *hypothesised* until P2/P3 observe it. A gap that never appears is closed, and its
flag is either removed or kept with a note.

### Consequences

- Good: the full stack, including real Lambda execution, runs offline and in CI for $0.
- Good: two emulators make emulator bugs distinguishable from application bugs.
- Good: insecure fallbacks are fenced by code *and* IaC validation, and default to off.
- Bad: mounting `/var/run/docker.sock` gives the emulator container root-equivalent control of
  the Docker host. Mitigations: the API binds to loopback only, images are pinned after
  verification, the emulator never runs on shared hosts, and CI runners are ephemeral.
- Bad: emulator fidelity for IAM, KMS policies, alarm evaluation and throttling is limited, so
  those controls are proven only in P6/P7 on real AWS.
- Bad: two emulators mean two sets of quirks to track.

## Confirmation

- P2: `make up && make local-apply` creates every resource on Floci, and `make local-plan-check`
  reports an empty second plan. *To be confirmed in P2.* Each gap observed is recorded in
  `docs/EMULATOR_GAPS.md` with its flag.
- P3: `make itest` passes 100 % on Floci. The same suite runs on MiniStack in the
  non-blocking CI job, and the differences are recorded in the table below. *To be confirmed
  in P3.*
- P6: the same code with all flags at their AWS defaults passes smoke on real AWS.

## MiniStack differences

P3 requires the MiniStack results to be recorded here. Every cell stays *pending P3* until the
suite has run against both emulators.

| Capability                                  | Floci       | MiniStack   | Impact      | Flag        |
|---------------------------------------------|-------------|-------------|-------------|-------------|
| `tofu apply` of all modules                 | pending P3  | pending P3  | pending P3  | n/a         |
| Second plan empty                           | pending P3  | pending P3  | pending P3  | n/a         |
| Lambda container execution (python3.13)     | pending P3  | pending P3  | pending P3  | G-10        |
| HTTP API JWT authorizer populates claims    | pending P3  | pending P3  | pending P3  | G-01        |
| Cognito `USER_PASSWORD_AUTH` + groups claim | pending P3  | pending P3  | pending P3  | G-02        |
| API invoke URL usable as returned           | pending P3  | pending P3  | pending P3  | G-03        |
| DynamoDB TransactWriteItems + conditions    | pending P3  | pending P3  | pending P3  | n/a         |
| SQS redrive to DLQ after 3 receives         | pending P3  | pending P3  | pending P3  | n/a         |
| ESM `ReportBatchItemFailures`               | pending P3  | pending P3  | pending P3  | n/a         |
| ESM `scaling_config`                        | pending P3  | pending P3  | pending P3  | G-07        |
| SNS publish (KMS-encrypted topic)           | pending P3  | pending P3  | pending P3  | G-08        |
| S3 put/get with SSE-KMS default             | pending P3  | pending P3  | pending P3  | G-14        |
| CloudWatch alarm evaluation (DLQ depth)     | pending P3  | pending P3  | pending P3  | G-05        |
| Dashboard create                            | pending P3  | pending P3  | pending P3  | G-15        |
| X-Ray                                       | pending P3  | pending P3  | pending P3  | G-04        |

## Pros and Cons of the Options

### Option 1: Floci + MiniStack + Moto
- Good: free, no token, CI-friendly; independent cross-check; fast units.
- Bad: younger projects, so service coverage must be verified service by service (V-001..V-005).

### Option 2: LocalStack primary
- Good: mature, broad coverage, large community.
- Bad: needs an auth token (a CI secret), and the free plan is non-commercial. Both are
  unsuitable for a public pipeline.

### Option 3: Moto server only
- Good: one tool for unit and integration tests.
- Bad: doesn't execute Lambda functions in containers the way the target flow needs, so the
  SQS → worker path would be simulated rather than run.

### Option 4: Real-AWS sandbox only
- Good: perfect fidelity.
- Bad: breaks the $0 rule, needs credentials in CI, and conflicts with "real AWS only via the
  approval-gated deploy workflow".

## More Information

- Code: `docker-compose.yml`, `Makefile` (`up`, `local-apply`, `local-plan-check`, `itest`),
  `infra/envs/local/`, `src/tixlane/config.py`, `infra/modules/compute/variables.tf`,
  `tests/unit/conftest.py`, `tests/integration/`, `scripts/e2e.py`.
- Docs: `docs/EMULATOR_GAPS.md`. VERIFICATION_LOG V-001..V-005 cover emulator images, health
  paths and service coverage; V-006/V-007 cover the provider endpoint configuration and the
  emulator account ID.
- Related: ADR-0003 (unit-test lock vs unlocked emulator concurrency test), ADR-0008 (why
  `infra/envs/local` is outside the scanner gate).
