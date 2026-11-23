# ADR 0004: Sandbox provider hardening and end-to-end workflow recovery

Status: accepted (sandbox composition). Builds on ADR 0001 (application), 0002 (message) and
0003 (calendar), which already provide durable SQLite sandbox providers and
provider-evidence-verified reconciliation. This ADR covers only what those left open.

## What already existed (not re-implemented)

* Durable SQLite sandbox providers for submissions, messages and calendar actions with a
  per-operation intent fingerprint, replay counter, scripted outcomes and `verify_*` evidence.
* Evidence states `PRESENT` / `ABSENT_CONFIRMED` / `UNKNOWN`; unknown, malformed or failed
  lookups fail closed; rejections surface as the existing `409 approval_conflict`.

## Gaps found by inspection and fixed

| Gap | Effect before | Fix |
|-----|---------------|-----|
| Message provider: a second operation key for an already delivered `message_id` raised a raw `sqlite3.IntegrityError` | classified as *ambiguous*, forcing a manual reconciliation for a definitive no-effect | mapped to `PreDeliveryCommunicationError` (rolled back, nothing committed) |
| Message provider: re-binding a `message_id` to different content raised a raw `ValueError` | same misclassification | mapped to `PreDeliveryCommunicationError` |
| Calendar provider: protection was per operation key only | a new key could create a second response decision for one event, or take over another candidate's `event_id` | new additive table `sandbox_calendar_events` binds `event_id` to one candidate and application and tracks its status; a second `accept`/`decline` without an intervening reschedule is rejected as a definitive no-effect |
| Composition built message and calendar providers with no outcome plan | ambiguous message/interview outcomes could not be driven through the production composition | `SandboxProviderPlans(submission, communication, calendar)`; the existing `sandbox_outcome_plan` argument still accepts a bare submission plan |
| Evidence tests existed only per service | no test of crash, lock contention, concurrency or the authenticated HTTP path for messages and interviews | see Tests |

The calendar rule respects the domain lifecycle: `ACCEPTED -> RESCHEDULE_REQUESTED -> ACCEPTED`
stays legal, so a plain "one accept per event ever" rule would have been wrong.

## Concurrency semantics (documented, not changed)

A second caller that finds an operation `in_progress` cannot tell a crash from an action still
in flight, so it never repeats the provider call: it moves the operation to
`reconciliation_required`. Measured over 300 concurrent identical calendar accepts: 215 both
succeeded, 63 one caller got an `OperationConflictError` and the other succeeded, 22 ended in
`reconciliation_required`. The provider effect count was exactly 1 in all 300; every
`reconciliation_required` outcome resolves to `succeeded` by evidence. This is a liveness cost
accepted in exchange for never repeating an effect.

## Tests

`test_sandbox_comm_calendar_hardening.py` (message and calendar: success and replay, timeout and
restart, crash before and after a committed effect, conflicting keys and content, tampered and
forged evidence, unknown/failed/malformed lookups, lock contention, SQLite insert/commit faults
including a commit error after a durable effect, concurrent identical and concurrent different
keys) and `test_candidate_workflow_evidence_http.py` (the composed workflow over the
authenticated HTTP boundary with restart, forged claims, wrong owner, missing token, stale
`state_version`, and an OpenAPI shape check). Two earlier concurrency assertions that wrongly
required one caller to succeed were corrected to the invariant above.

## Limits

* The HTTP start contract accepts only keyword, profile and location, so messages and interviews
  are not reachable from an HTTP-started run. The end-to-end tests start the run through the
  composition function and drive approvals, status and reconciliation over HTTP. The contract
  was deliberately not widened: letting a client choose a recipient is an abuse surface.
* A `failed` operation id is terminal for messages; a legitimate retry uses a new key.
* Sandbox only. A real adapter must return `UNKNOWN` unless its lookup is authoritative
  (ADR 0001). No real provider, credential or network path is added.
* Calendar status tracking is per `event_id`; conflicting time-slot detection remains the
  scheduling service's job.
