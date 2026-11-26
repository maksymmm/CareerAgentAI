# ADR 0019: Owner-scoped discovery of pending interview actions

## Status

Accepted for the candidate sandbox interview-reconciliation workflow.

## Context

After a browser reload or runtime restart, a candidate may know that an interview
calendar action is awaiting reconciliation but not know which event and durable run
the pending action belongs to. The existing reconciliation POST is already fenced by
the candidate run's state version, but the dashboard has no dedicated owner-scoped
way to rediscover the pending interview intent.

The ordinary schedule projection deliberately hides provider identities and active
operation claims. Those fields must remain private; exposing them is not required to
identify the candidate's pending action.

## Decision

Add a bounded read-only `GET /v1/candidate/interview-intents` projection. It filters
`autonomous_career_loops` by authenticated owner and interview-reconciliation phase
before parsing the payload, then cross-checks the pending human action against the
same owner's schedule event, active action claim, and exact durable `calendar.accept`
operation payload/status.

The response contains only the run ID, run state version, action type/status, and a
candidate-safe event snapshot. It never returns the internal operation ID, provider
event ID, application ID, or owner ID.

The dashboard can rediscover these intents after a browser/runtime restart. Selecting
an intent forces a fresh owner-scoped run-status read. Reconciliation controls are
enabled only when the fresh phase and state version exactly match the discovered
intent. A stale discovery is cleared and must be loaded again.

## Consequences

- Foreign-owner payloads are excluded in SQL before JSON parsing.
- Loop, event claim, and external-action operation are read from one SQLite snapshot.
- Mismatched, stale, or corrupt intent/event/operation bindings fail closed.
- The projection is read-only and does not execute or replay a calendar provider action.
- Existing version-bound interview reconciliation remains the only write path.
- Tests use synthetic data and local SQLite/Chromium only; no real calendar or production
  credential is used.
