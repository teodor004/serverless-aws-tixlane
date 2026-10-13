# Cost

> **NOT RUN.** Infracost has not been executed (offline build, no API key), nothing has been
> deployed, and every figure below is an **estimate, unverified (V-024, V-040, V-041)**,
> worked out from memory of public list prices. P4 replaces the estimate with Infracost
> output, and P7 adds the actual figures from Cost Explorer.

## Budget rules

- Fresh AWS **Free-plan** account, never joined to an AWS Organization. Region **eu-central-1** only.
- Target: **net spend $0.00**, with any usage covered by Free-plan credits and reported.
- Not allowed: NAT gateway, VPC Lambdas, public IPv4 / Elastic IPs, RDS, provisioned
  concurrency, WAF.
- Required: DynamoDB on-demand, 7-day log retention, 7-day S3 lifecycle.
- Live window: **48 hours** (every resource is tagged `TTL=48h`), then TEARDOWN.md.
- Guardrails the architect configures on day 1 (P6): a $1 budget with alerts at 50/80/100%
  plus a forecast alert, Free Tier usage alerts, and Cost Explorer.

## Method

1. `make infracost` (CI job `infracost`) runs `infracost breakdown --config-file infracost.yml`
   against **infra/envs/aws**. It parses HCL directly, so no AWS credentials are needed.
   Usage comes from `infracost-usage.yml`: a 30-day month of portfolio traffic (smoke runs
   plus one bounded spike).
2. Infracost reports monthly **list price**. It does not model free tiers or credits.
3. Prorate to the live window: `monthly × 48 / 730`. The state CMK lives longer (bootstrap
   through the end of teardown, estimated at about 5 days), so it is prorated separately.
4. Subtract free-tier allowances and Free-plan credits to get **net**.

## Estimate (48-hour window)

All numbers: **estimate, unverified (V-024, V-040, V-041)**.

| service | driver | est. 48 h list price | free tier / credits | net |
|---|---|---|---|---|
| KMS — data CMK | 1 key, about $1/month, prorated hourly; requests well under the free allowance | ≈ $0.07 | Free-plan credits (V-024) | $0.00 |
| KMS — state CMK (bootstrap) | 1 key, about $1/month, about 5 days incl. teardown; plus the 7-day `PendingDeletion` window if billed (V-041) | ≈ $0.17–0.40 | Free-plan credits | $0.00 |
| CloudWatch alarms | 8 alarms = 9 alarm metrics (the 5xx-rate metric-math alarm counts 2) at about $0.10 per alarm metric per month | ≈ $0.06 | 10 alarm metrics free (V-040) | $0.00 |
| CloudWatch dashboard | 1 dashboard, fewer than 50 metrics | $0.00 | 3 dashboards free | $0.00 |
| CloudWatch Logs | 4 log groups, KB–MB of ingest, 7-day retention | ≈ $0.00 | Free ingest/storage allowance | $0.00 |
| Lambda | 3 functions, 256 MB, at most a few thousand invocations (smoke + spike), reserved concurrency (no provisioned concurrency) | ≈ $0.00 | 1M requests + 400k GB-s monthly allowance, or credits | $0.00 |
| API Gateway HTTP API | at most a few thousand billed requests; throttled 429s are expected to be unbilled (V-060) | < $0.01 | credits | $0.00 |
| DynamoDB on-demand | at most a few thousand WRU/RRU, KB of storage, PITR on KB of data | < $0.01 | 25 GB storage allowance, or credits | $0.00 |
| SQS | at most tens of thousands of requests (including long polling) | ≈ $0.00 | 1M requests monthly allowance | $0.00 |
| SNS | ticket-issued publishes (no subscribers) + alarm notifications | ≈ $0.00 | 1M publishes monthly allowance | $0.00 |
| S3 (artefacts) | KB of tickets, at most a few thousand PUT/GET, Bucket Keys on | ≈ $0.00 | credits | $0.00 |
| S3 (state) | KB of state versions | ≈ $0.00 | credits | $0.00 |
| X-Ray | traces from smoke + spike | ≈ $0.00 | 100k traces recorded monthly allowance | $0.00 |
| Cognito | at most a handful of MAU (ephemeral smoke users) | $0.00 | MAU free tier (V-044) | $0.00 |
| Data transfer | JSON responses to GitHub runners and k6 | ≈ $0.00 | 100 GB egress allowance | $0.00 |
| **Total** | | **≈ $0.30–0.55 list** | | **$0.00 expected** |

