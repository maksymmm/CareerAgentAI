# Autonomous Implementation Plan

Arbeitnow explicit-status contract: sandbox transports must provide an exact integer
HTTP status; a missing attribute no longer defaults to a successful 200 response.
Regression coverage proves rejection before body reads and gives every byte-stream
fixture an explicit status.
Next: verify exact-head CI and Codex review; continue provider failure coverage.

Arbeitnow strict JSON contract: decoded pages reject duplicate object members,
non-standard NaN/infinity constants and excessive nesting instead of accepting
ambiguous or Python-specific payloads. All failures surface as the same deterministic
invalid-JSON provider error and close the response before any page is accepted.
Next: verify full CI and Codex review; continue provider failure coverage.

Arbeitnow transport contract: each sandbox response must expose an exact integer HTTP
status in the protocol range and return bytes from its bounded read. Boolean, string,
float, missing/out-of-range statuses and non-byte bodies now fail with deterministic
provider errors before decode or partial result acceptance. Responses close on every
rejection. Next: verify full CI and Codex review; continue provider failure coverage.

Arbeitnow configuration contract: timeout must be a finite positive numeric value,
while page and response limits must be positive integers. Booleans, NaN/infinity,
strings and missing values now fail deterministically during construction instead of
reaching transport or pagination with ambiguous runtime errors. Sandbox tests cover
every rejected category and integer-timeout normalization before transport.
Next: verify full CI and Codex review; continue provider sandbox failure coverage.

Arbeitnow page-envelope failure atomicity: every decoded page requires list-valued
data and a valid optional links/next contract. A malformed first or later page aborts
the whole search instead of returning a plausible but incomplete prefix. Sandbox
tests cover invalid data, links and next types plus explicit retry from page one.
Next: verify full CI and Codex review; continue provider sandbox failure coverage.

Arbeitnow redirect trust: the active discovery adapter validates every redirect
destination before urllib follows it, then validates the final URL again before
reading the response. Scheme downgrades, foreign or deceptive hosts, credentials,
non-default ports, alternate paths and fragments therefore cannot receive a follow-up
request. Exact authority/path spelling also rejects empty or zero-padded ports and
repeated trailing slashes. Sandbox tests cover trusted HTTPS variants and fail-closed
preflight/final-response cases without making external requests.

Arbeitnow response bounding: the active job-discovery adapter now limits every
HTTP page before decoding (2,000,000 bytes by default, positive integer override).
Overflow closes the response and aborts pagination without returning partial results.
Sandbox transport tests cover the exact UTF-8 byte boundary, oversized bodies before
JSON parsing, a later-page failure and explicit retry from page one.
Next: verify full CI and Codex review; continue provider sandbox failure coverage.

Contextual private-request errors: application detail, application history, event
detail and approval-prompt failures now expose distinct operation-specific generic
messages. Backend and malformed-JSON payloads remain suppressed. Chromium regressions
verify accurate recovery guidance without leaking private response text.
Next: exact-head CI/review, then continue bounded dashboard usability.

Candidate detail request deduplication: application/event detail triggers and the
application-history control are disabled for their current read-only request. Repeated
activation cannot start a second request; success and failure restore retry controls,
while selection, authentication and session invalidation retire stale work safely.
Delayed-response Chromium coverage proves the single-request contract for all three.
Next: exact-head CI/review, then continue bounded dashboard usability.

Candidate collection request deduplication: run, application and event reload controls
are disabled during their current authenticated page request, preventing repeated
activation from starting redundant concurrent reads. Completion, failure and context
invalidation restore the controls; delayed-response Chromium coverage verifies all
three collections together with their `aria-busy` state.
Next: exact-head CI/review, then continue bounded dashboard usability.

Candidate collection loading state: owned run, application and event lists expose
`aria-busy` only while their current authenticated page request is active. Success,
failure, token/session invalidation and stale responses restore the idle state without
rendering superseded data. Chromium covers all three delayed collection requests.
Next: exact-head CI/review, then continue bounded dashboard usability.

Candidate keyboard bypass: the first focusable control is an initially off-screen
skip link that becomes visible on focus and moves focus directly to the dashboard
content. Chromium verifies keyboard activation, visible focus, the exact fragment,
and no HTTP request before normal navigation and workflow acceptance continue.
Next: exact-head CI/review, then continue bounded dashboard usability.

Candidate contextual action labels: repeated application and event view buttons keep
their concise visible labels while accessible names include a page ordinal plus the
validated, already visible record ID. The ordinal preserves uniqueness after assistive-
technology whitespace folding. Chromium verifies whitespace-colliding IDs, distinct
first/final-page names, and exact binding without adding private fields to the DOM.
Next: exact-head CI/review, then continue dashboard accessibility and usable summaries.

Candidate collection summaries: run, application and event status regions report
the exact current-page count plus whether another page is available. Singular,
plural, empty, first-page and final-page states are covered by Chromium without
exposing raw response data or implying a collection-wide total.
Next: exact-head CI/review, then continue dashboard accessibility and usable summaries.

Candidate narrow-screen layout: bounded fieldsets, wrapping snapshot/ID text and
single-column actions below 32rem. Chromium covers 320/390 px and desktop with 2000
unbroken snapshot characters and a 200-character run button; no page/pane horizontal
overflow or content truncation is accepted. API/action behavior is unchanged.
Next: exact-head CI/review, then continue dashboard accessibility and usable summaries.

Candidate dashboard navigation: six native fragment links in a named navigation
landmark, focusable destinations, visible keyboard focus and a return-to-top link.
Chromium checks every destination and keyboard activation, no HTTP requests, unchanged
token and disabled decisions. Links encode only fixed section IDs, never private data.
Next: exact-head CI/review, then continue dashboard usability and accessibility.

Run-list pagination hardening: reject duplicate/reversed IDs, items at or before
the requested cursor, and non-terminal cursors not matching a full page's last ID.
Chromium covers five malformed first pages plus replayed page one during page-two
loading; invalid pages expose no rows and preserve explicit next-page retry.
Next: exact-head CI/review, then improve bounded dashboard navigation.

Collection privacy and empty-state hardening: run/application page requests suppress
raw result rendering before validation. Chromium regressions inject top-level and
nested private extras, malformed JSON, and empty pages; assert no private text enters
the DOM, rows clear, pagination disables, and explicit reload recovers real data.
Next: obtain exact-head CI and Codex review; continue bounded dashboard usability.

Candidate session clearing delivered: explicit local clear removes credentials,
profile/start/run inputs and displayed run/application/event snapshots, invalidates
pending reads/starts and approval prompts, and performs no HTTP request. Chromium
clears while event detail is delayed, verifies empty credentials/inputs/panes and
disabled decisions, then confirms the late response cannot restore private state.
This does not revoke server credentials or cancel previously accepted work.
Next: verify fresh review/integration once Codex review capacity is available;
continue candidate dashboard usability and bounded empty-state acceptance.

