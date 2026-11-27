# ADR 0005: Cross-ledger verification for sandbox application submissions

Status: accepted (candidate sandbox). Scope: application.submit.

## Decision

The SQLite sandbox submission provider treats its intent, ordered attempt history, and
submission effect as one durable evidence record. verify_submission() reads all three
under one SQLite writer snapshot.

A completely empty ledger, or a durable intent with a contiguous sequence of known
no-effect attempts and no effect, may confirm absence. A present submission requires
exactly one final effect-producing attempt, a matching intent fingerprint, a recomputed
fingerprint over the effect's job/application/artifact/candidate snapshot fields, the
deterministic fake:<application_id> provider identifier, and a valid non-negative
replay counter. Orphaned or partial rows, gaps, unknown outcomes, an effect missing
after an effect-producing attempt, multiple effects, attempts after an effect, malformed
payloads, and histories above 1,000 attempts fail closed.

Verification is read-only: it does not increment replay counters, append attempts, repair
the ledger, or call a provider. The contract is limited to the permanently no-network
SQLite sandbox and is not evidence about a live employer ATS.

## Consequences

- Corrupted or incomplete provider evidence cannot be interpreted as confirmed absence
  and cannot authorize a candidate submission retry.
- A valid no-effect retry history remains recoverable across process restarts.
- A successful or timeout-after-effect submission remains reconcilable without a second
  provider effect.
- Verification work is bounded to at most 1,001 indexed attempt rows per operation.
- No production credentials, network calls, real applications, or employer systems are
  introduced.