Expected result: **net $0.00**, with list-price usage of well under $1 drawn from Free-plan
credits.

## Estimate vs actual

Filled in during P7 from Cost Explorer, grouped **by service** and filtered **by tag
`Project=tixlane`** (cost-allocation tag activation can lag by up to a day).

| service | estimate (48 h) | actual (Cost Explorer) | credits applied | net | note |
|---|---|---|---|---|---|
| KMS | ≈ $0.24–0.47 | | | | |
| CloudWatch | ≈ $0.06 | | | | |
| Lambda | ≈ $0.00 | | | | |
| API Gateway | < $0.01 | | | | |
| DynamoDB | < $0.01 | | | | |
| SQS / SNS | ≈ $0.00 | | | | |
| S3 | ≈ $0.00 | | | | |
| X-Ray | ≈ $0.00 | | | | |
| Cognito | $0.00 | | | | |
| **Total** | **≈ $0.30–0.55** | | | | |

Remaining credit balance before deploy: _fill in_ · after teardown: _fill in_

## Infracost delta

_Placeholder — filled by the CI `infracost` job in P4._

| run | monthly list estimate | delta vs base | link |
|---|---|---|---|
| P4 baseline (main) | | | |
| PR … | | | |

If the job skips because `INFRACOST_API_KEY` is not configured, record "skipped (no key)".
Do not record $0.

## Risk register

| ID | risk | impact | mitigation |
|---|---|---|---|
| RISK-01 | A new account's Lambda concurrent-executions quota may be as low as 10 (V-037). AWS keeps a minimum of unreserved concurrency, so reserving 5 × 3 = 15 can fail (`PutFunctionConcurrency` error) and block the deploy. | Deploy fails in P6 | Check Service Quotas on day 1. If the quota is low, set `reserved_concurrency = -1` (no reservation) and rely on the HTTP API stage throttle (10 rps, 20 burst) and the SQS event source `maximum_concurrency = 2`, which together still cap concurrency. Optionally request a quota increase. Record the decision in ADR-0006. |
| RISK-02 | KMS keys stay in `PendingDeletion` for at least 7 days. They may still bill (V-041) and may still appear in the tag sweep (V-042). | Small cost; tag sweep ≠ 0 | TEARDOWN.md lists pending keys separately, then repeats the sweep with `STRICT=1` after the window closes. |
| RISK-03 | A late invocation after destroy recreates `/aws/lambda/<fn>` without tags or retention. | Untagged log storage that never expires | scripts/tag_sweep.sh lists `/aws/lambda/tixlane-prod*` log groups directly; delete them by hand if any appear. |
| RISK-04 | Alarm metrics grow past the 10 free (e.g. by adding per-function alarms or a metric-math alarm). | About $0.10 per extra alarm metric per month | Keep the alarm count in observability/main.tf (currently 9 metrics); review it in every PR touching that module. |
| RISK-05 | Spike traffic (P7) | Throttles and alarms firing (expected); small request cost | k6 spike is hard-capped at ≤ 50 VUs and ≤ 5 min (the script refuses larger stages). 429s from the stage throttle are expected and treated as non-failures. |
| RISK-06 | Teardown forgotten after 48 h | Ongoing KMS and alarm charges | `TTL=48h` tag on every resource, a calendar reminder at deploy time + 44 h, the $1 budget with 50/80/100% + forecast alerts, and Free Tier alerts (configured by the architect). |
