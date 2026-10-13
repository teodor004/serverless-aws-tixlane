# ADR-0004: Idempotency record written inside the reservation transaction

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

Clients retry: mobile networks drop responses, k6 and browsers time out, and TixLane itself
answers `503` with `Retry-After` under contention (ADR-0003) or when the SQS send fails. A
retried `POST /events/{id}/reservations` must not book a second set of seats. The API therefore
requires an `Idempotency-Key` header.

There is also a dual-write gap. The reservation is committed to DynamoDB first and then sent to
SQS. If the send fails, the reservation is stuck in PENDING unless something re-sends it.

How do we make reservation creation idempotent and close the commit/send gap?

## Decision Drivers

- The key claim must be **atomic with** the capacity decrement and the reservation write. A
  separate record could be lost or stale while the seat is held.
- Keys must be scoped per caller, so two attendees choosing the same key never collide or see
  each other's data.
- Reusing a key for a different request must be detected, not silently replayed.
- Replays must be cheap and return the same resource.
- No extra tables, infrastructure or cost.

## Considered Options

1. **Idempotency record as the third item of the reservation transaction**, with a request
   fingerprint and TTL.
2. Powertools for AWS Lambda idempotency utility (`@idempotent`, DynamoDB persistence layer).
3. DynamoDB `ClientRequestToken` on `TransactWriteItems`.
4. Transactional outbox via DynamoDB Streams to drive SQS.

## Decision Outcome

Chosen option: **Option 1**. The idempotency claim, the seat decrement and the reservation
commit or fail together (ADR-0003). It costs one extra item in a transaction we need anyway.

Rules (`src/tixlane/http.py`, `src/tixlane/services.py`, `src/tixlane/repository.py`):

- Header `Idempotency-Key` is required: 8-128 characters of `[A-Za-z0-9_-]`. A missing or
  malformed header returns `400 bad_request`.
- Record key `IDEMP#<cognito sub>#<key>`: scoped to the authenticated caller.
- Fingerprint `sha256(canonical JSON {event_id, quantity})` stored as `request_hash`.
- TTL: `expires_at = now + 24 h` (`IDEMPOTENCY_TTL_SECONDS`, default 86400).
  DynamoDB TTL deletes it later. Deletion isn't instant, and correctness doesn't depend on it.
- Same key + same fingerprint → `202` with the **current** state of the original reservation and
  `Idempotent-Replayed: true`.
- Same key + different fingerprint → `422 idempotency_key_reused`.

### Closing the DB-commit / SQS-send gap

- The first attempt sends the SQS message after the commit. If the send fails, the API returns
  `503 enqueue_failed` with `Retry-After: 1`. The seat is held and the reservation is PENDING.
- **Any replay of a PENDING reservation re-sends the message.** The worker is idempotent
  (ADR-0006), so duplicate messages are harmless. A client that follows `Retry-After` therefore
  heals the gap itself (`test_enqueue_failure_is_retryable_and_heals_on_replay`).
- Residual risk: a client that never retries leaves the reservation PENDING. A reconciler that
  re-enqueues PENDING reservations older than N minutes via GSI1 is recorded as future work
  (THREAT_MODEL / WELL_ARCHITECTED reliability pillar). An outbox (Option 4) would remove the
  dependency on client retries.

### Consequences

- Good: exactly-once booking per (caller, key), with no extra infrastructure.
- Good: replays are a few consistent reads; no recomputation.
- Good: per-caller scoping prevents cross-user key collisions and replay-based probing.
- Bad: the key lives 24 h. After expiry, the same key would create a new reservation. That
  matches common idempotency-key semantics, and clients generate a fresh key per intent.
- Bad: replays return the *current* reservation state, not a byte-for-byte copy of the first
  response. This is deliberate: a client that retries late sees CONFIRMED.
- Bad: closing the enqueue gap depends on client retries until a reconciler or outbox exists.

## Confirmation

- P1: unit tests cover replay, key reuse with a different body, per-attendee scoping, a missing
  or invalid header, the enqueue-failure retry and the "no re-enqueue after CONFIRMED" path
  (`tests/unit/test_api_reservations.py`, `tests/unit/test_repository.py`). *To be confirmed
  in P1.*
- P3: the integration flow replays the same key over HTTP and asserts the same
  `reservation_id` plus the replay header. *To be confirmed in P3.*
- P6: `scripts/e2e.py smoke` records `replay_same_reservation` and `replay_header` in
  `docs/evidence/`. *To be confirmed in P6.*

## Pros and Cons of the Options

### Option 1: In-transaction record
- Good: atomic with the seat hold; per-caller scope; detects payload mismatch.
- Bad: bespoke code (small, unit-tested); the enqueue gap relies on retries.

### Option 2: Powertools idempotency utility
- Good: well-documented, handles in-progress locking and response caching.
- Bad: its record is written in a **separate** request from the capacity transaction, so a crash
  between the two leaves either a held seat without a cached response or a cached response
  without a seat. It also caches the first response rather than returning current state.

### Option 3: `ClientRequestToken`
- Good: built into DynamoDB, zero code.
- Bad: only a 10-minute window; tokens are not scoped per user; it can't distinguish "same key,
  different payload" in a client-friendly way; it doesn't help replays return the original id.

### Option 4: Outbox via DynamoDB Streams
- Good: removes the dual-write gap entirely, independent of client retries.
- Bad: Streams + another Lambda + another IAM role and alarms. Deferred: the replay path covers
  the portfolio scope, and the outbox is the documented next step.

## More Information

- Code: `src/tixlane/http.py::idempotency_key`, `src/tixlane/services.py::request_fingerprint`,
  `ReservationService.reserve`, `src/tixlane/repository.py::_resolve_cancellation`,
  `src/tixlane/models.py::IDEMPOTENCY_KEY_PATTERN`.
- Related: ADR-0002, ADR-0003, ADR-0006.
- VERIFICATION_LOG: DynamoDB TTL deletion latency, the `ClientRequestToken` window, and the
  Powertools idempotency behaviour cited above are logged there for confirmation. None have
  been checked yet (offline build).