Schedule UI review P2 timezone: Intl IANA-zone formatting must agree with local
date/time at both exact UTC instants after tuple/offset validation. Unknown zones,
fixed-offset labels and browser/server timezone-data mismatches fail closed. Negative
epoch flooring preserves historical seconds. Chromium rejects contradictory/unknown
zone labels and retains DST-fold, leap-day and Monrovia second-offset acceptance.
Browser timezone database disagreement is an explicit documented display limitation.

Schedule UI review P2 consistency: local date/time/offset tuples must reconstruct
their exact UTC instants; a present end must follow start. Whole-second UTC arithmetic
plus BigInt microseconds avoids Date.parse rounding, retains year 1–99 and historical
offset seconds. Chromium rejects mismatched local tuples and equal/earlier microsecond
ends while accepting a one-microsecond interval and historical offsets.

Schedule UI review P2: Gregorian dates (including leap-century rules), clock fields,
and UTC offsets now validate semantic ranges. Instants require exact timezone-bearing
date/time with optional six-digit microseconds, not permissive Date.parse normalization.
Chromium rejects impossible local/UTC/end values, date-only/naive/millisecond inputs,
and accepts leap-day and historical second-resolution offset snapshots.

Schedule UI delivered: owned event pages and explicit details render allowlisted
text-only fields/UTC/local times and both offsets; next-page retry, malformed response
rejection and token/page/selection invalidation prevent stale/private rendering.
Chromium restart acceptance covers persisted DST-fold details, pagination retry,
private extras/errors/parse failures, identity/version/status/offset rejection and
delayed detail invalidation after token edits. No calendar actions enabled.
Next: verify reviewed integration, then candidate dashboard usability/empty-state refinement.

Schedule HTTP collection delivered: GET /v1/candidate/schedule-events authenticates
before strict bounded limit/after parsing and request-local read-only owner queries.
Malformed escapes, duplicate/unknown/invalid inputs fail before storage; errors are
sanitized and no-store/OpenAPI match the minimal exact-time projection. Restart,
max-ID pagination/private isolation, missing storage and corrupt-owned tests added.
Next: read-only candidate event discovery/detail UI without calendar actions.

Schedule discovery groundwork delivered: RuntimeCandidateApp.list_schedule_events
returns bounded ID-keyset pages using owner-filtered read-only SQL and the existing
exact-time detail projection. Writable scheduling startup backfills owner/ID index;
reads never migrate/create storage or load private fields/claims. Restart, pagination,
isolation, input limits, corrupt owned data, missing storage and query-plan regressions.
Next: authenticated HTTP collection, then read-only candidate event discovery/detail UI.

HTTP review P2: historical IANA offsets now serialize as ISO-compatible ±HH:MM[:SS]
and OpenAPI accepts second precision. Restart HTTP regressions cover positive Amsterdam
and negative Monrovia offsets, optional ends, schema patterns and exact UTC reconstruction.

Scheduling HTTP delivered: authenticated GET /v1/candidate/schedule-event?event_id=...
reads only the bearer's owned event through request-local read-only storage. Strict
single-parameter/domain identity validation precedes storage; missing/foreign 404,
sanitized failures, no-store and OpenAPI are covered. Restart/DST/microsecond/private
isolation, malformed query, missing-store and corrupt-owned-event regressions added.
Next: bounded owned event discovery and read-only candidate UI, without calendar actions.

Integration P2: HumanScheduleView preserves nonzero microseconds in local start/end
time strings, matching exact persisted UTC timestamps. Whole-second strings remain
unchanged. Restart/DST-fold regressions now cover both precision levels and optional ends.

Scheduling visibility groundwork: RuntimeCandidateApp.get_schedule_event reads
one owned event using SQL ownership filtering and existing read-only storage.
Reuses domain scalar validation and HumanScheduleView for exact UTC/local dates,
times and both offsets across DST. Provider/application IDs and action claims are
not loaded. Restart, foreign-corruption isolation, missing-storage, malformed-owned
fields/indexes, input and closed-runtime tests cover it. Next: authenticated HTTP
event contract, then candidate UI discovery/detail without calendar actions.

Prompt-binding CI follow-up: legacy browser fake now emits schema_version,
action_kind and allowed_decisions so the existing successful approval scenario
exercises the full contract alongside malformed-prompt rejection.

Integration review P2: loaded approval prompts must match the selected run and the
complete versioned wire schema before storing/rendering or enabling decisions.
Malformed/misrouted prompts never enter the raw result pane. Chromium exercises
identity, schema, version, kind, title, details, fingerprint, decision and extra-field
failures, then verifies a fresh valid prompt can still approve the selected run.

Integration review P2: history UI now checks each status pair against the domain
lifecycle map, not merely known enum values/adjacent continuity. Chromium rejects
skipped stages, self transitions and transitions from terminal states before render.

Candidate history UI delivered: explicit Load application history after detail
selection renders only bounded allowlisted transition fields/version. Empty state,
retry, malformed payload rejection and detail/page/token invalidation are supported.
Chromium restart acceptance covers persisted history, private-field/parse-error
redaction and delayed-response invalidation. Next: verify reviewed integration,
then candidate-facing interview/scheduling visibility without calendar actions.

HTTP history delivered: GET /v1/candidate/application/history?application_id=...
reuses detail validation/authentication and reads owner-only bounded lifecycle
transitions through request-local read-only storage. No private children or action
controls exposed. OpenAPI/no-store, restart/Unicode/foreign isolation, invalid-query,
auth/method/backend-failure regressions cover the boundary. Next: read-only history UI.

Candidate history groundwork: read-only owner-only runtime history exposes bounded
sequence/from/to/time transitions and state version, not notes/metadata/operation IDs.
One transaction pins owner/snapshot/events; domain validation checks continuity,
chronology, sequence, serialization and version before returning anything. Restart,
private-corruption isolation, malformed-owned-history and no-create tests cover it.
HTTP endpoint delivered above; next: read-only candidate history UI.

Detail UI review follow-up: all suppressed-raw request failures, including JSON
parse/network errors, are replaced with a generic error before reaching the shared
error renderer. Chromium injects malformed JSON with a private sentinel and requires
only the generic message, no detail and no sentinel anywhere in the DOM.

Detail UI review P2: disable shared raw-result rendering for detail requests before
validation/projection. Only allowlisted fields enter the detail pane; detail HTTP
failures use a generic error instead of backend message text. Chromium regression
injects extra top-level/nested private fields and requires their absence from the DOM.

Candidate detail UI delivered: explicit View application reads the authenticated
detail endpoint and renders allowlisted minimal fields/timestamps as text only.
Selection/page/token generations prevent stale detail resurrection. Failure clears
detail and permits explicit reselection. Chromium restart acceptance covers success,
retry, malformed identity and token changes. No approval or execution controls added.
Next: scoped read-only candidate application history projection.

