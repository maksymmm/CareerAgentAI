# CareerAgentAI

CareerAgentAI is a modular AI-powered career platform built with Clean Architecture principles.

The project is designed to automate and coordinate career-related workflows while keeping business logic deterministic, testable, and independent of external services.

---

# Current Architecture

```
CareerAgentAI
│
├── application
│   ├── agents
│   │   ├── job_application
│   │   ├── job_search
│   │   └── resume
│   ├── brain
│   ├── career
│   │   ├── decision engine
│   │   ├── orchestration
│   │   ├── pre-vacancy opportunity pipeline
│   │   └── outreach drafting
│   ├── communication
│   │   ├── provider-neutral draft/send/read/reply protocol
│   │   └── safe dry-run provider
│   ├── external_actions
│   ├── jobs
│   │   └── durable application tracker
│   ├── memory
│   ├── search
│   ├── storage
│   └── workflow
│
└── tests
```

---

# Core Components

## Agent Brain

Coordinates application-level requests.

Responsibilities:

- receive requests
- load memory snapshots
- resolve specialized agents
- start and continue workflows
- return immutable responses

The Agent Brain coordinates; domain-specific decisions remain in dedicated services.

---

## Career Orchestrator

Turns a career objective into a bounded execution plan.

It supports:

- deterministic planning
- isolated run state
- unique run identifiers
- bounded execution
- agent failure handling
- human-action gates
- in-memory resume of paused runs; `CareerOrchestrator.resume()` and
  `continue_run()` require authenticated `user_id` as a keyword argument and reject
  foreign/missing runs before acquiring an execution lease or changing state
- SQLite-backed recovery of paused runs after process restart
- run summaries in memory

A human-gated run pauses instead of performing the next external action automatically.
The candidate sandbox also serializes each browser-side start request: its start
control remains disabled and exposes a busy state until the authenticated request
settles, preventing duplicate concurrent POSTs while stale results are discarded
after the token or any submitted start field changes.
Status refreshes are likewise serialized in the browser so repeated activation cannot
issue overlapping authenticated reads or race the displayed run snapshot.
Approval prompt loading keeps a separate in-flight lock even when run or token changes
invalidate the response, preventing a replacement request from overlapping the stale read.

---

## Career Decision Engine

Selects the next career action from the objective and current progress.

The decision contract is explainable through:

- selected action
- rationale
- confidence
- metadata describing the decision source

The implementation is deterministic and provider-independent so an LLM/ML planner can later implement the same contract without changing orchestration boundaries.

---

## Job Search

The active Arbeitnow adapter validates its timeout as a finite positive number and
its pagination/response-size limits as positive integers before any request. It
bounds each response to 2,000,000 bytes by default
(`max_response_bytes` accepts a positive integer). It reads at most one extra byte
to detect overflow before UTF-8/JSON decoding, closes the response on rejection,
and aborts the search rather than returning partial pages. A deliberate retry
starts from page one. This bounds response bytes, not total decoded-object memory.
Every redirect destination is validated before urllib follows it, and the final
response URL is checked again before its body is read. Only the expected Arbeitnow
HTTPS endpoint is accepted (omitted port or literal `:443`, at most one trailing
slash, no credentials or fragment); cross-host, downgraded, alternate-path and
ambiguous authority/path spellings fail closed without a follow-up request.
Each decoded page must also contain a list-valued `data` field and a valid optional
pagination envelope. Malformed data, links or next-page state abort the entire search
instead of silently returning an incomplete prefix; an explicit retry starts again at
page one. Every transport response must provide an integer HTTP status in the protocol
range and a byte body; malformed adapter responses fail deterministically and close
before decoding; a missing status is never treated as an implicit success. When a
`Content-Type` is present it must unambiguously declare `application/json`, optionally
with only a UTF-8 charset; conflicting duplicates and unsafe media declarations are
rejected before reading the body. Every `Content-Length` occurrence must contain only
ASCII digits, agree with any
duplicate, and remain within the configured byte limit; unsafe declarations are
rejected before the body is read, while the bounded
read remains authoritative when the header is absent or understates the body. JSON decoding is strict: duplicate object members, non-finite numeric
constants and excessive nesting are rejected rather than interpreted ambiguously.

The job-search layer provides:

- provider abstraction
- live job-provider integration
- repository-level deduplication
- keyword/company/location/remote/employment/salary filters
- relevance scoring
- explicit sorting

An optional `AdaptiveJobRanker` can refine that relevance ordering from the
authenticated candidate's own durable application outcomes. Employer-level interview
and offer evidence raises a score, while direct rejection evidence lowers it. The
adjustment is confidence-shrunk for sparse history and strictly bounded so historical
feedback cannot overwhelm current job relevance. Every result exposes the baseline,
adjustment, evidence count, and plain-language reason; another candidate's history is
never consulted. Saved, merely applied, and withdrawn records remain neutral.
The restart-safe autonomous loop uses this scorer during discovery and persists the
baseline, bounded adjustment, final score, evidence count, and explanation with the
selected vacancy so a resumed process can audit the original decision.
New tracked applications also retain normalized role and requested-skill attributes.
Repeated outcomes for those attributes can transfer learning across employers, but a
single result is ignored as attribute evidence, duplicate matches from one application
count once, and the existing relevance-relative cap remains authoritative. A small
bounded exploration bonus gives unseen attributes a chance to collect evidence instead
of permanently reinforcing the first observed outcome.

