# Architecture Decision Records

TixLane records significant decisions as [MADR](https://adr.github.io/madr/) documents.
Every record starts as **Proposed** and becomes **Accepted** only after the architect approves
it at a phase review. Superseded records stay in place with a link to their replacement.

Nothing in these records has been executed yet: the repository was assembled offline, so every
"expected" outcome is marked *to be confirmed in Pn* and every external fact points at an entry
in [`../VERIFICATION_LOG.md`](../VERIFICATION_LOG.md).

| #    | Title                                                                                   | Status   | Date       |
|------|-----------------------------------------------------------------------------------------|----------|------------|
| 0001 | [Emulator strategy: Floci primary, MiniStack cross-check, Moto for units](0001-emulator-strategy.md) | Proposed | 2026-10-13 |
| 0002 | [Single-table DynamoDB design with GSI1](0002-single-table-dynamodb.md)                 | Proposed | 2026-10-13 |
| 0003 | [Overselling prevention with one conditional TransactWriteItems](0003-conditional-capacity-write.md) | Proposed | 2026-10-13 |
| 0004 | [Idempotency record written inside the reservation transaction](0004-idempotency-in-transaction.md) | Proposed | 2026-10-13 |
| 0005 | [One customer-managed KMS key per environment](0005-kms-customer-managed-key.md)        | Proposed | 2026-10-13 |
| 0006 | [Asynchronous confirmation via SQS with a DLQ after 3 receives](0006-async-confirmation-sqs-dlq.md) | Proposed | 2026-10-13 |
| 0007 | [CI/CD: GitHub OIDC and approval-gated plan → apply](0007-cicd-oidc-approval-gated-deploys.md) | Proposed | 2026-10-13 |
| 0008 | [Scanner suppression policy and register](0008-scanner-suppressions.md)                 | Proposed | 2026-10-13 |

## Writing a new record

1. Copy the section layout of any existing record (MADR: context, drivers, options, outcome,
   consequences, confirmation, pros/cons, more information).
2. Number it sequentially, set `Status: Proposed`, and add a row above.
3. Reference code paths and VERIFICATION_LOG IDs for every external claim.
4. Scanner suppressions must name ADR-0008 and be added to its register.
