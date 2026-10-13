# CLAUDE.md — working agreement for Claude Code in this repository

The architect (repository owner) reviews. Claude Code is the implementation engineer.

## Phase protocol
- Work in phases P0–P7 (README → "Phases"). At each **STOP — REVIEW**, report: files changed,
  test/scan results (with output), Infracost delta, open risks. Then wait for "approved".
- Report outcomes faithfully. If something was not run, say so. Never mark a
  `docs/VERIFICATION_LOG.md` row verified without having checked the source.

## Hard rules
1. **Clean-room.** Write everything from scratch. Reuse nothing (code, IaC, pipelines, naming,
   templates) from any former employer or internship. Cite public sources in `NOTICE.md`.
2. **Verify before use.** Check every CLI flag, provider argument, action version and emulator
   setting against current official docs. Log each check in `docs/VERIFICATION_LOG.md`.
   Pins live in `versions.env`, `.tflint.hcl` and the workflow SHAs (verified 2026-10-13);
   `make verify` fails on any `PIN_ME` placeholder or tag-only `uses:`.
3. **No real-AWS mutations from a workstation.** Never run `tofu apply/destroy/import` (or state
   surgery) outside `infra/envs/local`. Never run mutating `aws` commands unless
   `--endpoint-url` targets the local emulator. Never pass `--allow-real-aws` to
   `scripts/e2e.py`. Real AWS changes happen only through `deploy.yml`, `destroy.yml` and
   `ops.yml`, after the architect approves the `production` environment.
4. **No secrets in git.** No credentials, tokens, real account IDs or real `.tfvars`. Use
   `*.example` files and the documentation placeholder account `123456789012`.
5. **Budget $0.** eu-central-1 only. No NAT gateway, VPC Lambdas, public IPv4/EIPs, RDS,
   provisioned concurrency or WAF. DynamoDB on-demand, logs 7 days, S3 lifecycle 7 days.

These rules are enforced in `.claude/settings.json` (deny/ask rules) and by the PreToolUse hook
`.claude/hooks/guard.py`. Run `python3 .claude/hooks/guard.py --self-test` after editing it.
The hook is a second line of defence; the rules above still apply where it cannot see
(for example, inside Makefile recipes or Python scripts).

## Everyday commands
| Task | Command |
|---|---|
| Install deps | `make install` (first time: `uv lock`, needs network) |
| Lint + types | `make lint` |
| Unit tests (≥90% coverage gate) | `make test` |
| Start emulator | `make up` (Floci) / `make up EMULATOR=ministack` |
| Apply to emulator + empty-second-plan check | `make local-apply` |
| Integration tests | `make itest` |
| k6 smoke / DLQ drill (emulator) | `make k6-smoke`, `make dlq-drill` |
| Static scanners | `make scan` |
| Version and action pins | `make verify` |

## Layout
- `src/tixlane/` domain + handlers (`handlers/api_events.py`, `api_reservations.py`,
  `worker_tickets.py`); one deterministic zip, built by `scripts/build_lambda.py`.
- `tests/unit` (Moto), `tests/integration` (emulator only), `k6/`.
- `infra/modules/{api,auth,compute,data,messaging,storage,observability,iam_oidc}`,
  `infra/envs/local` (emulator), `infra/envs/aws` (S3 backend + state encryption),
  `bootstrap/` (applied once by the architect).
- `docs/`: architecture, ADRs, verification log, emulator gaps, threat model, cost, teardown,
  evidence.

## Conventions
- Python 3.13 (3.12 emulator fallback), ruff, mypy strict on `src/`, Powertools for logging,
  metrics, tracing, event handling and SQS batch processing.
- Resource names: `tixlane-<env>-<thing>`. Every resource carries the provider default tags
  `Project, Env, Owner, CostCenter, TTL, ManagedBy`.
- Any scanner suppression: inline, with a justification and an ADR-0008 reference.
- New emulator workaround: add a `G-xx` row to `docs/EMULATOR_GAPS.md` and gate it behind a
  variable or environment flag that defaults to the real-AWS behaviour.