---

## Pre-Vacancy Opportunity Pipeline

The proactive pipeline is designed for companies that may be hiring before a public vacancy is available.

Flow:

```
Signal Provider
      │
      ▼
Opportunity Ranking
      │
      ▼
Bounded Decision
      │
      ├── ignore
      ├── monitor
      └── prepare_outreach
              │
              ▼
        Unsent Outreach Draft
```

The current implementation deliberately stops at `prepare_outreach`.

It does **not** send messages, contact employers, or perform other external communication.

Signals enter through an explicit `OpportunitySignalProvider` boundary. The static provider remains deterministic for supplied data and tests. A production-capable RSS/Atom adapter can also collect real company-owned news feeds over HTTPS. It emits signals only when configured deterministic rules match actual feed entries, retains the entry/feed provenance and observation time, rate-limits requests, bounds response size, reports source failures without fabricating results, and deduplicates provider-native entry identities.

`EmployerIntelligenceService` aggregates those deduplicated source-backed signals by employer while preserving the underlying evidence, latest observation timestamp, signal types, and provenance sources. The same deduplicator is applied before opportunity scoring so repeated provider observations do not inflate a company's score.

---

## Memory Engine

Responsible for career memory snapshots and run summaries.

The engine preserves its original in-memory default while accepting a provider-neutral
memory repository. Its SQLite implementation persists JSON-safe, versioned records
across process restarts, validates stored data on retrieval, and supports indexed
queries by candidate user and memory type. Memory records use stable keys and retain
metadata and timezone-aware creation timestamps without unsafe deserialization.

Resumable career-run state continues to use a separate SQLite persistence boundary so
paused human-gated runs can survive process restarts.

---

## Crash-Safe External Actions

Consequential provider calls are coordinated through a provider-neutral external-action service and a durable SQLite operation repository. Callers supply a stable operation ID, prepare the intent, obtain explicit human approval, and only then request execution.

Operations move through `prepared`, `in_progress`, `succeeded`, `failed`, and `reconciliation_required` states. Intent is committed before an adapter is invoked and the terminal outcome is committed afterward. Reusing an operation ID with a different intent is rejected, while repeating a completed invocation returns its existing result without another provider call.

An `in_progress` operation found after restart is treated as ambiguous. It moves to `reconciliation_required` and is **not** blindly executed again; a future provider-specific reconciliation workflow must determine the real external outcome. Adapters must also pass the operation ID to providers as their idempotency key.

The current capability is infrastructure only: no production communication or application provider is configured, and no real external action is sent.

---

## Communication Adapter

Recruiter and employer communication is isolated behind a provider-neutral protocol
covering draft, send, read, and reply primitives. The application service validates
plain-text content and provider identifiers, persists message/thread/reply identifiers
in SQLite, and retains them across restarts.

Every send and reply—including local dry runs—requires explicit human approval and
is executed through the crash-safe `ExternalActionService`. Stable operation IDs
suppress duplicate requests and provider calls. A process restart after an operation
entered `in_progress` moves it to `reconciliation_required` rather than risking a
duplicate message. Provider failures are persisted as terminal failed attempts; a
new deliberate attempt must use a new operation ID.

Only persisted drafts can begin a new send operation, so changing the operation ID
cannot resend an already-outbound message. Provider delivery responses must preserve
the exact prepared content and conversation identifiers and must report an outbound
state before they are persisted. Message timestamps are normalized to UTC, while
thread retrieval compares timestamp instants so legacy offset timestamps remain
chronologically safe.

Immediately before a provider call, SQLite atomically binds the draft to exactly one
delivery operation. Stale, restarted, or concurrent operations with other IDs cannot
cross that claim, even if they were prepared earlier. If a provider returns success
but its response cannot be validated or durably stored, the operation moves to
`reconciliation_required` rather than being mislabeled as a safe failure or retried.
Successful reply operations can be replayed with the same operation ID after restart
without another provider call. A provider's explicit pre-delivery failure releases
the claim so a deliberate new operation can retry; uncertain or post-delivery failures
retain the claim and continue to block competing sends until reconciliation.
Concurrent workers may also idempotently persist the same previously unseen message:
the losing insert reloads and returns the identical winning row, while reuse of the
same message ID for different content or direction remains a hard conflict.
Thread chronology uses an indexed integer UTC epoch at exact microsecond precision;
legacy ISO timestamps are safely backfilled during repository initialization. Message
text validation also rejects lone Unicode surrogate code points before any adapter or
SQLite boundary is reached.

The included fake provider is deterministic, in-memory, and dry-run only. It performs
no network I/O and needs no credentials. No production communication provider or
credential configuration is included.

---

## Scheduling and Interview Coordination