HTTP detail review P2: reject truncated/non-hex raw percent escapes before parsing
or opening storage. Regression cases cover malformed names/values and mixed valid
escapes; correctly percent-encoded literal percent IDs still round-trip after restart.

HTTP application detail delivered: GET /v1/candidate/application?application_id=...
authenticates before strict query validation/storage and returns the minimal owned
snapshot. Duplicate/unknown/empty/padded/oversized/invalid-Unicode queries fail closed;
foreign/missing rows share 404. Restart tests cover max emoji and opaque reserved
characters, corrupt foreign/private records and byte-identical storage. OpenAPI and
no-store match the contract. Next: candidate detail UI and scoped history projection.

Integration review P2: application projection identifiers reject unpaired Unicode
surrogates before opening storage or binding SQL. Regression coverage checks both
surrogate ranges for application and authenticated owner identities against a missing
database; valid maximum-length emoji IDs remain supported.

Candidate detail groundwork: programmatic `get_application` returns one versioned,
minimal owner-only snapshot through existing read-only storage. Foreign and missing
IDs share an identical failure, even with corrupt foreign rows. Restart, private
child omission, validation-before-storage and closed-runtime regressions cover it.
Next: authenticated HTTP detail and explicitly scoped candidate history views.

This document is the prioritized execution queue for CareerAgentAI. Codex should take the highest-priority unfinished item that is not blocked and deliver it as one coherent, verified unit.

### Release scope clarification

The eight completed units cover the autonomous backend core. `SPEC.md` now records
its implemented contracts and release exclusions; `PROJECT_PLAN.md` retains the
broader web/mobile/provider/deployment roadmap. Completed core work does not imply
that the public product is complete.

Parent integration commit `44bcc50907ce2827e1612fecbe16f1ac81dca2cb` passed
GitHub CI #899 with 635 tests and 91.72% coverage, and its exact-head Codex review
found no major issues. Any documentation commit after that evidence must pass its own
exact-head CI and Codex review before integration. The resulting integration HEAD must
then receive fresh release evidence before the final explicit approval to merge into
`main`. Subsequent product delivery needs separate acceptance criteria for the user
interface, real providers and deployment.

## 1. Crash-safe idempotency for consequential external actions

Status: **COMPLETED**

Goal: guarantee that restart/retry behavior cannot silently duplicate a real-world action such as an application submission or recruiter message.

Acceptance criteria:
- [x] Introduce an explicit external-action operation model with stable operation/idempotency ID.
- [x] Add repository abstraction for external-action operations.
- [x] Add SQLite persistence with parameterized SQL and appropriate indexes.
- [x] Model at least: prepared, in_progress, succeeded, failed, reconciliation_required.
- [x] Persist intent before adapter execution.
- [x] Persist terminal outcome after execution.
- [x] On restart, do not blindly repeat ambiguous in-flight operations.
- [x] Add tests for duplicate invocation, crash/restart recovery, failure, and malformed persisted state.
- [x] Maintain >=90% total coverage.
- [x] Document the contract.

Delivered: a provider-neutral adapter boundary, mandatory explicit approval at execution,
atomic SQLite state transitions, terminal duplicate suppression, and safe recovery of
ambiguous in-flight work. No real provider adapter or credential is configured.

Verification: GitHub CI verified all 217 tests passing with 90.67% total coverage.

## 2. Durable long-term career memory

Status: **COMPLETED**

Goal: replace purely in-memory career memory with a durable provider-neutral boundary.

Acceptance criteria:
- [x] Define durable memory repository interface.
- [x] SQLite implementation.
- [x] Versioned/validated JSON-safe serialization.
- [x] Retrieval by user and memory type.
- [x] Restart tests.
- [x] No unsafe deserialization.
- [x] Existing `MemoryEngine` contract preserved or migrated explicitly.

Delivered: a provider-neutral memory repository boundary, compatible in-memory
default, and restart-safe SQLite implementation. Persisted values and metadata are
strict JSON, carry an explicit serialization version, and are validated when written
and loaded. Records can be queried deterministically by user and memory type.

## 3. Application tracker

Status: **COMPLETED**

Goal: persist and track each job application across its lifecycle.

Acceptance criteria:
- [x] Application aggregate and lifecycle states.
- [x] Repository boundary and SQLite implementation.
- [x] Link application to job/company and external-action operation IDs.
- [x] Timeline/history events.
- [x] Duplicate prevention.
- [x] Query/filter support.
- [x] Tests and documentation.

Delivered: an immutable, validated application aggregate with explicit lifecycle
transitions, chronological history, optimistic concurrency, unique candidate/job
applications, and stable links to company and crash-safe external-action operation
IDs. The provider-neutral repository has in-memory and restart-safe SQLite adapters;
SQLite storage uses versioned strict JSON for event metadata and deterministic
filtering and ordering. No external action is executed by the tracker.

Next priority: communication adapter.

## 4. Communication adapter

Status: **COMPLETED**

Goal: introduce a provider-neutral communication layer for recruiter/employer messages.

Acceptance criteria:
- [x] Adapter protocol for draft/send/read/reply primitives.
- [x] Safe dry-run/fake provider.
- [x] Human approval gate before real send.
- [x] Idempotency integration.
- [x] Thread/conversation identifiers persisted.
- [x] Input sanitization and failure handling.
- [x] No production credentials in repository/tests.

Delivered: a provider-neutral communication boundary and deterministic no-I/O fake,
strict validated plain-text message models, and restart-safe SQLite storage for
message, thread, and reply-to identifiers. Send/reply actions always pass through
the existing approval-gated external-action service. Duplicate requests reuse their
durable result, failures remain safely terminal, and ambiguous in-flight actions are
held for reconciliation without another provider call. No real provider, credential,
or network send is configured. Review hardening also prevents a new operation ID from
resending an outbound message, validates provider delivery results against their
prepared intent, applies strict deterministic message typing, and orders persisted
threads by timezone-aware instants. A durable atomic draft-to-operation claim prevents
stale and concurrent competing sends immediately before provider execution. Failures
after possible provider success now require reconciliation rather than being recorded
as safe failures. Successful reply retries reuse their durable result across restarts;
only an explicit pre-delivery provider failure releases the delivery claim for a new
deliberate operation, while ambiguous outcomes remain locked.
Concurrent identical message inserts converge on the durable winning row; conflicting
reuse of a message ID remains rejected.
Thread ordering preserves exact microsecond instants through a legacy-compatible UTC
epoch backfill, and malformed lone Unicode surrogates are rejected at the domain edge.

Next priority: scheduling and interview coordination.

## 5. Scheduling and interview coordination

Status: **COMPLETED**

Goal: model interview/trial-day scheduling and present exact employer, address, date, and time when human participation is required.

Acceptance criteria:
- [x] Scheduling domain model.
- [x] Calendar adapter boundary.
- [x] Human gate for accept/decline/reschedule.
- [x] Time-zone-aware storage.
- [x] Conflict handling.
- [x] Tests for restart and duplicate events.

