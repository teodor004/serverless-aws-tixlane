# ADR-0006: Asynchronous confirmation via SQS with a DLQ after 3 receives

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

After the capacity transaction (ADR-0003) the reservation is PENDING. Confirmation means
setting it to CONFIRMED, writing `tickets/{id}.json` to S3 and publishing to the SNS topic
`ticket-issued`. That work must not slow down or fail the HTTP request. A message that can
never succeed (a *poison message*) must reach a dead-letter queue **after 3 receives**, and a
CloudWatch alarm must fire.

How is confirmation decoupled, and how do we keep it correct under at-least-once delivery?

## Decision Drivers

- The API responds `202` quickly regardless of S3/SNS latency.
- SQS standard queues deliver at least once, so every worker step must be safe to repeat.
- One bad message must not block or re-drive a whole batch.
- Poison messages are quarantined after exactly 3 receives and alarmed on.
- The worker's concurrency is bounded (reserved concurrency 5, budget).
- No extra paid services.

## Considered Options

1. **SQS standard queue + DLQ, Lambda event source mapping with partial batch responses, and an
   idempotent worker**.
2. SQS FIFO queue (exactly-once processing within the deduplication window).
3. AWS Step Functions workflow per reservation.
4. EventBridge Pipes from SQS to the worker.
5. Synchronous confirmation inside the API request.

## Decision Outcome

Chosen option: **Option 1**. It is the simplest, cheapest design that meets every driver.
Idempotent steps make at-least-once delivery safe, and partial batch responses isolate poison
messages.

### Queue configuration (`infra/modules/messaging/main.tf`)

- `tixlane-<env>-tickets`: standard queue, 4-day retention, long polling (20 s), CMK encryption.
  `redrive_policy.maxReceiveCount = 3` → `tixlane-<env>-tickets-dlq`.
- DLQ: 14-day retention (it outlives the source), redrive allow policy `byQueue` restricted to
  the tickets queue, TLS-only queue policies on both.
- Visibility timeout ≥ 6 × worker timeout, the AWS guidance for Lambda consumers, enforced by
  a `check` block in each env root. AWS: 60 s vs 10 s. Emulator: 30 s vs 5 s, so the DLQ test
  finishes quickly.

### Consumer (`infra/modules/compute/main.tf`, `src/tixlane/handlers/worker_tickets.py`)

- Event source mapping: batch size 10, `function_response_types = ["ReportBatchItemFailures"]`,
  `scaling_config.maximum_concurrency = 2` (emulator: omitted, EMULATOR_GAPS G-07).
- Powertools `BatchProcessor` reports failed records individually. A batch where **every**
  record fails makes the invocation fail, which re-drives the batch and counts towards the
  Lambda Errors alarm.
- Invalid bodies raise `PoisonMessageError`, as do unknown reservations and mismatched
  event IDs. These are never acknowledged. After the 3rd receive, SQS moves the message to
  the DLQ.

### Idempotent worker sequence (`src/tixlane/services.py::TicketIssuer`)

1. Load the reservation. If it's missing or the event ID doesn't match → poison.
2. If `PENDING`: build the ticket (codes are `uuid5(namespace, "<reservation_id>:<seat>")`, so
   they are deterministic and a re-delivery rewrites byte-identical content) and **write the S3
   artefact first**. Then apply the conditional update `PENDING → CONFIRMED`. CONFIRMED
   therefore always implies the artefact exists. Losing the race to a concurrent delivery is
   fine: reload and continue.
3. If `notified_at` is unset: publish to `ticket-issued` (message attributes `type`,
   `event_id`), then conditionally set `notified_at`.
4. A crash between publish and `notified_at` re-publishes on retry, so publication is
   **at-least-once**. Subscribers must deduplicate on `reservation_id`. This is part of the
   topic contract.

### Alarm

`tixlane-<env>-dlq-depth`: `AWS/SQS ApproximateNumberOfMessagesVisible` (Maximum, 60 s) > 0 on
the DLQ → `ops-alerts` (encrypted; ADR-0005), with OK actions too. The DLQ drill
(`scripts/e2e.py dlq-drill`) peeks with `VisibilityTimeout=0` so it doesn't hide the message
from the metric.

### Consequences

- Good: the API path doesn't depend on S3/SNS availability; failures are retried by SQS.
- Good: per-record failure isolation; poison messages are quarantined and alarmed on.
- Good: every step is repeatable, so duplicates can't double-confirm or corrupt artefacts.
- Bad: confirmation is eventual. Clients poll `GET /reservations/{id}`; the target is CONFIRMED
  within 30 s (P3 acceptance).
- Bad: `ticket-issued` can be delivered twice. Subscribers must be idempotent.
- Bad: a poison message takes ~3 × visibility timeout to reach the DLQ (~3 min on AWS). The
  alarm adds a further evaluation period.
- Bad: DLQ redrive/purge is a manual operator action (runbook in TEARDOWN.md / ops.yml).

## Confirmation

- P1: `tests/unit/test_worker_tickets.py` covers the happy path, redelivery without a second
  publish, deterministic codes, an all-poison batch failing the invocation, per-record
  failures, the publish-failure retry and the lost confirmation race. *To be confirmed in P1.*
- P3: `tests/integration` asserts CONFIRMED + S3 artefact within 30 s, and poison → DLQ → alarm.
  The alarm leg depends on emulator alarm evaluation (G-05). *To be confirmed in P3.*
- P7: the DLQ alarm drill on AWS via `ops.yml`, with timestamps and a screenshot of the ALARM
  state in `docs/evidence/`. *To be confirmed in P7.*

## Pros and Cons of the Options

### Option 1: SQS standard + DLQ + idempotent worker
- Good: cheap, simple, native redrive, partial batch responses.
- Bad: at-least-once semantics push idempotency into the worker (done) and subscribers.

### Option 2: SQS FIFO
- Good: deduplication window, ordering per group.
- Bad: lower throughput; dedup only within 5 minutes, so idempotency is still needed. Ordering
  isn't required here.

### Option 3: Step Functions
- Good: visual state machine, built-in retries.
- Bad: per-transition cost and another service to secure, for a three-step linear flow.

### Option 4: EventBridge Pipes
- Good: filtering/enrichment without code.
- Bad: adds a hop and IAM surface; Lambda ESM already provides batching and partial failures.

### Option 5: Synchronous confirmation
- Good: no eventual consistency for clients.
- Bad: couples API latency and availability to S3 and SNS; there's no natural place for poison
  handling; it contradicts the specified PENDING → async flow.

## More Information

- Code: `infra/modules/messaging/main.tf`, `infra/modules/compute/main.tf`,
  `infra/modules/observability/main.tf`, `src/tixlane/handlers/worker_tickets.py`,
  `src/tixlane/services.py`, `scripts/e2e.py` (`dlq-drill`).
- Related: ADR-0003, ADR-0004 (replay re-enqueue), ADR-0005.
- VERIFICATION_LOG: Lambda ESM `ReportBatchItemFailures`, `scaling_config` minimum, the
  visibility-timeout guidance, Powertools whole-batch-failure behaviour, and the
  `ApproximateNumberOfMessagesVisible` metric are all logged as unverified.