The candidate sandbox offers Clear this tab's session: it removes the bearer token,
profile/start inputs and all loaded run/application/event data, invalidates pending
reads/starts and disables approval prompts without making a network request. This is
local clearing, not server-side token revocation or cancellation of accepted work.

Candidate sandbox My interviews and events loads owned event pages, supports
next-page/retry and View event detail, and renders allowlisted text-only snapshots
including exact UTC/local times and both offsets. Page loads/token edits invalidate
pending details; token edits clear all schedule state. Malformed responses and raw
backend errors never enter the result pane. No calendar action controls are added.
The browser verifies the IANA zone's local date/time at both UTC instants. Unknown
zones or browser/server timezone-database disagreement fail closed with a retry hint;
historical dates unsupported by the browser are not displayed as verified schedules.

Authenticated `GET /v1/candidate/schedule-events?limit=25&after=...` exposes owned
ID-keyset pages with strict bounded query validation, bearer-derived ownership,
no-store, sanitized errors and OpenAPI. It uses existing read-only storage, cannot
create a database or execute work, and never loads private calendar identifiers.
The collection's `after` is a range cursor, not an ownership credential. Read-only
candidate UI discovery/detail is the next delivery unit.

The collection also accepts an optional validated `status` filter (`proposed`,
`accepted`, `declined`, `reschedule_requested`, or `cancelled`). Filtering remains
owner-scoped in SQL, preserves event-ID keyset pagination, and is available from the
read-only candidate dashboard without enabling calendar actions.

`RuntimeCandidateApp.list_schedule_events(user_id=..., limit=25, after=None)` reads
bounded owner-only event snapshots in stable event-ID order (maximum 100), using
the same minimal exact-time projection as detail. `next_after` is the last returned
ID when more rows exist, not an ownership credential. Reads never initialize schema,
load provider/application IDs or action claims, or execute calendar actions. Writable
repository startup installs/backfills the owner/ID keyset index. HTTP collection and
candidate discovery UI remain follow-up work.

`RuntimeCandidateApp.get_schedule_event(event_id, user_id=...)` returns a versioned
read-only owner-only event snapshot with employer/location, lifecycle status, UTC
instants, local dates/times and explicit start/end UTC offsets. Reads never create
or migrate storage and never load provider/application IDs or calendar action claims.
Foreign/missing events share one failure before deserialization. Trusted callers
must supply authenticated identity. Authenticated GET
`/v1/candidate/schedule-event?event_id=...` exposes this read-only snapshot using
bearer-derived ownership, strict single ASCII event identity, identical foreign/missing
404, sanitized backend errors, no-store and OpenAPI. No calendar action is enabled;
candidate event discovery/UI remain follow-up work.
UTC offsets use `±HH:MM`, with `:SS` retained for historical IANA offsets; local
dates/times and offsets reconstruct the exact UTC instant, including microseconds.

Human-participation career events such as interviews, trial days, phone calls, and video calls are modeled as durable scheduling aggregates. Each event preserves the employer, event type, address or meeting location, UTC-normalized start/end instants, the original IANA timezone used for presentation, lifecycle status, application linkage, and provider identifiers.

The scheduling service exposes an exact human-facing projection with employer, location, local date, local start/end time, timezone, and UTC offset so DST transitions remain unambiguous. SQLite persistence uses integer epoch-microseconds for deterministic ordering without losing timestamp precision, validates persisted state on reload, and prevents duplicate provider-event identities.

Accept, decline, and reschedule are provider-neutral calendar actions. Every consequential response passes through the existing human-approval and crash-safe external-action layer. A durable per-event operation claim prevents stale or concurrent workers from issuing competing calendar responses. Explicit pre-provider failures release the claim for a deliberate retry; uncertain provider outcomes and post-provider persistence failures require reconciliation and are never blindly retried.

The included fake calendar adapter is deterministic, in-memory, and performs no network I/O. No production calendar provider or credential is configured.

---

## Application Tracker

`RuntimeCandidateApp.get_application_history(application_id, user_id=...)` returns
only ordered lifecycle transitions (sequence/from/to/time), the application ID and
state version. Ownership is checked before reading children, and the read-only
transaction pins aggregate and events to the same snapshot. Domain lifecycle bounds
cap the query; malformed sequence/continuity/chronology/version data fails closed.
Notes, metadata, event/operation IDs and claims are never loaded or exposed.
Authenticated `GET /v1/candidate/application/history?application_id=...` exposes this
history with the detail endpoint's strict query, bearer ownership, identical
foreign/missing 404, sanitized errors, no-store and OpenAPI contract. Reads cannot
execute work or create storage. After selecting an application, Load application
history displays allowlisted transitions/version as text only. Empty history and
explicit retry are supported; detail/page/token changes invalidate pending history.
Raw history responses and backend error text never enter the result pane.