Delivered: a provider-neutral scheduling domain for interviews, trial days, phone/video
calls, and other human-participation events; a deterministic no-I/O calendar adapter;
and restart-safe SQLite persistence. Events retain employer, location, application and
provider identifiers, exact UTC instants, and their presentation timezone. Human-facing
projections expose employer, event type, address/link, local date/time, timezone, UTC
offset, and status. Accept, decline, and reschedule actions require explicit human
approval and use the crash-safe external-action service. Durable per-event action
claims suppress stale/concurrent duplicate responses; explicit pre-provider failures
can be retried deliberately, while uncertain or post-provider outcomes require
reconciliation. Conflict detection is deterministic and persisted state is strictly
validated after restart.

Next priority: real signal-source adapters and employer intelligence.

## 6. Real signal-source adapters and employer intelligence

Status: **COMPLETED**

Goal: replace static-only proactive opportunity input with real provider adapters while keeping scoring deterministic and explainable.

Acceptance criteria:
- [x] At least one production-capable signal provider boundary implementation.
- [x] Provenance and observation timestamps.
- [x] Rate/error handling.
- [x] Deduplication.
- [x] Employer intelligence aggregation.
- [x] No fabricated signals.

Delivered: a production-capable HTTPS RSS/Atom opportunity-signal adapter for configured
company-owned news feeds. Signals are emitted only from actual parsed feed entries that
match explicit deterministic rules. Each signal retains an entry/feed provenance URL,
provider-native external identity when available, publication/fetch observation time,
and matched evidence keywords. Collection is request-rate-limited, response-size
bounded, fail-soft across sources, and exposes sanitized per-source errors. Stable
provider identities are deduplicated before employer aggregation and before opportunity
scoring so repeated observations cannot inflate a score. Employer intelligence groups
the retained evidence by company with latest observation time, signal types, and
provenance sources. No synthetic signal is created when source evidence does not match.

Next priority: end-to-end autonomous career loop.

## 7. End-to-end autonomous career loop

Status: **COMPLETED**

Goal: connect search -> decision -> resume/application preparation -> approval -> communication -> tracking -> interview coordination.

Acceptance criteria:
- [x] Bounded long-running loop.
- [x] Durable recovery.
- [x] Idempotent external actions.
- [x] Clear human-action events.
- [x] End-to-end integration tests with fake providers.
- [x] No duplicate application/message after restart.

Delivered: a bounded durable state machine connects ranked search, deterministic
career decision progression, resume preparation, application preparation, explicit
human approval, crash-safe application submission, optional outbound communication,
application tracking, and interview coordination. Active loop state uses versioned
strict JSON in SQLite and can recover both human-gated states and already-approved
nonterminal phases after restart. Application submission has its own provider-neutral
adapter and no-I/O fake, executes through the existing external-action coordinator,
and uses the stable application identity for idempotency rather than a transient run
ID. Existing communication and scheduling services retain their own crash-safe
idempotency and reconciliation contracts. Human-action events expose the precise
decision being requested, including the exact typed application artifact and its
SHA-256 digest before submission plus exact interview employer/location/local time.
The approved application artifact is persisted in restart-safe loop state and copied
into the crash-safe external-action intent so provider execution is cryptographically
bound to the content the human saw. Interview coordination requires candidate
ownership and compatible application linkage. End-to-end fake-provider tests exercise
process restarts and verify that tracked applications and outbound messages are not
duplicated. A later run is prevented from resubmitting a user/job application that has
already progressed beyond draft.
Durable execution leases are heartbeat-renewed during long-running steps so a second
worker cannot reclaim an active run mid-provider-call. Snapshot persistence failures
after already-completed external actions remain resumable, and ambiguous application
submission outcomes enter an explicit reconciliation gate. Provider-verified success
is finalized without another submit call; provider-verified no-effect outcomes reopen
the same stable idempotency operation for a deliberate retry.
The same explicit reconciliation pattern covers ambiguous recruiter-message delivery
and interview/calendar responses, preventing uncertain external effects from becoming
terminal loop failures.

Next priority: production hardening.

## 8. Production hardening

Status: **COMPLETED**

Acceptance criteria:
- [x] Structured logging and correlation IDs.
- [x] Configuration validation.
- [x] Database migration strategy.
- [x] Observability for failed/stuck runs.
- [x] API surface and OpenAPI where applicable.
- [x] Security review of external inputs and secrets.
- [x] Release/deployment documentation.
- [x] Full CI green at >=90% coverage.

Delivered: bounded structured JSON logging with correlation scopes and credential-key
redaction; strict environment-driven runtime configuration with safe external-effect
defaults; a read-only SQLite operational probe for failed, ambiguous, and stale work;
and an authenticated WSGI operational endpoint with a matching OpenAPI 3.1 contract.
A versioned SQLite migration runner records immutable SHA-256 checksums, applies each
migration transactionally, and refuses destructive migrations without explicit
approval. Deployment, migration, rollback, and security-review documentation defines
the production operating contract. The complete GitHub CI suite remains above the
project's 90% coverage floor.

## Completion definition

### Exact-head release review follow-up

Review of `100acf2e8d` found an ownerless approval/reconciliation boundary (P1) and
SQLite query casing that could disable durable heartbeat renewal (P2).

- [x] Require caller identity on all existing autonomous-loop entry points and
  compare it with the durable owner before state disclosure or mutation.
- [x] Exercise foreign approve/decline after restart at every approval gate and
  reject missing/invalid identities and cross-user resolver/recovery/read calls.
- [x] Use SQLite-reported actual database backing in both renewal repositories,
  avoiding URI reinterpretation for casing, decoded tokens and repeated query keys.
- [x] Open worker and renewal connections with consistent explicit URI semantics.
- [x] Document the required caller API migration and URI filename behavior.

Verification: full local suite passed with 611 tests and 91.69% coverage (90% floor).
Regression cases compare SQLite-reported backing for ordinary, case-variant,
percent-encoded, repeated-key and fragment-bearing URI filenames. Owner tests cover
approve/decline and all three ambiguous-outcome gates across process restart.

Additional P1 follow-up: production runtime validation now asks SQLite for actual
URI backing, preserves URI text, and rejects encoded/fragment-bearing memory
paths and invalid URIs. Durable case-sensitive and repeated-key paths remain
accepted. Local verification: 623 tests passed with 91.71% coverage.

The subsequent exact-head review found percent-encoded NUL bytes could make SQLite
truncate a URI filename. Runtime validation now rejects raw or decoded NUL bytes
before opening the database URI, with coverage in every runtime environment.

Release review of `c61a1a6fe6` found live job discovery bypassed the runtime network
kill switch. Job providers now use the same execution-boundary guard as employer
signals, and default `AgentFactory` job-search composition is network-disabled unless
an explicitly enabled validated runtime configuration is supplied.

