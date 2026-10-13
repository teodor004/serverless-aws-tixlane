# AWS Well-Architected mapping

This maps TixLane's design to the six pillars of the AWS Well-Architected Framework. Columns:

- **Design choice → file**: where the decision is implemented.
- **Evidence (phase)**: how and when it will be demonstrated. Nothing has executed yet, so every
  item is still to be confirmed in the phase noted.
- **Gap / trade-off**: deliberate limits, mostly imposed by the $0 budget.

## 1. Operational Excellence

| Design choice → file | Evidence (phase) | Gap / trade-off |
|---|---|---|
| Everything as code: OpenTofu modules + env roots (`infra/`, `bootstrap/`) | Emulator apply, then an empty second plan (P2) | Bootstrap is a manual, human apply by design |
| One command per workflow: `make up && make local-apply && make itest` (`Makefile`) | CI integration job (P4) | Requires Docker in WSL2 |
| Structured JSON logs, correlation ids, EMF metrics (`src/tixlane/handlers/*.py`) | Log samples in evidence (P6) | Metrics are kept few to stay in the free tier |
| Dashboard + 8 alarms → ops-alerts (`infra/modules/observability/main.tf`) | Dashboard and alarm screenshots (P7) | No paging integration; e-mail is optional |
| Runnable drills: DLQ drill, smoke, snapshot (`scripts/e2e.py`, `ops.yml`) | DLQ alarm test on AWS (P7) | Drills are manual dispatches |
| Phase gates + verification log (`docs/VERIFICATION_LOG.md`) | Reviewer approval per phase | Verification is pending (offline build) |
| Documented teardown (`docs/TEARDOWN.md`, `scripts/tag_sweep.sh`) | Tag sweep = 0 (P7) | KMS keys stay listed until the 7-day deletion window ends |

## 2. Security

| Design choice → file | Evidence (phase) | Gap / trade-off |
|---|---|---|
| Cognito JWT authorizer on every route; groups enforced in code; access tokens only (`infra/modules/api/main.tf`, `src/tixlane/auth.py`) | Unit tests for 401/403 (P1), integration sign-in (P3) | `USER_PASSWORD_AUTH` enabled for scripted sign-in ([THREAT_MODEL](THREAT_MODEL.md) S-3) |
| One role per function, explicit actions, ARN-scoped (`infra/modules/compute/main.tf`) | Checkov/Trivy (P4); IAM Access Analyzer review (P6) | X-Ray needs `Resource: "*"` (ADR-0008) |
| Deploy role via GitHub OIDC; `sub` pinned to `environment:production`; boundary on created roles (`infra/modules/iam_oidc/main.tf`) | First deploy (P6) | The policy is expected to need iteration on the first plan |
| CMK on table, queues, topics, bucket, logs and Lambda env (`infra/modules/data/main.tf`) | Resource configuration in the snapshot (P6) | CMK costs about $1/month, prorated ([ADR-0005](adr/0005-kms-customer-managed-key.md)) |
| TLS-only policies on S3, SQS, SNS; S3 BPA + BucketOwnerEnforced (`infra/modules/storage`, `messaging`) | Scanners (P4) | No S3 access logging (ADR-0008) |
| Secret hygiene: gitleaks (pre-commit + CI), `.gitignore`, Claude Code hook (`.gitleaks.toml`, `.claude/hooks/guard.py`) | CI secret scan (P4) | The hook is best-effort command parsing; CI is the backstop |
| Supply chain: SHA-pinned actions, verified tool versions (`scripts/check_action_pins.sh`, `versions.env`) | Both gates pass locally (2026-10-13); preflight job green in CI (P4) | Pins must be refreshed manually |
| Threat model per trust boundary ([THREAT_MODEL.md](THREAT_MODEL.md)) | Review (P5) | No WAF (not supported on HTTP APIs; disallowed by budget) |

## 3. Reliability