`RuntimeCandidateApp.get_application(application_id, user_id=...)` reads a versioned
single-application snapshot with the same minimal fields as the collection. The
trusted caller supplies authenticated identity; foreign and absent IDs raise the
same `KeyError` before private fields are decoded. Reads open existing storage
read-only, omit timeline/metadata/claims, and cannot execute work. Candidate history
views remain follow-up work. Authenticated
`GET /v1/candidate/application?application_id=...` exposes this detail snapshot with
bearer-derived ownership, strict single-parameter validation, no-store and OpenAPI.
The query preserves opaque Unicode IDs without WSGI path-decoding ambiguity;
foreign/missing applications share a 404. No update or execution action is exposed.

`RuntimeCandidateApp.list_applications(user_id=..., limit=25, after=None, status=None)` exposes
minimal owner-only application snapshots with bounded ID-keyset pagination. Reads
open existing SQLite storage read-only, never migrate it, and omit timeline notes,
metadata, skills and execution claims. Identity must come from the trusted caller.
Authenticated `GET /v1/candidate/applications` exposes the same page with `limit`,
`after`, and an optional validated lifecycle `status`, bearer-derived ownership,
strict query validation, no-store and OpenAPI. Status-filtered pages use an
owner/status/application-ID index and retain the same exclusive cursor contract.
It never executes or updates an application. The candidate sandbox displays owned
application pages as text-only job/company/status/version snapshots, with explicit
status selection and next-page/retry controls. A selected status is sent on every
page request; changing it clears the old page. Token edits clear the list and stale responses cannot
restore it. View application loads an owner-authenticated detail snapshot with
timestamps, rendered as text-only allowlisted fields. New selections/page loads/token
edits invalidate older detail responses; failures permit explicit reselection/retry.
No approval/submission controls exist in this tracker list or detail view.

The sandbox runtime's programmatic
`list_run_statuses(user_id=..., limit=25, after=None, phase=None)`
returns owner-filtered, minimal run status pages in stable run-ID order (maximum 100).
The caller must supply the authenticated identity. Reads never create or migrate the
database, execute work, or parse another owner's snapshots. `next_after` is a keyset
cursor, not an ownership credential. Authenticated `GET /v1/candidate/run-statuses`
exposes the same read-only page with optional `limit`, `after`, and a validated exact
lifecycle `phase`; identity comes
only from bearer authentication. Duplicate/unknown/invalid query parameters fail
before storage access. Filtered pages use an owner/phase/run-ID projection index and
retain the exclusive cursor across pages. The candidate sandbox can select a phase,
load owned pages, advance with Next page, and explicitly select a run without
executing or approving it. Filter changes clear old rows and cursors. Token
edits clear the list/cursor; old list responses and selections are generation-bound.
Failed next-page requests retain the attempted cursor for explicit retry, without
restarting pagination or restoring another authentication context.

The application tracker persists an immutable aggregate for each candidate/job pair.
It models validated transitions through `saved`, `applied`, `interview`, `offer`,
`rejected`, and `withdrawn`, while retaining a chronologically ordered timeline.

Both the provider-neutral repository and its SQLite implementation support stable
queries by candidate, job, company, status, and external-action operation ID.
SQLite enforces duplicate prevention for candidate/job pairs and optimistic versions
prevent stale writers from silently overwriting newer state. Application submission
operations can be linked by their stable crash-safe external-action IDs without
causing or repeating any external action. Persisted records use versioned, strict
JSON for timeline metadata and are fully validated after restart; no unsafe
deserialization is used.

---

## End-to-End Autonomous Career Loop

The end-to-end loop now connects ranked job discovery, deterministic career decision
progression, resume preparation, durable application preparation, explicit human
approval, crash-safe application submission, optional recruiter/employer messaging,
application tracking, and interview coordination.

