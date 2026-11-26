# ADR 0012: Candidate run cancellation at ordinary approval gates

- Status: Proposed
- Date: 2026-11-25

## Context

The candidate workflow already has explicit approval gates for application
submission, recruiter messaging and interview acceptance. Declining an individual
gate terminates the run as a failed decision, but the candidate has no distinct
operation for stopping the remaining workflow while preserving the fact that the
candidate cancelled it.

Cancellation must not be confused with application withdrawal, message retraction,
calendar cancellation or compensation. Those are separate consequential provider
operations and cannot be inferred from stopping orchestration.

## Decision

Add a durable `CANCELLED` terminal phase and an authenticated,
owner-scoped `POST /v1/candidate/runs/{run_id}/cancel` operation.

The request contains only `schema_version` and the exact `state_version`
observed by the candidate. The transition uses the existing execution lease and
optimistic version check, so it races safely with approval and recovery.

Cancellation is permitted only at the ordinary application, message and interview
approval gates. It is rejected while a worker owns the run, during non-human phases,
and during any provider reconciliation phase. Ambiguous external outcomes must be
resolved with provider evidence before the candidate can stop the run.

A cancelled run remains terminal after process restart. Earlier successful
application submissions and message deliveries remain durable and unchanged.
Cancellation never calls a provider, deletes a provider ledger row, or performs
automatic compensation.

## Consequences

- The candidate can distinguish an intentional stop from workflow failure.
- Stale browser snapshots cannot cancel a newer phase.
- A cancellation cannot suppress unresolved provider evidence.
- Previously completed external effects remain auditable and are not silently undone.
- Application withdrawal, message retraction and calendar cancellation require
  separate explicit provider contracts and approvals.
