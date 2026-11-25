# ADR 0004: Validate communication attempt history before retry

Status: accepted (candidate sandbox). Scope: `communication.send` and
`communication.reply` in `SQLiteSandboxCommunicationProvider`.

## Context

The communication evidence verifier can reject a malformed or incomplete attempt
ledger, but the provider write path must enforce the same boundary before it invokes
the outcome plan. Otherwise a retry could append a new attempt to a gapped, unknown,
or contradictory history and create a delivery that reconciliation cannot safely
attribute to the original operation.

An existing intent row without attempts is also ambiguous: the provider commits the
intent and first attempt in the same transaction, so a persisted intent without its
attempt history is not a valid pre-attempt state. Likewise, attempts or a delivery
without their original intent must not be rebound to a caller-supplied request.

## Decision

Before invoking the sandbox outcome plan, the provider reads the intent, ordered
attempt history, and delivery row under its existing `BEGIN IMMEDIATE` transaction.

- A new intent is allowed only when no attempts or delivery already exist.
- An existing intent requires a non-empty, contiguous attempt history with recognized
  outcomes.
- A new attempt is allowed only when every prior attempt is explicitly
  `NO_EFFECT` or `INTERRUPTED_BEFORE_EFFECT`.
- A persisted delivery requires a terminal `SUCCESS` or `TIMEOUT_AFTER_EFFECT`
  attempt, with only explicit no-effect outcomes before it.
- Missing intent, missing attempts, gaps, unknown outcomes, or an effect-producing
  attempt without its delivery row fail closed before the outcome plan runs.
- A valid explicit no-effect history may continue with the same operation ID and
  original message fingerprint after restart.

## Consequences

Corrupted history cannot be repaired implicitly by a new provider call. The caller
must reconcile or repair the durable evidence through an explicit, audited path.
Valid no-effect retries remain supported and exactly-once delivery behavior is
preserved. Regression tests use only synthetic messages and local SQLite; no network
provider or real recipient is contacted.