Release review of `87696564b7` found the new public `RuntimeConfig` annotation was
unavailable to runtime introspection. The factory now imports that type from the
cycle-safe config module at module scope, with regression coverage for
`typing.get_type_hints()` on both annotated factory methods.
The runtime package also advertises and caches its lazy composition exports so
`dir()` and `inspect.getmembers()` preserve the established public API surface.

## 9. Operational service composition

Status: **COMPLETED**

Goal: turn the existing read-only operational API into a safely composable deployment
unit without adding a real provider or crossing an external-action boundary.

Acceptance criteria:
- [x] Validate production runtime configuration before opening the service database.
- [x] Require a deployment-secret bearer token without storing it in `RuntimeConfig`.
- [x] Compose the SQLite probe, API service, and WSGI adapter through one public root.
- [x] Use request-local SQLite connections that are safe under threaded WSGI servers.
- [x] Open operational inspection read-only so missing storage cannot be masked.
- [x] Add regression tests for health, authenticated inspection, invalid secrets, and cleanup.
- [x] Document the deployment contract.

Delivered: `build_operational_app_from_env()` validates environment and operational
authentication, then wires the configured SQLite database to the read-only probe and
WSGI API. Each request opens and closes SQLite in its own execution thread, and the
runtime rejects new requests after idempotent shutdown. Invalid bearer tokens fail
before runtime validation can probe a SQLite URI or create a database file. Liveness
and routing do not open SQLite; inspection failures stay inside the sanitized API error
boundary. Inspection uses SQLite read-only mode, so lost storage is never replaced by
an empty database. Runtime URI classification is side-effect free, including for
valid writable URI text, so composition cannot recreate lost storage before the
read-only probe executes. Empty and `:memory:` URI paths fail the production durability
gate regardless of an explicit `ro`, `rw`, or `rwc` mode. No network
provider, consequential action, production
credential, or server process is enabled by this composition.

The latest exact-head review also identified SQLite's `vfs=memdb` backend as an
ephemeral database despite its non-empty URI path. Production validation now rejects
that VFS using SQLite's percent-decoded, last-value-wins query semantics, with
regression coverage for encoded and repeated parameters.

## 10. Candidate-facing approval contract

Status: **COMPLETED**

Goal: define the security boundary a future approval UI must use before adding any
web interface or write endpoint.

Acceptance criteria:
- [x] Expose only the pending action, not the full candidate profile or loop request.
- [x] Bind each response to the owner, run, durable state version, and displayed action.
- [x] Use a strict versioned response schema with explicit approve/decline values.
- [x] Reject stale, foreign, malformed, or action-mismatched responses.
- [x] Keep the contract independent of HTTP and consequential provider adapters.

Delivered: `CandidateApprovalPrompt` produces a minimal JSON-safe view of the pending
human action. Its SHA-256 fingerprint covers the exact action kind, title, and details
shown to the candidate. `CandidateApprovalSubmission` rejects ambiguous booleans,
unknown fields, unsupported schema versions, malformed digests, and non-explicit
decisions. The prompt is a deeply immutable canonical snapshot. Ordinary approvals are
atomically revalidated and persisted under the existing execution lease without
reducing the response to an unbound boolean; stale concurrent responses therefore
cannot approve a later gate. Reconciliation actions remain excluded because they need
provider-verified, kind-specific outcomes. No write endpoint, provider, or action
execution is introduced.

Delivered: an authenticated dependency-free WSGI boundary reloads the owner-scoped
durable prompt and submits the strict approval contract immediately before the existing
atomic resume gate. It fails closed on incomplete or oversized bodies, hides foreign
and missing runs identically, returns no-store responses, and publishes a matching
OpenAPI 3.1 contract. Regression tests cover authentication-before-storage, owner
isolation, URL/payload binding, bounded parsing, incomplete reads, and API discovery.

Delivered: a candidate-service composition root requires the explicit
`CAREER_AGENT_CANDIDATE_SANDBOX=true` gate and refuses network-provider or
consequential-action runtime flags. It owns durable SQLite loop state, accepts only an
injected bearer resolver and job seeds, and wires deterministic no-I/O application,
communication and calendar adapters. Runtime lifecycle and fail-closed configuration
paths have regression coverage. Memory-backed storage is rejected, and each WSGI
request builds repositories over a connection opened in its own worker thread. No
storage is opened for unauthenticated or unroutable API requests. No production
credentials or real providers are used.

Crash recovery hardening: authenticated candidates can invoke the owner-scoped
`POST /v1/candidate/runs/{run_id}/continue` endpoint to resume an already-approved
non-human phase after process failure. Human-gated runs remain blocked with 409,
foreign runs remain undisclosed, and the operation opens storage only after auth.
Recovery refusal uses a dedicated human-gate error so a concurrent execution lease
or unexpected runtime failure cannot be mislabeled as pending human action. The
OpenAPI contract enumerates both human-gate and execution-conflict `409` payloads.

Delivered: the sandbox-only `POST /v1/candidate/runs` endpoint authenticates before
reading its body or opening storage, derives ownership exclusively from the bearer
identity, and accepts a strict versioned schema containing only a stable run ID,
keyword, bounded candidate profile, and optional location. Duplicate run IDs return a
sanitized conflict; provider, credential, messaging, calendar, and action-control
fields are rejected. End-to-end composition coverage starts a durable run through
HTTP and reads its approval gate after restart-safe persistence.

Release hardening scopes each caller-supplied idempotency ID to the authenticated
owner before persistence and returns the resulting opaque durable run ID. Equal
client IDs from different candidates no longer conflict or reveal foreign state.
Runtime schema-version parsing also accepts every JSON numeric representation of the
OpenAPI integer constant `1`, while continuing to reject booleans and other values.
Numeric JSON lexemes are parsed exactly, canonical owner identities feed the scope,
and an identical lost-response retry returns the existing run without new execution.
If two identical starts race between lookup and insert, the losing request reloads and
returns the durable winner; a different request still fails closed as a conflict.
HTTP and programmatic runtime starts use the same owner-scoped transition, preventing
mixed entry points from creating a second run under the raw caller ID.
Upgrade compatibility probes a caller's legacy unscoped ID through an owner-checked,
request-matching replay path before creating a scoped run. Unsupported decimal
exponents are classified as invalid input at both JSON endpoints rather than 500s.
New durable IDs use a `scoped-v2:` generation namespace longer than the legacy input
limit, while owner-checked replay preserves transitional SHA-256 scoped runs. This
prevents a legacy caller from pre-seeding another owner's derived key. Rollback to a
pre-scope build is forbidden after any scoped start; recovery must roll forward.
Because pre-scope workers cannot understand owner-scoped keys, mixed-version serving
is forbidden. Runtime startup requires an explicit drain-and-replace cutover gate and
the deployment protocol records the required worker drain, verification, and rollback
ordering.

