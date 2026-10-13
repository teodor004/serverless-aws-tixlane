# ADR-0003: Overselling prevention with one conditional TransactWriteItems

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

`POST /events/{id}/reservations` must never sell more seats than an event's capacity, even when
many attendees reserve at the same moment and Lambda runs several concurrent execution
environments. The reservation is first written as PENDING and confirmed asynchronously
(ADR-0006). The guard therefore belongs in the synchronous write, not in the worker.

The P1 acceptance test states the requirement precisely: **50 concurrent reservations for 10
seats yield exactly 10 confirmable reservations.**

## Decision Drivers

- Correctness under concurrency without locks, leases or a single-writer bottleneck.
- Atomicity with the reservation item and the idempotency record (ADR-0004). A held seat must
  never exist without a reservation, and the reverse must not happen either.
- Clear, distinguishable client outcomes: 404 unknown event, 409 sold out, 422 key reused,
  503 retryable contention.
- Identical behaviour on DynamoDB, Moto and the emulators.
- No extra infrastructure, and on-demand cost only.

## Considered Options

1. **One `TransactWriteItems` with a conditional decrement on the event item, plus two
   conditional puts**.
2. Optimistic concurrency: read the event, then `UpdateItem` with a version-number condition.
3. One item per seat, claimed with conditional puts.
4. Unconditional atomic counter (`ADD remaining -1`) with compensation when it goes negative.
5. Serialise all reservations for an event through an SQS FIFO queue (message group = event).

## Decision Outcome

Chosen option: **Option 1**, because the condition `remaining >= :q` is evaluated by DynamoDB
at commit time. Two transactions can never both consume the last seat, and the same
transaction atomically creates the reservation and claims the idempotency key.

The transaction (`src/tixlane/repository.py::Repository.create_reservation`):

| # | Action | Item                     | Expression                                                                 |
|---|--------|--------------------------|----------------------------------------------------------------------------|
| 1 | Update | `EVENT#<id>` / `META`    | `SET remaining = remaining - :q`, condition `attribute_exists(PK) AND remaining >= :q` |
| 2 | Put    | `RES#<id>` / `META`      | reservation in `PENDING`, condition `attribute_not_exists(PK)`             |
| 3 | Put    | `IDEMP#<sub>#<key>` / `META` | idempotency record, condition `attribute_not_exists(PK)`               |

When DynamoDB cancels the transaction, the repository **diagnoses the cause with consistent
reads** rather than relying only on `CancellationReasons`, whose detail varies between
DynamoDB, Moto and emulators:

1. Idempotency record exists → same fingerprint: return the stored reservation as a replay;
   different fingerprint: `422 idempotency_key_reused` (ADR-0004).
2. Event missing → `404 not_found`.
3. `remaining < quantity` → `409 sold_out`.
4. Otherwise the cancellation was transient (`TransactionConflict` with a concurrent
   transaction) → retry with jittered exponential backoff (≤ 5 attempts, delay capped at
   0.4 s), then `503 service_busy` with `Retry-After: 1`. The client retries with the same
   Idempotency-Key, so a retry can never double-book.

Cancellation reasons are still logged for diagnosis.

### Consequences

- Good: the guarantee comes from DynamoDB's serializable transactions, not application locks.
- Good: a failed attempt writes nothing: no held seat, no orphan reservation, no consumed key
  (`test_sold_out_leaves_no_partial_writes`).
- Good: outcome mapping is explicit and covered by unit tests for each branch.
- Bad: a transaction costs twice the write capacity of a plain write, and the diagnostic reads
  add consistent reads on the failure path. At portfolio scale that's fractions of a cent.
- Bad: every reservation for an event updates one item, so a very hot event is bounded by
  DynamoDB's per-item write throughput and sees `TransactionConflict` retries. That's
  acceptable at the ≤ 50 VU spike limit and visible on the dashboard
  (`ConditionalCheckFailedRequests`). Sharded counters are the documented escape hatch.
- Bad: diagnosis reads happen after the cancellation, so they observe a slightly later state.
  The order (idempotency → existence → capacity → transient) keeps the answer correct: the
  only race is a seat freed in between, which yields a harmless retry.

### Testing note: the Moto lock

Moto's in-memory backend makes no thread-safety promise, whereas DynamoDB documents that
`TransactWriteItems` is serializable. `tests/unit/test_concurrency.py` therefore wraps Moto's
`DynamoDBBackend.transact_write_items` in a lock. The lock **models DynamoDB's guarantee**: the
test then exercises TixLane's condition expressions and outcome mapping, not Moto's internals.
To keep this honest, the same scenario runs **without** any lock over HTTP against a real
emulator server (`tests/integration/test_reservation_flow.py::test_oversell_is_impossible_over_http`).

## Confirmation

- P1: `test_fifty_concurrent_reservations_for_ten_seats` asserts exactly 10 winners, 40
  `SoldOutError`, `remaining == 0`, exactly 10 queue messages, and all 10 confirmed by the
  worker with an S3 artefact each. *To be confirmed in P1 (suite not yet executed).* The
  conflict-retry and busy paths are covered in `tests/unit/test_repository.py`.
- P3: the unlocked HTTP oversell test passes on Floci. *To be confirmed in P3.*
- P7: during the bounded k6 spike, the number of `202` responses never exceeds capacity. The
  event's final `remaining` and the count of CONFIRMED reservations are captured as evidence.
  *To be confirmed in P7.*

## Pros and Cons of the Options

### Option 1: Conditional transaction
- Good: atomic with the reservation and idempotency writes; no new infrastructure.
- Bad: hot-item contention on one event; doubled write cost.

### Option 2: Version-number optimistic concurrency
- Good: plain `UpdateItem`, cheaper per call.
- Bad: needs a read before every write; can't include the reservation and idempotency puts
  atomically without a transaction anyway; more retries under contention.

### Option 3: Item per seat
- Good: no single hot item; natural seat assignment.
- Bad: capacity × items to create; reserving N seats needs a multi-item claim and a search for
  free seats; overkill without assigned seating.

### Option 4: Unconditional counter with compensation
- Good: cheapest write.
- Bad: transiently oversells and relies on compensating writes that can themselves fail, which
  violates the requirement.

### Option 5: SQS FIFO serialisation
- Good: no contention on the item.
- Bad: makes the API asynchronous for the capacity decision (no immediate 409), adds a queue per
  concern, and still needs idempotency.

## More Information

- Code: `src/tixlane/repository.py`, `src/tixlane/services.py::ReservationService.reserve`,
  `src/tixlane/handlers/api_reservations.py`, `src/tixlane/errors.py`.
- Tests: `tests/unit/test_concurrency.py`, `tests/unit/test_repository.py`,
  `tests/integration/test_reservation_flow.py`.
- Related: ADR-0002 (item layout), ADR-0004 (idempotency), ADR-0006 (confirmation).
- VERIFICATION_LOG: DynamoDB transaction isolation and `TransactionConflict` semantics, IAM
  actions required for transactions (`PutItem`/`UpdateItem`), and Moto's
  `DynamoDBBackend.transact_write_items` attribute path are listed there as unverified.
