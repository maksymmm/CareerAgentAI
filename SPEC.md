# CareerAgent AI Specification

## Current release: autonomous backend core

Status: implementation complete on `feat/decision-engine-implementation`; release
review and explicit approval to merge into `main` remain required. This specification
covers the current backend release. The broader product roadmap is in `PROJECT_PLAN.md`.

## Functional contract

The core accepts a candidate profile and career objective, ranks job opportunities,
selects explainable next actions, and prepares a typed application artifact. Its
bounded autonomous loop persists progress in SQLite and can resume after restart.
Application tracking, career memory, communication records, and interview events
use explicit repository boundaries and validate restored data.

Live job discovery and HTTPS RSS/Atom employer-signal adapters exist. Employer
signals retain provenance and observation times; repeated source identities cannot
inflate ranking. Source failures do not fabricate opportunity evidence.

Application submission, outbound communication, and calendar responses currently
use provider-neutral contracts with fake/no-I/O implementations. A production
provider for those actions is not included in this release.

## Human approval and recovery contract

- Applications require explicit approval of the exact persisted artifact and digest.
- Outbound messages and interview accept/decline/reschedule decisions require approval.
- Interview projections expose employer, location, local date/time, timezone and UTC offset.
- Stable operation IDs and durable intent/outcome records suppress duplicate actions.
- Ambiguous provider outcomes stop for reconciliation rather than blind retry.
- Candidate ownership and durable execution leases protect recovery boundaries.
- Runtime permission flags default to disabled and do not replace domain approval gates.
- Destructive migrations require explicit approval and a verified backup.

## Operational contract

Production composition must use validated `RuntimeConfig`, durable SQLite storage,
guarded provider execution, and structured logging with bounded secret redaction.
Both live job discovery and employer-signal collection enforce the disabled-by-default
network-provider flag at the provider call boundary.
The read-only WSGI API exposes public liveness at `/healthz` and bearer-authenticated
operational issues at `/v1/operational/issues`. Its schema is OpenAPI 3.1.
This operational API is not a candidate-facing workflow API.

Migration sequencing, deployment and rollback constraints are documented in
`docs/operations/`. Runbooks alone do not establish a deployed service or verified
production environment.

## Acceptance evidence

The full test suite must pass with at least 90% coverage. Existing tests exercise
end-to-end fake-provider flows, human gates, restart recovery, duplicate suppression,
ambiguous-outcome reconciliation, concurrent leases, tenant ownership, configuration,
migrations and operational authentication. Release evidence must identify the exact
commit and CI run; a review of an older commit is not final review of the current head.

## Outside this release

- Candidate-facing web dashboard, workflow API and approval interface.
- Production application/email/calendar providers and sandbox acceptance evidence.
- Packaged service/worker startup and verified hosting/deployment composition.
- Mobile application, public beta, and live-user acceptance validation.

These remain product work. Core coverage or eight completed execution-plan items
must not be reported as a measured completion percentage for the entire product.

## Candidate interview reconciliation recovery

The sandbox candidate interface exposes the authenticated GET endpoint
`/v1/candidate/runs/{run_id}/interview-reconciliation` to restore a pending interview
intent after browser reload. It is owner-scoped, returns the durable run state version,
allowlisted local event details and only outcomes supported by the current durable state.
It never returns the private provider operation ID or provider event ID. The candidate
must independently verify the calendar provider outcome before using the existing POST
reconciliation endpoint; the submitted state version is checked before durable resolution.
The GET path opens existing SQLite storage in read-only mode, performs no schema writes
or provider I/O, and never composes a writable provider workflow. This is sandbox recovery
functionality, not a production candidate dashboard or live calendar integration.