Delivered: authenticated read-only run-status discovery exposes only a strict
versioned status projection. Ownership is checked before projection; foreign and
missing IDs return identical responses. Reads do not advance or save state or claim
execution. The request opens existing SQLite state read-only and checks durable owner
metadata before private-payload decoding, preventing missing-store recreation and
foreign corruption disclosure. Restart/thread, human/reconciliation/terminal,
authentication, method and sanitized-failure regression tests cover the boundary and
published OpenAPI schema.

Delivered: a candidate-facing sandbox interface now uses the existing start, status,
recovery and prompt-bound approval contracts. It keeps bearer credentials only in
page memory, renders server data through text-only DOM APIs, and submits the exact
run ID, state version, and action fingerprint that the candidate inspected. Static
delivery has no-store, anti-sniffing, no-referrer and restrictive CSP headers; tests
cover the route, security contract, method boundary, and absence of browser storage.
Merge into `main` still requires explicit user approval.

Release review hardening: all run-derived application, communication and interview
identifiers now use a deterministic bounded namespace when a caller supplies a long
programmatic run ID. The full 200-character run contract remains accepted without
allowing later phases to fail on suffixed child identifiers; short-ID compatibility is
unchanged and regression coverage exercises the maximum-length restart-safe flow.
The `run-child:` namespace is reserved at new-run creation. Existing reserved-prefix
runs retain their legacy operation keys and replay durable outcomes after restart.
Long-run creation rejects an existing legacy namespace alias before persisting intent.
Retained application, message and external-action rows preserve that reservation even
after the legacy loop snapshot is deleted.

The project is not considered complete merely because modules exist. Completion requires a verified end-to-end flow that safely survives restarts, prevents duplicate consequential actions, and stops at explicit human gates for real-world decisions.

Approval API hardening: duplicate JSON members are rejected before submission
validation or durable resume, including repeated decisions and escaped names.
Delivered: CI executes the sandbox start, prompt inspection, prompt-bound approval,
runtime/server restart, and durable status recovery flow in a fresh headless Chromium
session against the sandbox-gated SQLite composition. The test verifies exactly one
successful `application.submit` row with a `fake:` provider ID; no messaging or calendar
action is exercised, so it cannot send applications, messages, or calendar changes.

Delivered: a deployable candidate-service startup validates the IP listener, port,
secret bearer token and owner identity before binding; it retains the sandbox/cutover
and no-I/O gates, uses constant-time authentication, and closes the listener/runtime on
SIGTERM, interruption, or bind failure.

Delivered: the candidate service now exposes public, non-cached liveness and readiness
routes. Readiness opens the configured durable SQLite database read-only and fails with
a sanitized `503` without creating missing storage; candidate workflow routes remain
behind bearer authentication.

Delivered: the candidate service now has a minimal container boundary that runs as
numeric UID/GID `10001:10001`, keeps network/consequential-action switches disabled,
and writes only to an explicit durable data mount. CI builds and starts the image with a
read-only root filesystem and constrained temporary filesystem, then verifies the
non-root identity plus live health/readiness probes without real I/O.

Delivered: release CI pins every third-party action by immutable commit, records the
exact source commit and built container's content-addressed image ID, generates and
validates an SPDX 2.3 SBOM, retains the vulnerability report, and checksums the complete
evidence bundle. A pinned scanner fails the build for fixed critical vulnerabilities;
evidence is uploaded under the exact source commit only after the runtime smoke passes.
Checkout and evidence use the same exact SHA, repository-local scanner configuration is
disabled through runner-owned explicit configs, and the SBOM is validated against the
vendored official SPDX 2.3 JSON schema after verifying its upstream SHA-256. The evidence
pipeline runs in a clean job that never executes PR-controlled pytest code, preventing
cross-step environment poisoning. Python validation also runs isolated from runner-owned
temporary storage outside the untrusted checkout, preventing repository module shadowing.
Release evidence runs only after reviewed code reaches the integration branch; pull
requests cannot use a modified workflow definition to fabricate trusted evidence, while
their complete test and coverage gate checks the merge result before merge. Integration
release evidence explicitly depends on that test job succeeding.
The gate does not publish an image, use production
credentials, or enable real actions.

The next product priority is promotion and rollback integrity: publish immutable
candidate artifacts to a non-production registry, attach provenance/attestation, and
verify digest-only deployment without enabling real providers or consequential actions.

Promotion-contract groundwork: release CI now emits a canonical versioned
`candidate-release-manifest.json` binding the reviewed source commit and local Docker
image/config ID to SHA-256 digests of the exact identity, SBOM and vulnerability evidence.
The parser rejects duplicate/unknown fields, incomplete evidence, mutable identifiers
and any false publication status. Registry publication, registry manifest identity and signed
provenance remain the next isolated unit; the current artifact must not be represented
as a published deployable image.

Delivered in the registry-contract unit: a canonical non-production publication receipt
binds the reviewed source commit, promotion-manifest checksum, immutable registry
manifest reference, and provenance checksum. Digest-only deployment validation rejects
tags, alternate repositories, and digest substitution. Real registry publication and
provenance generation remain intentionally outside this credential-free unit; CI wiring
to a sandbox registry is the next promotion priority.

Delivered in the sandbox-publication unit: trusted integration CI starts a pinned,
ephemeral localhost registry, pushes the candidate under an exact-source tag, derives
the registry manifest digest from the completed push, removes the tag locally and pulls
the candidate back by digest. The release bundle records the digest-only reference,
canonical provenance and non-production publication receipt and checksum-verifies all
three. The registry is destroyed after the job and no external registry, credential or
deployment is used. The next isolated promotion priority is a dry-run promotion/rollback
plan that accepts only this revalidated receipt and preserves the prior digest as the
rollback target.

Delivered in the dry-run promotion-plan unit: a canonical non-production,
non-executable plan revalidates the manifest, provenance and publication receipt before
binding its exact digest-only promotion target to a distinct immutable rollback target
in the same trusted repository. Exact-match guards reject either target being
substituted. No registry, orchestrator, credentials or deployment adapter is present.
The next isolated promotion priority is a provider-neutral execution port with explicit
human approval, idempotency and uncertain-outcome reconciliation, initially backed only
by an in-memory fake.

Delivered in the promotion execution-port unit: the exact dry-run plan is persisted as
an external-action intent before execution, literal human approval is required, a stable
operation ID suppresses duplicate execution, and restart or provider-result mismatch
moves the transition to reconciliation rather than retrying. Independent reconciliation
can confirm the exact durable outcome or safely return a verified no-effect operation to
prepared state. A success requires independently observed target and rollback references
that exactly match the durable plan. The only adapter is deterministic, in-memory and
no-I/O. Unclassified provider exceptions are treated as ambiguous unless explicitly
identified as pre-execution failures, and identical reconciliation decisions are
restart-safe to replay. The next
isolated priority is an authenticated candidate/operator approval projection for this
release action; no real orchestrator adapter or deployment credential may be added yet.

