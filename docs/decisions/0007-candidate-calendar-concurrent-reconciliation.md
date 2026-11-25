# ADR 0007: Single-winner candidate calendar reconciliation

Status: accepted. Scope: authenticated candidate sandbox reconciliation over
request-local SQLite connections.

## Context

The candidate runtime already composes a durable SQLite calendar provider, but its
calendar outcome plan was not configurable through RuntimeCandidateApp. The
provider's ambiguous outcome and restart behavior could therefore be tested at the
service layer, but not through the complete candidate HTTP composition.

After a runtime restart, duplicate browser submissions may also race with the same
version-bound provider evidence. Provider idempotency alone does not prove that the
candidate workflow transition is single-winner.

## Decision

- Add an optional deterministic calendar_outcome_plan to the candidate runtime,
  request-local loop builder, and build_candidate_sandbox_app_from_env.
- Pass the plan to every request-local SQLiteSandboxCalendarProvider; preserve the
  default successful sandbox outcome when it is omitted.
- Keep application submission and calendar outcome plans independent.
- Seed a synthetic candidate-owned interview event and run the real candidate
  application, message, and interview approval sequence.
- Cause the calendar provider to record one TIMEOUT_AFTER_EFFECT, close the first
  runtime, and reopen the same SQLite database.
- Reject mismatched provider-event evidence without changing the candidate state,
  event, external-action operation, attempt history, effect, or replay count.
- Race two authenticated interview-reconciliation POST requests with the same
  state_version and verified provider event ID through independent request-local
  SQLite connections.
- Require exactly one 200 OK reconciliation winner and one sanitized 409 Conflict.
- If the winning request returns the recoverable INTERVIEW_ACCEPT phase because
  post-effect application tracking could not finish in the concurrent segment, use
  the authenticated continue route after the race and require terminal COMPLETE.
- Verify the accepted event, succeeded external action, single provider effect,
  single attempt, and unchanged replay count.

## Consequences

The candidate runtime can now exercise ambiguous calendar provider outcomes without
monkeypatching constructors. The HTTP regression proves the version-bound
single-winner boundary across restart and concurrent requests while retaining the
provider ledger as the authoritative source of calendar evidence.

All effects are synthetic and local to SQLite. No network calendar, production
credential, real invitation, application submission, or outbound message is used.
