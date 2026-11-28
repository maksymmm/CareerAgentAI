# ADR 0061: Prove negative evidence waits for concurrent provider writes

- Status: Accepted
- Date: 2026-11-28

## Context

The cross-provider evidence concurrency regression already proves that a verifier on an
independent SQLite connection waits for a provider's TIMEOUT_AFTER_EFFECT transaction
to commit before returning PRESENT. That protects against a false negative while an
effect row is still uncommitted.

The complementary negative path is equally important. A candidate may use
ABSENT_CONFIRMED to authorize a deliberate retry after a provider reports no effect.
If verification can read before the in-flight provider transaction commits its attempt,
it could mistake an unresolved write for confirmed absence and release a retry too early.

## Decision

Extend the application-submission, mailbox, and calendar sandbox evidence tests to cover
both NO_EFFECT and INTERRUPTED_BEFORE_EFFECT outcomes. Each writer is held inside its
SQLite transaction while an independent verifier requests BEGIN IMMEDIATE. The test
proves that verification remains blocked until the writer commits the no-effect attempt,
then returns ABSENT_CONFIRMED.

For every provider/outcome pair, assert one outcome-plan call, one committed attempt,
zero provider effects, and no invocation of the provider plan by verification. The
verifier must not append attempts or mutate provider state.

## Consequences

The local no-network SQLite sandbox contract now covers both directions of evidence
serialization: positive evidence is not exposed before an effect commits, and negative
evidence is not exposed before a no-effect attempt commits. This does not claim
transactional or idempotency guarantees for real ATS, mailbox, or calendar providers.