Delivered in the operator approval-contract unit: only prepared dry-run promotion
operations can be projected, the exact `release:promote:dry-run` scope is mandatory,
and approve/decline submissions are rebound to the current durable intent before use.
The fingerprint covers the authenticated operator, operation ID and visible transition,
so foreign, under-scoped, stale and substituted decisions fail closed. The contract is
transport-neutral and performs no external action. The next isolated priority is a
sandbox-only authenticated HTTP boundary that consumes this contract without adding a
real orchestrator adapter or deployment credential.

Delivered in the sandbox HTTP-boundary unit: authenticated operators can fetch the
immutable promotion prompt and submit approve/decline decisions through a strict,
documented WSGI endpoint. Operator identity, scopes, and URL operation identity are
bound server-side; malformed, under-scoped, stale, substituted, or ambiguous JSON
requests fail closed. The endpoint validates intent only and cannot execute a
promotion. Backend authentication and operation-read failures are isolated from
request validation: even ValueError, TypeError and PermissionError return sanitized
500 responses, while invalid requests and insufficient scopes retain 400/403.
Persisted-intent projection failures, including malformed payloads and bad plan
checksums, also return sanitized 500 responses for both GET and POST.
Scope authorization precedes repository access to prevent operation enumeration.
Non-prepared operations return 409, and OpenAPI describes both JSON success bodies.
Foreign workflow actions return 404 regardless of lifecycle state; release scope
cannot reveal their state. OpenAPI also defines JSON error contracts for all statuses.
Delivered: PromotionApprovalReceiptService persists immutable approve/decline decisions
and atomically claims approval before the fixed no-I/O dry-run executor. Restart,
replay, scope/identity, stale fingerprint, and immutable-decline regressions are covered.
Claimed receipts are never automatically reused after a crash.
Explicit recover_claim() classifies mid-execution crashes for reconciliation without
adapter I/O; SQLite restart tests cover unauthorized recovery and repeated recovery.
The receipt service is exported from the public release package.
Recovery covers both receipt/executor boundaries: claimed but not started receipts
require inspection, while durable success completes the receipt only after exact
result validation. Neither path calls the adapter; repeated recovery is idempotent.
Durable success results must contain exactly the four execution evidence fields;
recovery never fills omitted evidence from dataclass defaults.
Delivered: optional HTTP receipt composition durably records validated decisions,
preserves identical retry behavior, returns 409 for conflicting decisions and sanitized
500 for receipt backend failures. SQLite restart tests prove recording does not execute.
Delivered: authenticated POST-only execute-dry-run/recover-dry-run routes consume
durable approvals or inspect interrupted execution via the fixed no-I/O service.
Strict version-only JSON, sanitized errors, replay conflicts and OpenAPI are covered.
HTTP regression coverage also verifies missing/under-scoped/foreign operators on
both execution routes and idempotent SQLite restart recovery without adapter calls.
Execution HTTP errors distinguish explicit lifecycle conflicts (409) from corrupt
durable receipts/plans (sanitized 500), with no durable mutations on corruption.
Delivered: explicitly gated operator sandbox runtime with injected authentication,
durable request-local SQLite, fixed no-I/O execution and deterministic cleanup.
Integration tests cover approval/execute across runtime restart, replay/recovery,
concurrent request threads, policy rejection and response-failure cleanup.
Startup also verifies main-database writability with transactional DDL rolled back
before returning; read-only/immutable URIs fail composition without changing data.
Delivered: loopback-only operator HTTP entrypoint with startup auth/listener
validation, fixed dry-run scope, constant-time bearer comparison and context-managed
shutdown. Actual HTTP smoke covers approval, runtime restart, execution, replay and
recovery; invalid startup and listener-bind cleanup are regression-tested.
Delivered: operator-service public GET-only liveness and read-only schema readiness,
sanitized unavailable responses and no replacement DB creation. Startup initializes
operation schema before binding. HTTP smoke and missing/empty/corrupt/ready storage
regressions prove probes do not mutate state; service OpenAPI includes both probes.
Readiness validates all eight repository read columns, rejecting partial legacy
schemas rather than reporting ready before workflow reads fail.
Readiness also requires a single-column non-partial unique operation-ID index.
Immutable schema snapshots avoid sidecar creation; WAL/URI configurations report
unavailable rather than ignoring live WAL evidence or writing checkpoints.
Operator-service container smoke is now defined in the trusted integration release
job before evidence upload: the same built image runs non-root with no external
network, a read-only root filesystem, private temporary storage and synthetic auth.
It checks public probes, authentication, database permissions and graceful SIGTERM.
The container scenario also seeds a synthetic intent through the repository, records
prompt-bound approval via HTTP, restarts the same container/volume, and checks dry-run
execution, replay conflict and safe recovery. No HTTP intent creation or live adapter
is enabled. Its integration CI passed on f3891a3.
Abrupt-process regression now exits a real SQLite worker without cleanup after the
receipt claim, inside the adapter, and after durable success. Recovery classifies
ambiguous claims for reconciliation, preserves recorded success, rejects consumption
replay and never invokes the recovery process's adapter. This is process-loss evidence,
not a power-loss/filesystem durability guarantee or a real deployment test.
Operator-facing GET status visibility now exposes a minimal authenticated operation
snapshot without execution, recovery classification, private payload or errors.
It is explicitly not approval-receipt state or permission to retry. Regression tests
cover every lifecycle state, auth-before-storage, method gates and corrupt intent.
HTTP restart coverage proves prepared status survives startup without database
mutation. The container smoke now checks exact prepared/succeeded snapshots around
execute, replay conflict and recovery, no-store and byte-identical storage after GET,
then restarts again to verify retained terminal status. Embedded Python smoke scripts
are executed by a regression against the real local HTTP composition; Docker evidence
still requires reviewed integration CI. Next: verify this expanded release-job smoke.
Operator sandbox UI now offers status and prompt-bound approve/decline with credentials
only in tab memory, stale-prompt invalidation and no execution/recovery controls.
It is served only by the explicitly gated runtime with restrictive no-store/CSP headers.
Service OpenAPI documents the public static HTML GET route, no-store response and
empty 405/Allow: GET contract; a regression prevents this surface becoming undiscoverable.
Browser acceptance now covers both approve and decline, closes the original service,
then reopens the same SQLite store: approval permits one no-I/O execution and rejects
replay; decline denies execution and leaves the operation prepared. UI decisions alone
never execute. Chromium evidence is required from CI, not local skipped browser tests.
Next priority: candidate-facing workflow dashboard beyond sandbox approval controls.
First dashboard slice: candidate run overview renders authenticated status snapshots
with decision-needed/in-progress/finished, phase, version and durable ID. Only the
latest matching response may update it; context changes and other results clear it.
Chromium acceptance covers waiting and terminal snapshots across runtime restart.
Next dashboard slice: candidate-owned run discovery/listing, not local browser storage.
Delivered backend discovery boundary: RuntimeCandidateApp.list_run_statuses reads
bounded owner-only keyset pages through the existing read-only status projection.
SQL ownership filtering precedes payload parsing; tests cover foreign corrupt data,
limits, pagination/restart, closed runtime, and byte-identical/missing storage.
Next: authenticated HTTP collection contract and UI selection of owned runs.
HTTP collection delivered at GET /v1/candidate/run-statuses with auth-before-storage,
strict bounded limit/after query parsing, no-store, sanitized backend errors and OpenAPI.
Regression tests cover real SQLite composition plus invalid/auth/method boundaries.
Next: UI list pagination and explicit selection with context invalidation.
Integration review P2 follow-up: failed next-page requests retain the attempted cursor
for explicit retry; only the current list generation can restore it. Token changes
still discard every cursor. Chromium acceptance injects a 503 then retries page two.
Delivered sandbox UI discovery: load owned run pages, next-page cursor, text-only
selection buttons, token-change clearing and stale-response/selection guards.
Chromium restart acceptance traverses 27 owned runs and selects the retained terminal
run without approval or execution. Next: richer candidate application/tracker views.
Candidate UI auth-context hardening: token edits now clear prompts/results and revoke
pending start selection. A generation guard rejects late responses even if inputs are
changed back; browser regression requires a fresh prompt before approval after token edits.
Integration review P2: programmatic run selection advances context/result generations
too, preventing a pending status for the previous run from rendering under the new ID.
Review hardening applies no-store/byte stability checks to prepared snapshots too.
The terminal restart baseline is copied from the stopped container before startup;
its SHA-256 must still match after readiness, catching startup-time state rewrites.
Execution evidence remains required from the post-merge integration CI; local
workflow contract tests alone do not establish container readiness.
HTTP receipt retries remain idempotent after execution/restart: only an identical
existing receipt can be returned; changed decisions conflict and no execution or
receipt lifecycle mutation occurs during recording.

