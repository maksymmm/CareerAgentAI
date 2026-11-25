# ADR 0005: Deterministic communication outcomes in the candidate sandbox runtime

Status: accepted. Scope: the no-network candidate sandbox composition only.

## Context

The candidate sandbox runtime already accepts a deterministic outcome plan for
application submission, but its SQLite communication provider was always created
with the default success plan. End-to-end message recovery tests therefore had to
monkeypatch the provider constructor instead of exercising the supported runtime
composition across request-local connections and process restarts.

## Decision

- Add an optional `communication_outcome_plan` to `RuntimeCandidateApp`,
  `build_candidate_sandbox_app_from_env`, and the request-local loop composition.
- Pass the plan unchanged to `SQLiteSandboxCommunicationProvider` for every
  request-local loop, including approval and reconciliation requests after restart.
- Keep the existing application `sandbox_outcome_plan` separate and backward
  compatible.
- Preserve the default provider behavior when no communication plan is supplied.
- Do not add a network provider, credentials, recipient integration, or any
  production side effect. The outcome plan only controls the local SQLite sandbox.

## Consequences

The candidate workflow can deterministically exercise `NO_EFFECT`,
`INTERRUPTED_BEFORE_EFFECT`, and `TIMEOUT_AFTER_EFFECT` through the actual
runtime composition. Regression tests can prove that an ambiguous delivery survives
a process restart, is restored through the authenticated owner-scoped HTTP intent
projection, and is completed only after provider evidence is verified—without a
second delivery or replay.

All tests use synthetic messages and a local SQLite database.
