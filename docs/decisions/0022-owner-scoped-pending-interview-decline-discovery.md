# ADR 0022: Owner-scoped pending interview-decline discovery

- Status: Proposed
- Date: 2026-11-26
- Scope: Candidate sandbox interview-decline workflow

## Context

The per-event decline recovery endpoint is safe and version-bound, but a candidate
returning after a browser or runtime restart must already know which event to inspect.
The dashboard's ordinary event list does not identify which events have ambiguous
provider outcomes. Requiring candidates to open every event is inefficient and makes
durable recovery difficult to discover.

The decline ledger can also contain a legitimate detached ambiguous operation after
the provider effect and local event transition committed but before the external-action
success status became durable. In that case the event's active-action pointer is
already cleared; discovery must not confuse this state with an absent action.

## Decision

Add authenticated, read-only GET /v1/candidate/schedule-event/decline-intents with
bounded keyset pagination.

- Scope all returned records to the authenticated candidate.
- Validate deterministic operation identity and the complete event source snapshot
  before returning an intent.
- Accept a pointer-free ambiguous operation only when the exact owned event is
  DECLINED at source version + 1 and the durable payload matches the source slot.
- Cross-check non-null active-action pointers against the ambiguous decline ledger.
- Bound the ambiguous-ledger scan and reject malformed JSON, duplicate members,
  non-finite numbers, duplicate event intents, and mismatched pointers.
- Return only a candidate-safe event snapshot, source version, and coarse operation
  status. Never return candidate, application, provider-event, or operation IDs.
- Use stable event-ID keyset pagination with a maximum page size of 100.
- Set no-store response headers through the shared WSGI response helper.
- Keep discovery read-only: it does not transition operations, call the provider,
  or grant permission to reconcile.
- On selection, the dashboard reloads the owned event and the existing per-event
  pending endpoint. Only that fresh version-bound snapshot may enable the explicit
  independent-provider-evidence attestation.

## Consequences

The candidate can find ambiguous declines after a browser/runtime restart without
knowing internal identifiers. A list item is only a discovery hint, never an approval
or reconciliation token. The existing per-event endpoint remains authoritative for
state recovery, and the provider evidence boundary is unchanged.

The same projection pattern may be applied to reschedule intents separately, but
must preserve the reschedule target snapshot and its existing version-bound approval
contract rather than generalizing decline payloads.