The loop is a bounded state machine with a restart-safe SQLite state repository.
All entry points for existing loop runs (`get`, `resume`, `continue_run`, and the
three reconciliation resolvers) require keyword-only `user_id`. Service callers
must derive it from the authenticated principal, never an untrusted request body.
The loop checks it against the durable request owner before exposing state, changing
an approval, claiming a lease, or resolving a provider outcome. A foreign run and
a missing run both raise the same `KeyError`.
`CandidateApprovalWSGIApp` exposes the minimal prompt and strict approval submission
at `GET`/`POST /v1/candidate/runs/{run_id}/approval`. Authentication is resolved before
durable state access, foreign and missing runs share a tenant-hiding `404`, request
bodies are bounded and must match their declared length, and
`candidate_approval_openapi_document()` publishes the matching OpenAPI 3.1 contract.
The recovery endpoint maps only an explicit pending-human-action signal to
`recovery_unavailable`; execution-lease conflicts keep their conflict response and
unexpected runtime failures remain sanitized internal errors. Its OpenAPI `409`
contract documents both `recovery_unavailable` and retryable `approval_conflict`
payloads.
The sandbox composition also exposes `POST /v1/candidate/runs` to start a durable
candidate-owned run. Its versioned body accepts only a caller-supplied idempotent
`run_id`, search keyword, bounded candidate profile, and optional location. The owner
always comes from the bearer identity; messaging, calendar, provider, credential, and
consequential-action controls are not accepted from the request. The caller-supplied
ID is deterministically scoped to that owner, stored in a `scoped-v2:` generation
namespace outside the legacy caller-ID range, and returned as an opaque durable run ID,
so equal client IDs from different candidates neither conflict nor disclose existence.
An identical retry, including one that loses a concurrent create race, returns the
existing owned run and durable ID; reuse with changed request content remains a
conflict. HTTP and programmatic starts share this same scoped-ID/replay path, so
mixing the two entry points cannot create a raw-ID duplicate. Retries of runs created before owner-scoped IDs were introduced safely
reuse the owned legacy ID after matching the canonical request, avoiding duplicate
execution after cutover. Mixed old/new workers are deliberately unsupported: startup
requires `CAREER_AGENT_CANDIDATE_ID_CUTOVER=drain-and-replace-scoped-v2`, which operators may
set only after draining every pre-scope worker as specified in
`docs/deployment/candidate-id-cutover.md`. Once scoped traffic has started, rollback to
a pre-scope build is forbidden; candidate starts stay disabled until a safe roll-forward.
The same WSGI service exposes a dependency-free candidate sandbox at
`GET /sandbox/candidate`. It can start a sandbox run, inspect its status, load the
current candidate-visible approval prompt, continue recovery, and submit approve or
decline decisions. The static page contains no credentials or durable state: the
candidate enters a bearer token that stays only in the tab's memory.
An accessible dashboard navigation landmark links to start, inspection, pending
decision, overview, applications and interviews. Native fragment links move keyboard
focus without API requests or credential/state changes; a return link reaches the top.
The first keyboard focus exposes a skip link that bypasses the heading and navigation
and moves focus directly to the interactive dashboard content.
The candidate dashboard wraps long IDs and snapshot text without truncation and stacks
action buttons on narrow screens. Chromium checks 320/390 px and desktop layouts for
horizontal overflow while retaining the exact displayed snapshot content.
Changing the token clears the displayed prompt and result; late responses from the
previous input context cannot repopulate them or select a run.
Run and application collections render only validated list fields, never raw API
payloads or JSON parsing errors. Empty pages clear prior rows and disable pagination;
explicit reloads recover after malformed responses without exposing private extras.
Run, application and event live-status summaries report the exact number of rows on
the current page and whether another page is available, without claiming a total.
Those three owned collection regions expose their current asynchronous update through
`aria-busy`; success, failure and authentication/session invalidation always restore
the idle state and stale responses cannot repopulate the collection. Their reload
controls are disabled for the current request so repeated activation cannot create
redundant concurrent reads, and are restored on completion or context invalidation.
Application and event detail controls, together with application-history loading,
apply the same single-request rule. Failure restores an explicit retry control, while
selection, authentication and session invalidation safely retire stale requests.
Private read-only failures use operation-specific generic messages for application
details, history, event details and approval prompts without rendering server payloads.
Application and event view buttons keep concise visible text while their accessible
names include a page ordinal and the validated record ID. The ordinal keeps repeated
controls distinguishable even if assistive technology folds whitespace inside opaque IDs.
Run pages require strictly ascending unique IDs beyond the requested cursor. A next
cursor must equal the last ID of a full page; malformed or replayed pages clear rows
and retain only the attempted cursor for explicit retry.
The run overview summarizes an authenticated status snapshot as waiting for a decision,
in progress, or finished, with its phase, version and durable ID. It is cleared on
context changes or other results and never substitutes for an approval prompt. Decisions copy
the prompt's run ID, state version, and action fingerprint back to the strict API, so
the interface cannot silently approve a changed or stale action. The page is served
with `no-store`, anti-sniffing, no-referrer, and restrictive content-security headers.
CI runs the complete start, prompt inspection, prompt-bound approval, runtime/server
restart, and durable status recovery path in a fresh headless Chromium session against
the sandbox-gated SQLite composition. The browser test verifies that the only persisted
external action is one successful application submission with a `fake:` provider ID;
messaging and calendar actions are not exercised, so it cannot perform external actions.

The sandbox now has a deployable standard-library HTTP entrypoint:
`python -m career_agent_ai.application.runtime.candidate_service`. Startup validates an
IP listener, port, 32+ character bearer token, candidate identity, durable SQLite path,
sandbox flag, and scoped-ID cutover before binding. Authentication uses constant-time
token comparison. SIGTERM and keyboard interruption close both the listener and runtime;
listener-bind failure also closes the already-composed application. The entrypoint keeps
all network providers and consequential actions disabled and uses only deterministic fake
adapters.

