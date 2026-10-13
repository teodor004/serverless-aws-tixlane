# Emulator gaps

TixLane must run fully on a local emulator: **Floci** is primary and **MiniStack** is the
cross-check, both on port 4566. Emulators do not reproduce AWS exactly. This file lists every
place where TixLane expects the emulator to differ, how each gap is detected, and the flag
that works around it.

> **Status: every row is a hypothesis.** Neither emulator has been run against this repository
> (offline build). Rows move to *confirmed* or *not a gap* during P2 (local apply) and P3
> (integration tests), with the Floci and MiniStack columns filled in from real runs.
> MiniStack differences are also summarised in ADR-0001.

Rules for every workaround:

- It is a **variable or environment flag**, never a code fork.
- It defaults to the AWS behaviour, and the emulator root (`infra/envs/local`) opts in.
- Anything that weakens security (G-01, G-12) is refused outside `env = "local"`. This is
  enforced by OpenTofu validation and, for G-01, a second time in code.

| ID | capability | expected gap (hypothesis) | detection | workaround / flag | status | Floci | MiniStack |
|---|---|---|---|---|---|---|---|
| G-01 | HTTP API JWT authorizer | Authorizer not enforced, or `requestContext.authorizer.jwt.claims` not populated | Integration test `test_unauthenticated_request_is_rejected`; every API call returns 401 even with a valid token | `var.auth_mode = "emulator-unverified"` → `TIXLANE_AUTH_MODE`. The Lambda decodes the bearer token **without verifying the signature**. Refused by `tixlane.config.Settings` unless `TIXLANE_ENV=local` **and** `TIXLANE_AWS_ENDPOINT_URL` is set; compute-module validation rejects it unless `env = "local"`; the aws root hard-codes `apigw` | hypothesis — confirm in P2/P3 | untested | untested |
| G-02 | Cognito token issuer | The `iss` claim uses an emulator URL, not `https://cognito-idp.<region>.amazonaws.com/<pool>` | Authorizer rejects valid tokens; decode a token and compare `iss` | `var.cognito_issuer_base_url` (auth module `issuer_base_url`) | hypothesis — confirm in P2/P3 | untested | untested |
| G-03 | API invoke URL | `api_endpoint` output is not routable from the host, or mimics an `amazonaws.com` host (which scripts/e2e.py refuses in emulator mode) | `ensure_target_allowed` error, or connection failures in tests | `TIXLANE_API_URL` env var / `--api-url` flag on scripts/e2e.py | hypothesis — confirm in P2/P3 | untested | untested |
| G-04 | X-Ray active tracing | Tracing config unsupported or ignored | Apply error on `tracing_config`, or no traces | `var.enable_xray = false` (local default); also sets `POWERTOOLS_TRACE_DISABLED=true` | hypothesis — confirm in P2/P3 | untested | untested |
| G-05 | CloudWatch alarm evaluation | Alarms are stored but never change state from the metric | DLQ drill: message reaches the DLQ but the alarm stays `OK` / `INSUFFICIENT_DATA` | `TIXLANE_EMULATOR_ALARMS=0` makes the DLQ test assert the alarm **definition** (namespace, metric, dimension, threshold, action) instead of its state. Alarm state is then proven only on AWS in P7 | hypothesis — confirm in P2/P3 | untested | untested |
| G-06 | Lambda reserved concurrency | Accepted but not enforced, or rejected | Apply error, or no throttling under load | `var.reserved_concurrency` (`-1` removes it) | hypothesis — confirm in P2/P3 | untested | untested |
| G-07 | SQS event source `scaling_config` | `maximum_concurrency` unsupported | Apply error, or perpetual diff on the event source mapping | `var.worker_max_concurrency = null` omits the block (local default) | hypothesis — confirm in P2/P3 | untested | untested |
| G-08 | KMS key policies and grants | Key policies and grants not evaluated; encryption may be simulated | Cannot be detected locally | None. Documented; key-policy correctness is validated only on AWS in P6 | hypothesis — confirm in P2/P3 | untested | untested |
| G-09 | DynamoDB point-in-time recovery | `UpdateContinuousBackups` unsupported | Apply error or perpetual diff | `enable_pitr = false` in infra/envs/local | hypothesis — confirm in P2/P3 | untested | untested |
| G-10 | Lambda `python3.13` runtime | Runtime image unavailable | `CreateFunction` error, or runtime failure at first invoke | `PY_VERSION=3.12 make local-apply`: sets `python_runtime=python3.12` and builds cp312 wheels | hypothesis — confirm in P2/P3 | untested | untested |
| G-11 | Lambda containers reaching the emulator | Containers started through the Docker socket cannot resolve or reach the emulator | Worker never confirms; Lambda logs show connection errors | `var.lambda_aws_endpoint_url` (default `http://emulator:4566`, the compose network alias) → `TIXLANE_AWS_ENDPOINT_URL`, plus the emulator's own Lambda-network setting (V-005) | hypothesis — confirm in P2/P3 | untested | untested |
| G-12 | IAM policies and permissions boundaries | Not enforced, so least privilege cannot be proven locally | Cannot be detected locally | `permissions_boundary_arn = null` locally. Least privilege is proven only on AWS (P6), using CloudTrail `AccessDenied` checks | hypothesis — confirm in P2/P3 | untested | untested |
| G-13 | HTTP API access logging | `access_log_settings` accepted but nothing is written, or rejected | No entries in `/aws/apigateway/tixlane-local-http` | Note only. If it is rejected at apply, add a flag in the api module and record it here | hypothesis — confirm in P2/P3 | untested | untested |
| G-14 | S3 lifecycle expiry and the TLS-only bucket policy | Stored but not enforced | Cannot be detected locally (an HTTP endpoint is used) | Documented; validated on AWS (P6 smoke) | hypothesis — confirm in P2/P3 | untested | untested |
| G-15 | CloudWatch dashboards | `PutDashboard` unsupported | Apply error | `var.enable_dashboard = false` | hypothesis — confirm in P2/P3 | untested | untested |

## The "second plan must be empty" rule

`make local-apply` runs `tofu plan -detailed-exitcode` again after apply (`make local-plan-check`),
and fails if it shows changes. Emulators often return attributes in a normalised form, for
example JSON policy whitespace or key order, default values the provider did not send,
lower-cased ARNs, or missing computed fields. That produces a perpetual diff that has nothing
to do with TixLane's code.

When the second plan is not empty:

1. Read `build/second-plan.txt` and find the attribute that changes.
2. Check whether the same diff happens on real AWS (it must not; P6 runs a second plan too).
3. Record it here as a new row: `G-16 … | attribute normalisation on <resource>.<attribute>`.
   Include the exact diff, which emulator shows it, and the chosen fix. Fixes, in order of
   preference:
   1. Write the attribute in the canonical form AWS returns. This is safe everywhere.
   2. Add a local-only flag that omits the attribute on the emulator.
   3. **Never** add `lifecycle { ignore_changes }` to a shared module just to silence an
      emulator. That would hide real drift on AWS.
4. If neither emulator can reach an empty second plan, mark the row *accepted* with a
   reviewer sign-off at the STOP — REVIEW gate.
