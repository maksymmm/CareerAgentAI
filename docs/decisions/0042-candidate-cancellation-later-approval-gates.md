# ADR 0042: Verify candidate cancellation at later approval gates

- Status: Proposed
- Date: 2026-11-26

## Context

The durable cancellation contract permits a candidate to stop the remaining run at
application, message, or interview approval. The first Chromium restart acceptance
proves cancellation at the initial application gate, but service-level coverage alone
does not prove that the browser preserves earlier provider effects when cancellation
occurs later in the workflow.

## Decision

Add parameterized Chromium acceptance for the message and interview approval gates
using RuntimeCandidateApp, authenticated loopback WSGI, and the no-network SQLite
sandbox providers.

- At the message gate, the application submission is already committed and the
  recruiter message remains a draft. Cancellation must preserve that submission,
  leave communication and calendar provider ledgers empty, and remain terminal
  after a new runtime/browser session is created.
- At the interview gate, the application submission and message delivery are already
  committed. Cancellation must preserve both effects, leave calendar provider
  ledgers empty, and keep the proposed interview event unchanged after restart.
- A state mutation invalidates the previous browser status snapshot. The cancellation
  control must remain disabled until a fresh authenticated status read binds the
  current approval phase and state version.
- A post-restart continuation must return the same terminal cancelled state without
  creating provider attempts, effects, or external-action operations.

The test snapshots durable provider ledgers before cancellation and compares them
after cancellation, runtime reconstruction, and explicit continuation. This proves
cancellation stops only future orchestration; it does not compensate earlier effects.

## Safety

Only synthetic candidate/job/event data, .example.test message identities,
authenticated loopback HTTP, Chromium, and SQLite sandbox providers are used. No
production credentials, real applications, outbound messages, live providers, or
calendar mutations are involved.