The repository also ships a minimal candidate-service `Dockerfile`. The image runs as
the dedicated numeric identity `10001:10001`, keeps Python bytecode and external-action
permissions disabled, and stores mutable SQLite state only under
`/var/lib/careeragent`. Supply the bearer token and candidate identity at runtime; they
are never baked into the image. CI builds the image and proves it can serve liveness and
readiness with a read-only root filesystem, a constrained `/tmp`, and an explicit
persistent data volume.
CI also records the exact source commit and built image's content-addressed ID, generates
a validated SPDX 2.3 SBOM, retains the vulnerability report, and verifies checksums for
the complete release evidence bundle. A pinned scanner blocks fixed critical findings.
Evidence is uploaded only after the runtime smoke test passes and is named for the exact
source commit. CI explicitly disables repository-local Syft/Grype configuration discovery
and validates the SBOM against the vendored official SPDX 2.3 JSON schema after checking
its trusted upstream SHA-256. Evidence runs in a clean job that never executes the PR's
pytest code. Its validator uses isolated Python from runner-owned temporary storage,
outside the untrusted checkout, preventing module shadowing and workflow-environment
poisoning. The release-evidence job runs only for reviewed pushes to the integration
branch, so a pull request cannot replace the workflow instructions that create its own
evidence. Pull requests still run the complete test and coverage gate against GitHub's
synthetic merge result, and trusted evidence depends explicitly on that job succeeding.
The bundle also contains a strict canonical pre-promotion manifest binding the reviewed
source and candidate local image/config ID to SHA-256 digests of every evidence input. Its
parser rejects duplicate or unknown fields, incomplete evidence, non-digest image
identities, and any claim that the local image has been published. A local Docker image
ID is not an OCI registry manifest digest and cannot be used as a deployment reference.
This groundwork does not yet publish, attest, promote, roll back, or deploy the image.
The release boundary now also defines a canonical non-production publication receipt
that binds the reviewed source, promotion-manifest checksum, immutable registry manifest
reference, and provenance checksum. Deployment validation accepts only that exact
`repository@sha256:...` reference; mutable tags, substituted repositories or digests,
and production-labelled receipts are rejected. The contract performs no registry login,
push, or deployment by itself.
After reviewed code reaches the integration branch, release CI now starts an ephemeral
localhost registry pinned by image digest, publishes the candidate only to that sandbox,
and pulls the resulting manifest back by its registry digest. The evidence bundle includes
the exact registry reference, canonical provenance and publication receipt and verifies
their checksums. The registry is destroyed at the end of the job; no external registry,
credential, production environment or deployment is contacted.
The release API can additionally produce a canonical dry-run promotion plan from that
receipt. It revalidates the manifest, provenance, receipt and trusted repository before
binding the new digest to a distinct immutable current digest for rollback. Exact-match
guards reject target or rollback substitution. The plan is deliberately non-production
and non-executable: it has no registry, orchestrator or deployment adapter.
A provider-neutral execution port now binds that exact plan to the existing durable
external-action lifecycle. It persists intent before execution, requires literal human
approval, uses a stable operation ID for idempotency, and moves restarted or mismatched
outcomes to reconciliation instead of retrying. The only included adapter is a
deterministic in-memory dry run with no registry, orchestrator, credential or network I/O.
`PromotionApprovalReceiptService` persists an immutable operator decision bound to
the exact intent fingerprint and consumes approval atomically before invoking a fixed
no-I/O dry-run adapter. SQLite restarts retain the decision; declined, foreign,
under-scoped, stale, or already-claimed approvals cannot start execution. A crash
after claiming approval requires inspection, never automatic receipt reuse. This
application service can be injected as `receipts=` into the HTTP approval endpoint:
POST then durably records the validated immutable decision, identical retries succeed,
conflicting decisions return 409, and backend failures return sanitized 500 errors.
Without receipt injection the endpoint remains validation-only. Neither mode executes
promotion. Separate authenticated POST-only `execute-dry-run` and `recover-dry-run`
routes accept only `{"schema_version":1}` and require receipt-service injection.
They consume durable approval once or classify recovery without live provider I/O;
replays conflict rather than rerunning the adapter. OpenAPI describes both routes.
`build_promotion_sandbox_app_from_env()` in `application.runtime.promotion_sandbox`
composes these routes with injected server-side operator authentication and lazy,
request-local SQLite repositories. Set `CAREER_AGENT_PROMOTION_SANDBOX=true` and a
durable `CAREER_AGENT_DB_PATH`; network providers and consequential-action flags
must remain disabled. Routing and authentication precede request storage access,
connections close even on response failures, and runtime close blocks new requests.
The composition accepts no deployment adapter and exposes no HTTP intent-creation
route; trusted callers must prepare the exact dry-run intent separately.
The explicitly enabled operator runtime serves a static client at `/sandbox/operator`.
It loads status and the exact approval prompt and records approve/decline only;
it cannot execute or recover operations. The bearer token stays in tab memory,
input changes invalidate the prompt, and response text is rendered without HTML.
Reloading clears credentials. This is a local dry-run tool, not a deployed product.
Run the local operator HTTP service with
`python -m career_agent_ai.application.runtime.promotion_service`.
In addition to the sandbox/database flags above, supply
`CAREER_AGENT_PROMOTION_OPERATOR_ID` and a 32+ character ASCII
`CAREER_AGENT_PROMOTION_BEARER_TOKEN` at runtime. The listener defaults to
`127.0.0.1:8081`; `CAREER_AGENT_PROMOTION_HOST` must remain IPv4 loopback and
`CAREER_AGENT_PROMOTION_PORT` must be 0–65535. The server assigns only the
dry-run scope, compares tokens in constant time, and closes its listener/runtime
on SIGTERM, interruption or startup failure. It exposes the existing promotion
OpenAPI route contract, not a production deployment endpoint.
Public GET-only `/healthz` is storage-independent; `/readyz` opens existing storage
read-only and checks the operation schema, returning sanitized 503 on missing,
empty or corrupt storage without creating a replacement or advancing operations.
Startup initializes the operation schema before binding. Readiness is a read/schema
check, not permission to execute or a guarantee of future write availability.
Readiness requires a non-partial unique operation-ID key and uses an immutable
schema snapshot to avoid SQLite sidecar creation. It fails closed for WAL-mode
databases and SQLite URI paths; use an ordinary rollback-journal database path for
this operator service. It does not ignore live WAL evidence or perform checkpoints.
`promotion_service_openapi_document()` includes these probes; all responses disable caching.
`GET /v1/release/promotions/{operation_id}/status` requires the same authenticated
dry-run scope and returns only schema version, operation ID, operation status and
execution mode, with no-store headers. It validates the intent but never executes,
classifies recovery or changes state. This is operation state only: a prepared
operation may already have a consumed approval receipt, so the snapshot is never
permission to execute or reuse approval.
Its explicit `recover_claim()` classifies both interrupted operation and consumed
receipt as reconciliation-required after revalidating operator/scope/intent binding;
it never calls the adapter and repeated classification is idempotent.
Confirming an ambiguous success requires independently observed target and rollback
references that exactly match the durable plan; planned values are never substituted
for provider observations. Provider exceptions are conservatively ambiguous unless the
adapter explicitly proves they occurred before any external effect, and matching
reconciliation decisions are safe to replay after a lost response.
An operator-facing approval contract now exposes only the immutable dry-run transition,
requires the exact `release:promote:dry-run` authorization scope, and binds each
approve/decline response to the authenticated operator, operation ID, and displayed
plan fingerprint. It remains transport-neutral and cannot execute a promotion itself.
A dependency-free WSGI boundary exposes that contract at
`/v1/release/promotions/{operation_id}/approval` for sandbox integration. Bearer
authentication resolves the operator and scopes server-side; clients cannot supply or
override either identity, and a validated response still performs no deployment.
Every consequential external action uses a stable idempotency key and the existing
`ExternalActionService`. Application submission is scoped to the durable application
identity rather than an individual run, so a later run cannot silently submit the
same tracked application again. Communication and interview responses reuse their
existing crash-safe adapters and persistence contracts.