## 11. Candidate-outcome adaptive ranking

Tracker UI review P2: returned application IDs/cursors remain opaque; browser trim
no longer overrides server Python canonical-whitespace rules. Chromium regression
round-trips a 200-scalar U+FEFF/emoji cursor and renders its application snapshot.

Candidate tracker UI delivered: explicit authenticated page loads, next-page/retry,
text-only application snapshots and token-generation invalidation, without action
controls. Chromium restart acceptance reads the persisted applied/version-2 snapshot
and verifies token edits remove it. Next: richer candidate detail/history views.

HTTP review P2: application collection raw queries now accommodate 200 four-byte
Unicode scalars percent-encoded in a cursor (2400 characters plus query overhead).
Decoded cursor bounds remain 200; restart pagination covers a maximum emoji cursor
and oversized raw/decoded queries fail before storage access.

HTTP tracker collection delivered: GET /v1/candidate/applications authenticates before
query/storage, shares bounded strict pagination parsing and reads request-local owner
snapshots without writable repositories. OpenAPI describes minimal fields and errors.
Next: candidate UI application list; no approval or execution from this collection.

P1 review correction: validate projected scalar fields independently of aggregate
timeline invariants. Every lifecycle status/version is covered after SQLite restart,
including malformed private timeline metadata that the projection must never load.
P2 review correction: writable application schema initialization backfills the
owner/application-ID keyset index. An EXPLAIN QUERY PLAN regression requires indexed
range search without temporary sorting; projection reads remain schema-free.
Scalar lifecycle validation now rejects unreachable status/version combinations using
the domain transition graph. All statuses at version one remain valid, matching the
existing aggregate constructor/repository contract, rather than assuming saved-only
initialization. Restart regressions cover each supported initial status and impossible
long histories without reading timeline children.
Candidate tracker dashboard groundwork delivered: bounded read-only owner-filtered
application pages expose minimal status/job/timestamp fields without loading private
children or mutating storage. Restart, foreign-corruption isolation, strict bounds
and missing-storage regressions cover the projection. Next: authenticated HTTP/UI
application tracker views; no real provider actions are enabled.

P1 recovery isolation: CareerOrchestrator resume/continue entry points now require
the caller's authenticated user_id. Foreign and missing runs fail identically
before lease acquisition, approval persistence, or execution. Existing recovery
callers supply owner identity explicitly; in-memory and SQLite regressions cover
foreign/missing/invalid identity and preserve legitimate owner recovery.

Diagnostic hardening: quoted secret redaction consumes escaped quotes and backslashes
without exposing secret suffixes. Operational bearer parsing accepts repeated spaces
after the scheme while preserving exact credential comparison.
Compatibility guard: configured tokens with leading/trailing spaces retain exact
matching; only an additional comparison permits repeated scheme-separator spaces,
never trimming credential suffixes.
Repeated separator compatibility retains the complete configured-token suffix,
including its leading spaces; only a space-only surplus prefix is discarded.

Operational HTTP hardening: bearer authentication scheme parsing is case-insensitive,
while credential comparison remains case-sensitive and constant-time. Regression
coverage accepts four scheme casings and rejects altered credentials for each.

Recovery hardening: zero-direct-evidence snapshots accept only the exact rounded,
bounded exploration boost emitted by AdaptiveJobRanker (or neutral zero). Negative
or arbitrary zero-evidence adjustments remain invalid. SQLite approval-gate restart
coverage includes unrelated historical employer/attribute outcomes.

Security regression hardening: returned agent messages are redacted before entering
career step state, SQLite run persistence, or durable run summaries. Regression
tests cover both successful and failed agent results and reloaded run state.

Status: **IN PROGRESS**

Goal: move job selection beyond static relevance by learning safely from the
candidate's observed application funnel while keeping decisions explainable.

Acceptance criteria:
- [x] Derive employer outcome evidence from durable candidate-owned applications.
- [x] Treat interviews and offers as stronger evidence than direct rejection.
- [x] Shrink sparse evidence toward neutral and strictly bound its ranking impact.
- [x] Preserve tenant isolation and expose an explanation for every adjustment.
- [x] Cover positive, negative, sparse, historical-funnel, and isolation behavior.
- [x] Feed the adaptive scorer into the durable autonomous-loop discovery phase.
- [x] Extend learning from employer outcomes to role/skill attributes with bias and
  exploration guardrails.

Delivered in the first unit: `AdaptiveJobRanker` composes the existing relevance
ranker with candidate-specific application outcomes. It recognizes an interview even
when the application later ends in rejection, ignores non-outcome states, applies
confidence shrinkage, and caps the adjustment so current relevance remains
authoritative. The autonomous discovery phase now persists the baseline, adjustment,
final score, evidence count, and explanation beside the selected job, preserving the
decision across restart. Tracked applications retain normalized role/requested-skill
attributes, and repeated outcomes transfer safely across employers. Attribute evidence
requires two applications, each application counts once even when multiple attributes
match, the relevance-relative cap still applies, and a small bounded exploration bonus
prevents unseen attributes from being permanently excluded.
