# ADR 0043: Serialize candidate cancellation against approval

- Status: Proposed
- Date: 2026-11-27

## Context

Candidate cancellation and ordinary approval are both bound to the candidate's observed
run state version. Each transition acquires the durable SQLite execution lease before
persisting a state change. Existing tests cover cancellation at each approval gate and
reject stale versions, but they do not prove the two operations cannot both win when
they begin from the same observed application-approval snapshot.

A double winner could either cancel a run after the candidate approved it or allow an
application submission after cancellation. Both outcomes would violate the candidate's
explicit decision boundary.

## Decision

Add a deterministic concurrent regression using two independent SQLite connections
and two independently composed AutonomousCareerLoop workers.

Both workers first read the same owner-scoped approval snapshot. The test then holds
the winning worker after its execution lease is committed while the competing worker
attempts to claim the same version.

- If cancellation wins, approval must receive a conflict, the run becomes terminal
  cancelled, the application remains saved, and no external-action operation or
  provider attempt is created.
- If approval wins, cancellation must receive a conflict, the application submission
  occurs exactly once, and the run advances to message approval. Creating the local
  message draft is expected; no communication send or calendar provider attempt is allowed.
- The losing worker must not mutate the durable state or execute a provider.
- The final SQLite operation ledger must contain zero operations for cancellation
  wins and exactly one application operation for approval wins.

This test exercises both winner orderings without sleeps or timing assumptions. The
barrier synchronizes the initial snapshots, and events control the lease hand-off.

## Safety

Only synthetic candidate/job data, file-backed SQLite, and local fake adapters are used.
No production credentials, real applications, outbound messages, live providers, or
calendar mutations are involved.
