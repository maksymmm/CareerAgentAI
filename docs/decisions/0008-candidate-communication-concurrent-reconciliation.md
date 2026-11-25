# ADR 0008: Single-winner candidate communication reconciliation

Status: accepted. Scope: authenticated candidate sandbox reconciliation over
request-local SQLite connections.

## Context

The candidate runtime can restore an ambiguous communication delivery after a
SQLite restart. The existing HTTP regression proves owner-scoped intent recovery,
stale-version rejection, and evidence-backed completion, but it does not prove that
two browser submissions racing with the same state version have exactly one
candidate-workflow winner.

A single durable delivery is not sufficient evidence of a single-winner workflow
transition: both requests must contend on the candidate state and execution lease
through independent request-local database connections.

## Decision

- Drive the real candidate application and message approval sequence through
  RuntimeCandidateApp and authenticated HTTP.
- Configure the SQLite sandbox communication provider to persist one
  TIMEOUT_AFTER_EFFECT delivery.
- Close the first runtime and reopen the same SQLite database.
- Restore only the public, owner-scoped message reconciliation intent; keep the
  private operation ID, message ID, and message body out of the HTTP projection.
- Reject a stale state version before the race.
- Submit two authenticated reconciliation POST requests with the same current
  state version through independent request-local SQLite connections.
- Require exactly one 200 OK completion and one sanitized 409 Conflict.
- Verify the candidate run is COMPLETE, the external action is SUCCEEDED, the
  provider attempt and outbound delivery rows are unchanged, and replay accounting
  remains zero.
- Assert that the restart-time provider outcome plan is never invoked.

## Consequences

The candidate communication workflow now has the same restart/concurrency
single-winner acceptance boundary as application and calendar reconciliation.
The provider ledger remains the source of truth for the one durable delivery, while
the candidate state-version and execution-lease boundary prevents duplicate
workflow completion.

All effects are synthetic and local to SQLite. No production credentials, real
recipient, network provider, outbound message, application submission, or calendar
mutation is used.
