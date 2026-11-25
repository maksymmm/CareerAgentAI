# ADR 0001: Provider-evidence-verified reconciliation of application submissions

Status: accepted (sandbox composition). Scope: `application.submit` only.

## Context

An application submission can end ambiguously: the provider call times out, the connection
drops, or the provider returns evidence that does not match the approved intent. The workflow
then waits in `application_reconciliation` for a human observation (`submitted` true/false and,
for true, a `provider_submission_id`). Before this change the observation was trusted as given:
a wrong "not submitted" claim reopened the operation (risking a duplicate application) and a
wrong "submitted" claim recorded an invented provider ID as durable success.

## Decision

Reconciliation is verified against provider evidence obtained by idempotency key, never against
the operator's claim alone. A provider opts in by implementing
`verify_submission(operation_id) -> SubmissionEvidence`; the composition requires it
(`ApplicationSubmissionService(..., require_provider_evidence=True)`) and refuses to start with a
provider that lacks it.

Evidence has three states. Absence of a record and evidence of absence are different things:

| Evidence state     | Meaning                                                    |
|--------------------|------------------------------------------------------------|
| `PRESENT`          | Provider holds a submission; all identity fields returned. |
| `ABSENT_CONFIRMED` | Provider's lookup is authoritative and holds no record.    |
| `UNKNOWN`          | Not conclusive: lookup failed, or provider cannot prove absence. |

Rules (every rejection leaves state, version and ledger untouched and releases the lease):

| Claim                         | PRESENT                                   | ABSENT_CONFIRMED | UNKNOWN / lookup error / malformed |
|-------------------------------|-------------------------------------------|------------------|-------------------------------------|
| `submitted=true`, id `X`      | accept only if `X`, job, application, artifact digest and snapshot digest all match | reject | reject |
| `submitted=false`             | reject (would risk a duplicate)           | accept           | reject |

Rejections surface as the existing `409 approval_conflict`; no new status, endpoint or OpenAPI
change. Authentication (401), owner isolation (404) and state-version checks (409) run first and
are unchanged.

### Sandbox simulation

A missing provider-effect row is not sufficient evidence of absence. The sandbox lookup takes
the SQLite writer lock first, then requires a valid durable intent and a non-empty, contiguous
attempt history containing only recognized outcomes. It returns `ABSENT_CONFIRMED` only when
every recorded attempt is explicitly `NO_EFFECT`, `RETRYABLE_NO_EFFECT`, or
`INTERRUPTED_BEFORE_EFFECT`. A missing intent, empty/gapped history, unknown outcome, or any
success/timeout/evidence-mismatch attempt without its effect row returns `UNKNOWN`.

Positive `PRESENT` evidence also requires a complete terminal effect: the last attempt must be
`SUCCESS`, `TIMEOUT_AFTER_EFFECT`, or `EVIDENCE_MISMATCH`; earlier attempts may only prove
no effect. The intent fingerprint, ledger fingerprint, and recomputed canonical fingerprint
over job, application, artifact digest, and candidate snapshot digest must all agree. Digests,
provider submission identity, and replay count are validated before any fields are exposed.
Missing or conflicting positive metadata returns `UNKNOWN`.

The writer lock serializes this snapshot with any in-flight submit, so a pending effect cannot
be read as absent. Failure to take the lock is reported as `UNKNOWN`-equivalent (lookup error,
fail closed). Verification also rejects a connection that already has an open transaction:
reusing its potentially stale read snapshot would not acquire the promised writer lock. Evidence
objects accept only the closed `EvidenceState` enum, and a negative claim requires the explicit
`ABSENT_CONFIRMED` member rather than merely any state other than `PRESENT`.

### Idempotent replay

A prior effect is replayable only when the original durable intent still exists and
matches the request, the complete ledger row recomputes to that intent, and the
attempt history is non-empty, contiguous, recognized, and ends in exactly one
effect-producing outcome. Earlier attempts may only be explicit no-effect or
interrupted-before-effect outcomes. Missing intent is never recreated from an effect
row during replay. Invalid effect metadata, malformed replay counters, exhausted
SQLite integer counters, missing/gapped/unknown attempts, and attempts recorded after
an effect fail closed before replay accounting changes. The replay counter is updated
with a compare-and-swap predicate inside the provider-owned writer transaction.

A new provider attempt is also blocked when an existing intent has missing, malformed,
gapped, unknown, or effect-producing attempt history but no corresponding effect row.
Only a contiguous history of explicit no-effect/interrupted outcomes permits another
attempt. This prevents deletion of a successful effect row from turning a duplicate
submission into a second provider effect.

### Future real-provider adapters

A real adapter MUST return `UNKNOWN` unless its lookup is authoritative for the idempotency
key (read-your-writes by client reference, and past the provider's consistency/idempotency
window). A 404, an empty result or a timeout is `UNKNOWN`, never `ABSENT_CONFIRMED`. Until an
out-of-band attestation channel exists, `UNKNOWN` blocks reconciliation by design: the system
prefers a stuck run to a duplicate application. External actions stay disabled
(`allow_network_providers` / `allow_consequential_actions` must remain false in the sandbox).

## How duplicate submissions are prevented (defence in depth)

1. Approval gate: a human approves the exact artifact and snapshot digest.
2. Intent is persisted before the provider is called (`prepared` -> `in_progress`).
3. A stable operation ID is the provider idempotency key; the ledger has `PRIMARY KEY` on it and
   `UNIQUE(application_id)`, so a second effect is impossible at the SQL level, even under
   concurrent connections (writes serialize on `BEGIN IMMEDIATE`).
4. The first valid provider attempt durably binds the key to its intent fingerprint, including
   attempts that produce no effect. Replaying the same key and intent can retry or return the
   stored result; a reused key with a different intent is rejected before another attempt. The
   fingerprint hashes a canonical JSON tuple, so identifier text cannot collide by containing a
   field delimiter.
5. Recovery of an `in_progress` action reconciles it and never re-invokes the provider.
6. Evidence verification (this ADR) stops an operator claim from reopening an operation whose
   effect exists.
7. Failures are classified conservatively: a provider that fails before taking its writer lock is
   a definitive no-effect; any other unexpected error is ambiguous and requires reconciliation.

## Consequences and limits

* Only application submission is verified. Message (`delivered`) and interview
  (`provider_event_id`) reconciliation still trust the operator's claim.
* Verification and resolution are separate steps. This is safe while the operation is
  `reconciliation_required` (no executor runs) and the loop's execution lease serializes
  resolvers; a real provider could still show a late effect after `ABSENT_CONFIRMED`, which is
  why adapters must only report it past their consistency horizon.
* Services constructed without the flag (legacy tests, the in-memory fake) keep operator-attested
  behaviour. Only the candidate sandbox composition enforces evidence.
* Rejections are generic `409`s; there is no operator-facing audit trail of why.
* The sandbox ledger shares the workflow SQLite file; schema changes are additive only.