| Design choice → file | Evidence (phase) | Gap / trade-off |
|---|---|---|
| Oversell-proof conditional transaction (`src/tixlane/repository.py`, [ADR-0003](adr/0003-conditional-capacity-write.md)) | 50-for-10 unit test (P1); 20-for-5 over HTTP (P3) | The unit test serialises Moto transactions to model DynamoDB isolation |
| Idempotency claimed in the same transaction ([ADR-0004](adr/0004-idempotency-in-transaction.md)) | Replay and mismatch tests (P1, P3) | Key scope is per caller; 24 h TTL |
| Async confirmation, partial batch failures, DLQ after 3 receives ([ADR-0006](adr/0006-async-confirmation-sqs-dlq.md)) | DLQ integration test (P3), AWS drill (P7) | SNS publish is at-least-once |
| Enqueue-failure heal via replay (`src/tixlane/services.py`) | Unit test (P1) | Relies on the client retrying; no outbox or reconciler |
| Transient-conflict retry with jitter; botocore standard retries (`src/tixlane/container.py`) | Unit test (P1) | — |
| Visibility timeout ≥ 6 × worker timeout (`check` blocks in env roots) | Plan (P2) | — |
| PITR on prod table (`infra/envs/aws/main.tf`) | Snapshot (P6) | Off on the emulator (G-09) |
| Single region by budget rule | — | No multi-region DR; RTO/RPO not targeted |

## 4. Performance Efficiency

| Design choice → file | Evidence (phase) | Gap / trade-off |
|---|---|---|
| Serverless, on-demand capacity (DynamoDB PAY_PER_REQUEST, Lambda) | k6 smoke + bounded spike (P6/P7) | Reserved concurrency of 5 caps throughput by design |
| HTTP API (v2) rather than REST API: lower latency and cost | Latency widget (P7) | Fewer features (no X-Ray segments, no WAF) |
| Lazy, cached boto3 clients reused across warm invocations (`src/tixlane/container.py`) | Duration p95 (P7) | Cold starts not tuned (no provisioned concurrency, by rule) |
| Strongly consistent key reads only where correctness needs them (`src/tixlane/repository.py`) | — | Consistent reads cost 2 × an eventually consistent read |
| Batch size 10 and ESM max concurrency 2 (`infra/modules/compute/main.tf`) | Queue age during the spike (P7) | The worker backlog grows under sustained spikes |
| S3 bucket keys cut KMS calls (`infra/modules/storage/main.tf`) | — | — |

## 5. Cost Optimization

| Design choice → file | Evidence (phase) | Gap / trade-off |
|---|---|---|
| Only free-tier-friendly services; no NAT/VPC/EIP/RDS/WAF/provisioned concurrency | Infracost (P4), Cost Explorer by service + tag (P7) | — |
| 7-day log retention, 7-day S3 lifecycle, noncurrent versions 1 day | Scanners (P4) | Short retention limits forensics |
| Alarm count kept at 9 metrics; detailed route metrics off (`infra/modules/api/main.tf`) | [COST.md](COST.md) estimate vs actual (P7) | Less granular API metrics |
| Single shared Lambda package; 256 MB functions | — | Not power-tuned |
| `TTL=48h` tag, destroy workflow, tag sweep (`destroy.yml`, `scripts/tag_sweep.sh`) | TEARDOWN timestamps (P7) | State bucket and state CMK outlive the stack until the human deletes them |
| Budget alerts, Free Tier alerts (human day-1 setup) | Screenshots (P6) | Outside IaC by decision |
| RISK-01 fallback: `reserved_concurrency = -1` if the quota is low ([COST.md](COST.md)) | Plan/apply (P6) | Throttling then relies on the API stage + ESM cap |

## 6. Sustainability

| Design choice → file | Evidence (phase) | Gap / trade-off |
|---|---|---|
| Scale-to-zero compute; nothing idles between requests | — | — |
| Short-lived environment (48 h) with full teardown | Tag sweep (P7) | — |
| Expiring data: TTL on idempotency records, 7-day lifecycle on artefacts and logs | — | — |
| Shared package with runtime-provided SDK stripped (`scripts/build_lambda.py`) | Package size printed by the build (P2) | — |
| x86_64 kept for emulator parity (`var.architecture`) | — | arm64 (Graviton) would be more efficient; kept as a flag for later |
