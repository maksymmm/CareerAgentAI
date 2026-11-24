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
The read-only WSGI API exposes public liveness at `/healthz`, bearer-authenticated
operational issues at `/v1/operational/issues`, and a bounded non-terminal action
lifecycle projection at `/v1/operational/actions`. The action projection omits raw
operation IDs, payloads, provider results and error text, and cannot execute or retry
work. Candidate-originated actions expose a stable `candidate_run_ref` that matches
the owner-scoped candidate status `run_ref`; the raw correlation identifier remains
in a dedicated nullable metadata column and is never copied into provider payloads.
Legacy action rows remain readable without a correlation reference, and replays cannot
rebind an existing operation. Its schema is OpenAPI 3.1. This operational API is not
a candidate-facing workflow API.

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


## Candidate-run scoped operator action discovery

GET /v1/operational/actions may be filtered by the 24-character lowercase hexadecimal
candidate_run_ref exposed by the owner-scoped candidate status projection. The filter
is applied in SQLite before keyset pagination and uses a separately indexed reference
column. Cursors are bound to the exact filter; cross-filter and filtered-to-unfiltered
reuse is rejected. Read-only requests never migrate legacy storage. If the indexed
reference column is absent, filtered discovery fails closed rather than claiming that
no matching actions exist.
