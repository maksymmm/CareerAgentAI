# ADR 0039: Validate calendar evidence against all durable provider ledgers

- Status: Accepted
- Date: 2026-11-27

## Context

The SQLite calendar sandbox persists an operation intent, ordered provider attempts and
a durable effect in separate tables. Reconciliation previously trusted the effect row
alone. An orphaned effect, a mismatched fingerprint, or a missing effect after a
recorded `SUCCESS`/`TIMEOUT_AFTER_EFFECT` attempt could therefore be interpreted as
positive evidence or confirmed absence without checking the other durable records.

The provider's evidence contract must fail closed when those ledgers disagree. It must
also distinguish a valid no-effect history from a missing or corrupted effect record.

## Decision

`verify_calendar_action` reads the intent, ordered attempts and effect in one
`BEGIN IMMEDIATE` SQLite snapshot and validates their relationship before returning
evidence.

- A never-attempted operation is absent only when all three ledgers are empty.
- Attempts require a matching durable intent, contiguous attempt numbers and known
  outcome values.
- An absent effect is confirmed only when no attempt records `SUCCESS` or
  `TIMEOUT_AFTER_EFFECT`.
- A present effect requires exactly one effect-producing attempt, and that attempt must
  be the final attempt for the operation.
- The effect fingerprint must match the intent fingerprint, and the action must be one
  of `accept`, `decline` or `reschedule`.
- The stored event must decode as a valid scheduling event whose lifecycle status
  agrees with the recorded action and whose provider event ID is present.

Any inconsistent, orphaned or malformed record raises an evidence error. It is never
converted into confirmed absence or successful completion. The check is read-only and
does not invoke the outcome plan or provider action.

## Consequences

Calendar reconciliation now requires internal consistency across the intent, attempt
and effect ledgers as well as the existing approved-event checks in
`SchedulingService`. Existing valid ledgers remain compatible; no schema migration or
network provider is introduced. The SQLite sandbox remains dry-run only.
