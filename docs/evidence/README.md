# Evidence pack

Proof that TixLane ran on real AWS, behaved as designed, and was torn down.

> **Nothing has been captured yet.** This repository was built offline. Every box below is
> empty until P6/P7.

## Redaction rules (apply before committing anything here)

- **No tokens, passwords or session credentials**, ever. The workflows mask them in logs, and
  scripts/e2e.py never writes them into evidence JSON. Raw dumps go to
  `docs/evidence/raw/`, which is git-ignored.
- **No real account IDs.** Replace them with `123456789012` (or `****`) in JSON, file names
  and screenshots (crop or blur). gitleaks rule `aws-account-id-in-arn` blocks ARNs with
  real IDs.
- No e-mail addresses other than the generated `e2e-*@example.com` smoke users.
- Keep the original file names below, so README links resolve.

## Layout

| file | produced by | phase | contents |
|---|---|---|---|
| `smoke.json` | deploy.yml → `scripts/e2e.py smoke` | P6 | sign-in → event → reservation (+ replay) → CONFIRMED latency → S3 artefact, plus a check list |
| `k6-smoke-summary.json` | deploy.yml → `k6 run --summary-export` | P6 | k6 smoke thresholds and timings |
| `deploy-timestamps.json` | deploy.yml | P6 | plan/apply start and end, commit SHA, run URL |
| `k6-spike-summary.json` | ops.yml (spike) | P7 | bounded spike (≤ 50 VUs, ≤ 5 min): status mix incl. expected 429s |
| `dlq-drill.json` | ops.yml (dlq-drill) | P7 | poison sent → seen in DLQ → alarm `ALARM`, with timings |
| `snapshot.json` | ops.yml (snapshot) | P7 | alarm states, Lambda metrics, reserved concurrency |
| `tag-sweep.json` | destroy.yml → `scripts/tag_sweep.sh --env prod` | P7 | remaining tagged resources (must be 0) + KMS keys pending deletion |
| `tag-sweep-final.json`, `tag-sweep-strict.json` | architect, read-only | P7 (+7 days) | full and strict sweeps after the bootstrap teardown |
| `screenshots/api-metrics.png` | console | P7 | API Gateway Count / 4xx / 5xx / Latency during the spike |
| `screenshots/lambda-duration.png` | console | P7 | Lambda Duration p95 per function |
| `screenshots/dlq-alarm.png` | console | P7 | DLQ-depth alarm in `ALARM` after the drill |
| `screenshots/xray-trace.png` | console | P7 | one trace: api_reservations → DynamoDB/SQS, and worker → S3/SNS |
| `screenshots/dashboard.png` | console | P7 | the `tixlane-prod` CloudWatch dashboard |
| `screenshots/cost-explorer-by-service.png` | console | P7 | Cost Explorer grouped by service |
| `screenshots/cost-explorer-by-tag.png` | console | P7 | Cost Explorer filtered by `Project=tixlane` |

## Checklist

P6 — deploy
- [ ] `smoke.json` (`passed: true`, confirmation ≤ 30 s)
- [ ] `k6-smoke-summary.json` (all thresholds met)
- [ ] `deploy-timestamps.json`

P7 — within 48 hours
- [ ] `k6-spike-summary.json`
- [ ] `dlq-drill.json` (`alarm_state: "ALARM"`)
- [ ] `snapshot.json`
- [ ] `screenshots/api-metrics.png`
- [ ] `screenshots/lambda-duration.png`
- [ ] `screenshots/dlq-alarm.png`
- [ ] `screenshots/xray-trace.png`
- [ ] `screenshots/dashboard.png`
- [ ] `screenshots/cost-explorer-by-service.png`
- [ ] `screenshots/cost-explorer-by-tag.png`

Teardown
- [ ] `tag-sweep.json` = 0
- [ ] `tag-sweep-final.json` = 0 (+ pending keys)
- [ ] `tag-sweep-strict.json` = 0
- [ ] Timestamps table in [../TEARDOWN.md](../TEARDOWN.md) complete
- [ ] "Estimate vs actual" in [../COST.md](../COST.md) complete
