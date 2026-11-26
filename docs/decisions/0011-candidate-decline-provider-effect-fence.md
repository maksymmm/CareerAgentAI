# ADR 0011: Stop downstream provider effects after candidate decline

- Status: Accepted
- Scope: Candidate human-approval workflow and SQLite sandbox providers

## Context

The candidate workflow has three ordinary human approval gates: application,
recruiter message, and interview acceptance. A decline is terminal for the current
candidate run. The workflow must not execute the provider action associated with the
declined gate or any later action.

Unit-level decision tests are not sufficient to prove this boundary because provider
attempts and effects are persisted by separate SQLite sandbox adapters.

## Decision

Treat a candidate decline as a durable terminal transition to `FAILED`. The runtime
must not invoke the declined provider action or advance to later provider stages.

The production-composition regression drives the authenticated WSGI approval route
through each of the three gates. It uses the real SQLite sandbox application,
communication, and calendar providers and inspects their durable attempt/effect ledgers
after the runtime closes.

## Consequences

- Declining application approval produces no application submission attempt or ledger row.
- Declining message approval may retain the already approved application effect, but
  produces no communication delivery or calendar effect.
- Declining interview approval may retain the earlier application and message effects,
  but produces no calendar attempt or event effect.
- Every decline leaves the candidate run terminal and not waiting for human input.
- All effects are synthetic and SQLite-local; no production credentials, real
  applications, outbound messages, or real calendar mutations are used.