Human-action events are explicit for application submission, outbound messaging, and
interview/trial-day calendar responses. Before an application can be approved, the
resume/application preparation step must emit an explicit typed artifact. The exact
artifact text and SHA-256 digest are persisted in loop state, shown in the approval
event, copied into the durable external-action intent, and passed unchanged through
the submission-adapter contract. Reusing an operation ID with different artifact
content is rejected, so the provider cannot silently submit something other than what
the human approved.

Interview coordination verifies both candidate ownership and application linkage before
exposing an approval gate. A restart can recover both human-gated states and
already-approved in-progress phases through the durable continuation entry point.
Integration tests use only fake/no-I/O providers and verify that restarts do not create
duplicate applications or messages.

---

## Production Hardening

Production runtime behavior is now explicit rather than implicit. `RuntimeConfig` validates
environment, durable database path, logging level, network-provider permission, and
consequential-action permission from `CAREER_AGENT_*` environment variables. Safe
defaults keep network providers and consequential external actions disabled.

Operational logs can be emitted as compact JSON with a correlation ID propagated
through `correlation_scope()`. Structured fields use bounded JSON-safe serialization
and redact credential-like keys before formatting.

`SQLiteOperationalProbe` provides a read-only view of failed, ambiguous, and stale
external actions plus failed or non-human-gated stuck autonomous loops. A minimal WSGI
adapter exposes public liveness at `GET /healthz` and bearer-authenticated operational
issues at `GET /v1/operational/issues`. `openapi_document()` publishes the matching
OpenAPI 3.1 contract. The operational endpoint is read-only and returns `no-store`
responses.

`build_operational_app_from_env()` is the supported operational-service composition
root. It validates `RuntimeConfig`, requires a 32-to-4096-character ASCII token from
`CAREER_AGENT_OPERATIONAL_BEARER_TOKEN`, opens the configured SQLite database, and
wires the probe, service, and WSGI adapter without exposing the token through runtime
configuration. Database-backed requests open and close an isolated read-only SQLite
connection inside their request thread, while liveness and routing remain
database-independent. A missing database fails inspection instead of creating an empty
replacement that could mask lost storage.
The application provides an idempotent `close()` plus context-manager shutdown control
for deployment lifecycle hooks.

The deployable candidate service also exposes unauthenticated `GET /healthz` liveness
and `GET /readyz` readiness probes. Readiness opens the configured durable SQLite file
in read-only mode and returns a sanitized `503` when storage is missing or unavailable;
it never creates a replacement database. Candidate workflow routes retain bearer
authentication, and all probe responses disable caching.

