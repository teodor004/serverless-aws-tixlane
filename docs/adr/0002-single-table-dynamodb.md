# ADR-0002: Single-table DynamoDB design with GSI1

- Status: Proposed
- Date: 2026-10-13
- Deciders: architect (reviewer), implementation engineer

## Context and Problem Statement

TixLane stores three kinds of data: events (with a remaining-capacity counter), reservations,
and idempotency records. The overselling guard (ADR-0003) and the idempotency guarantee
(ADR-0004) both require all three to be written **atomically in one transaction**. Organiser
reporting also needs reservations to be listable by event. RDS is ruled out by the budget, and
storage must be serverless with no provisioned capacity.

How should the items be laid out?

## Decision Drivers

- One `TransactWriteItems` must touch the event, the reservation and the idempotency record.
- Key lookups (`GET /events/{id}`, `GET /reservations/{id}`) must be strongly consistent, so a
  client can read its own write right after `202 Accepted`.
- Listing reservations for an event, in creation order, without a scan.
- On-demand billing only. Pay per request and stay inside the free tier at portfolio traffic.
- Idempotency records must expire on their own, with no cleanup job.
- One encryption key, one set of alarms, one IAM resource ARN per function.

## Considered Options

1. **Single table with generic `PK`/`SK` and one overloaded GSI (`GSI1PK`/`GSI1SK`)**.
2. One table per entity (events, reservations, idempotency).
3. Aurora Serverless / RDS PostgreSQL with row locks.

## Decision Outcome

Chosen option: **Option 1**. DynamoDB transactions work across tables too, but a single table
keeps the IAM policies, the KMS grant, the alarms and the Moto fixture to one resource. The
access patterns are few and known in advance, which is where single-table design works best.
Option 3 is excluded by the budget rules (no RDS, no VPC Lambdas, no NAT).

### Item layout

| Entity      | PK                            | SK     | GSI1PK              | GSI1SK                              | Notes                                   |
|-------------|-------------------------------|--------|---------------------|-------------------------------------|-----------------------------------------|
| Event       | `EVENT#<event_id>`            | `META` | none                | none                                | `capacity`, `remaining`, `organiser_id` |
| Reservation | `RES#<reservation_id>`        | `META` | `EVENT#<event_id>`  | `RES#<created_at>#<reservation_id>` | `status` PENDING/CONFIRMED, `ticket_key`, `notified_at` |
| Idempotency | `IDEMP#<sub>#<key>`           | `META` | none                | none                                | `reservation_id`, `request_hash`, TTL attribute `expires_at` |

Access patterns:

| Pattern                                | Operation                                            |
|----------------------------------------|------------------------------------------------------|
| Create event                           | `PutItem` with `attribute_not_exists(PK)`            |
| Get event / reservation                | `GetItem` with `ConsistentRead=True`                 |
| Reserve seats                          | `TransactWriteItems` (ADR-0003, ADR-0004)            |
| Confirm / mark notified                | conditional `UpdateItem` on `status` / `notified_at` |
| List reservations for an event         | `Query` on `GSI1` with `begins_with(GSI1SK, "RES#")` (eventually consistent) |
| Expire idempotency records             | DynamoDB TTL on `expires_at` (24 h)                  |

Table settings (`infra/modules/data/main.tf`): `PAY_PER_REQUEST`, TTL on `expires_at`,
server-side encryption with the environment CMK (ADR-0005), PITR on in AWS and off on the
emulator (EMULATOR_GAPS G-09), deletion protection off so the 48-hour teardown can proceed.

### Consequences

- Good: atomic multi-entity writes, one IAM ARN per function policy, one alarm and dashboard
  scope, and one table definition mirrored by `tests/unit/factories.py`.
- Good: strongly consistent reads by key make read-your-own-write behaviour predictable.
- Good: the creation-ordered GSI1 sort key supports organiser listing without a scan.
- Bad: key prefixes are conventions in code (`src/tixlane/keys.py`). A typo is a data bug, so
  every key is built through that one module.
- Bad: GSI reads are eventually consistent, so a reservation can be missing from the listing for
  a moment after creation. That's acceptable for reporting.
- Bad: all events share one partition key namespace. A single very hot event concentrates writes
  on one item (`EVENT#<id>`). This is inherent to a counter-based capacity guard; see ADR-0003
  for the contention handling.
- Neutral: the GSI uses `projection_type = "ALL"`, which doubles storage for reservations. That
  is negligible at this scale and avoids follow-up reads.

## Confirmation

- P1: the repository tests (`tests/unit/test_repository.py`) cover each access pattern on
  Moto, including GSI1 listing scoped to one event. *To be confirmed in P1 when the suite
  first runs.*
- P2: the table, GSI and TTL create on Floci, and a second plan is empty. *To be confirmed
  in P2.*
- P6/P7: CloudWatch `ConsumedRead/WriteCapacityUnits` and `ConditionalCheckFailedRequests`
  on the dashboard show the expected pattern during smoke and spike. *To be confirmed in P7.*

## Pros and Cons of the Options

### Option 1: Single table + GSI1
- Good: one transaction scope, one key, one policy; known access patterns fit naturally.
- Bad: less self-describing than named tables; key conventions need discipline.

### Option 2: Table per entity
- Good: obvious schemas; per-table metrics.
- Bad: three tables to encrypt, alarm, grant and tear down; the transaction spans tables, which
  works but triples the IAM resources and test fixtures for no functional gain.

### Option 3: Aurora Serverless / RDS
- Good: relational constraints, `SELECT ... FOR UPDATE`.
- Bad: forbidden by the budget rules (RDS, VPC Lambdas, NAT); connection management from Lambda.

## More Information

- Code: `src/tixlane/keys.py`, `src/tixlane/repository.py`, `infra/modules/data/main.tf`,
  `tests/unit/factories.py` (`TABLE_DEFINITION`).
- Related: ADR-0003 (capacity guard), ADR-0004 (idempotency records), ADR-0005 (encryption).
- VERIFICATION_LOG: DynamoDB on-demand and TTL behaviour, the GSI argument shape in
  `hashicorp/aws ~> 6.66`, and emulator support for transactions/GSI/TTL are all logged
  there. None have been checked yet (offline build).
