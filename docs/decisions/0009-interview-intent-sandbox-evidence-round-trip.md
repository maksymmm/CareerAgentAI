# ADR 0009: Interview intent to sandbox evidence recovery round trip

Status: accepted. Scope: candidate interview reconciliation after a provider
effect is committed but its response is lost.

## Context

The owner-scoped interview intent projection can be restored after a runtime
restart, and the SQLite sandbox calendar provider can persist an effect before
raising `TIMEOUT_AFTER_EFFECT`. Those boundaries were covered separately.
A candidate workflow still needs an acceptance proof that connects the restored
intent to authoritative provider evidence and reaches a terminal run without
replaying the calendar action.

## Decision

- Create a pending candidate interview reconciliation using the existing
  durable workflow fixture.
- Use `SQLiteSandboxCalendarProvider` with a deterministic
  `TIMEOUT_AFTER_EFFECT` outcome so the attempt and effect commit before the
  simulated timeout.
- Reopen the candidate runtime over the same SQLite database.
- Restore only the owner-scoped, allowlisted interview intent over authenticated
  HTTP; keep operation IDs and provider evidence out of that response.
- Reject mismatched provider evidence without changing the run, schedule event,
  external-action payload/status, attempt history, effect, or replay count.
- Submit the matching provider event ID with the restored state version.
- Require the candidate run to reach `COMPLETE`, the schedule event to become
  `ACCEPTED`, and the external action to become `succeeded`.
- Require exactly one timeout-after-effect attempt, one durable calendar effect,
  and zero provider replays.

## Consequences

The acceptance test joins intent restoration, authoritative SQLite provider
evidence, version-bound HTTP reconciliation, and terminal candidate completion.
It proves that the provider effect is not repeated after restart and that an
incorrect observation cannot mutate the pending workflow.

All provider state is synthetic and local to SQLite. No network calendar,
production credential, real invitation, application submission, or outbound
message is used.