Coordinated schema changes use `SQLiteMigrationRunner`. Migration versions are
ordered, transactionally applied, and recorded with immutable SHA-256 checksums.
Destructive migrations fail closed unless the caller explicitly opts in. Deployment,
migration, rollback, and security-review guidance lives under `docs/operations/`.

---

## Workflow Engine

Responsible for deterministic workflow lifecycle management.

Supports:

- start
- next step
- execute
- complete
- fail
- pause
- resume
- cancel
- snapshots

---

# Design Principles

- Clean Architecture
- Immutable domain models
- Constructor injection
- Deterministic execution
- Explicit provider boundaries
- No global state
- No circular dependencies
- Bounded autonomous execution
- Human gates before consequential actions
- External communication isolated behind explicit interfaces

---

# Testing

Run the complete suite with:

```bash
python -m pytest
```

CI also runs pytest with coverage enforcement at the project's 90% minimum.

---

# Current Project Status

Implemented foundations:

- Workflow Engine
- Memory Engine
- Agent Brain
- Specialized career agents
- Job search and ranking
- Career Decision Engine
- Isolated autonomous career runs
- Human-gated resume support
- Durable SQLite recovery for paused career runs
- Crash-safe SQLite operation records for consequential external actions
- Explicit human approval and reconciliation gates around external-action adapters
- Provider-neutral, human-gated communication with a no-I/O fake provider
- Restart-safe persistence of communication thread, message, and reply identifiers
- Durable, human-gated interview/trial-day scheduling with conflict detection and exact local-time presentation
- Durable application lifecycle tracking, history, duplicate prevention, and operation linkage
- Durable, versioned SQLite career memory with user/type retrieval
- Workflow state restoration
- Pre-vacancy opportunity scoring
- Bounded pre-vacancy action policy
- Unsent proactive outreach drafting
- Production-capable HTTPS RSS/Atom opportunity-signal collection with provenance and rate/error handling
- Deterministic signal deduplication and employer intelligence aggregation
- Restart-safe end-to-end autonomous career loop with explicit human-action events
- Crash-safe, application-scoped submission idempotency and cross-run duplicate prevention
- Structured production logging with correlation IDs and secret redaction
- Validated runtime configuration with safe external-effect defaults
- Read-only operational observability API with OpenAPI 3.1
- Versioned checksum-verified SQLite migration runner and deployment/security runbooks
- Environment-composed operational WSGI runtime with request-local database lifecycle
- Explicitly enabled candidate sandbox composition with durable loop state, injected
  authentication, request-local SQLite connections and deterministic no-I/O application,
  messaging and calendar adapters; authentication and routing occur before storage is
  opened, and memory-backed storage is rejected
- Owner-scoped candidate recovery endpoint for approved non-human phases after restart
- Continuous integration workflow

The eight-item autonomous-core execution plan is implemented on the integration branch.
This is a backend release candidate, not a completed public web/mobile product.
Release/deployment remains an explicit operator decision. See `SPEC.md` for the
current release contract and `PROJECT_PLAN.md` for the remaining product scope.

---

# Roadmap

The long-term system is intended to evolve toward an autonomous career agent that can:

1. understand a candidate's requirements and preferences
2. discover suitable public vacancies
3. identify companies showing credible pre-vacancy hiring signals
4. rank opportunities with explainable decisions
5. prepare applications and outreach
6. pause for human approval where required
7. communicate through explicit external-service adapters
8. track applications and responses
9. coordinate interviews and trial days
10. provide the exact employer, location, date, and time when human participation is required

The system should only cross consequential external-action boundaries through explicit, testable interfaces.

---

# License

Private project.

Candidate approval JSON rejects duplicate member names, including escaped equivalent
names, before durable resume; malformed submissions receive HTTP 400.

Authenticated `GET /v1/candidate/runs/{run_id}/status` returns only the durable run
ID, phase, state version, human-wait flag and terminal flag in a versioned, no-store
snapshot. It never advances the loop, acquires an execution lease or returns profile,
provider, error or human-action details. Foreign and missing IDs are indistinguishable.
The projection opens the existing SQLite database read-only and checks the indexed
owner metadata before decoding the private durable payload, so a missing database is
not recreated and malformed foreign state remains indistinguishable from absence.
Use the opaque durable ID returned by start; approval decisions still require the
separate prompt-bound approval endpoint. Status is a snapshot, not permission to act.

Programmatic loop IDs may use the full 200-character durable identifier budget.
Application, message, thread, send and interview child IDs retain the readable legacy
form when it fits; otherwise they use a deterministic SHA-256 run namespace so every
downstream identifier remains within its 200-character contract and is restart-stable.
New run IDs beginning with `run-child:` are rejected at creation. Existing runs
retain their legacy child and operation IDs, including replay after restart.
Long-run creation also rejects a conflicting pre-existing legacy namespace owner.
The ownership check includes retained applications, messages and external-action
records, so deleting a terminal loop snapshot cannot release its child namespace.
