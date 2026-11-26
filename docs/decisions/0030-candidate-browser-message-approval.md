# ADR 0030: Candidate browser message approval acceptance

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate workflow already separates application approval from recruiter-message
approval. The authenticated HTTP regression proves that a message remains a draft
until its own approval, but that contract must also hold through the actual browser
sandbox rather than only through direct HTTP calls.

## Decision

Keep the existing two-gate workflow and make the browser path an explicit regression
contract:

1. The application approval is bound to the displayed durable run version and
   candidate-visible application snapshot.
2. Completing that gate may create one immutable communication draft, but it must
   not create a provider attempt, intent, delivery, or outbound message.
3. The browser must load the separate message approval prompt and display the exact
   sender, recipient, subject, and body before enabling approval.
4. Only approval of that exact message prompt may invoke the SQLite sandbox
   communication provider.
5. The resulting delivery, attempt, intent fingerprint, and outbound message must
   agree in durable storage.

The acceptance test uses RuntimeCandidateApp, the authenticated WSGI sandbox,
Chromium, synthetic .example.test identities, and the no-network SQLite provider.
It does not call a real messaging service or persist credentials in the browser.

## Consequences

- Browser behavior is covered at the same consequential boundary as the HTTP API.
- A draft cannot be mistaken for a delivered message.
- The provider effect is tied to the separately approved message contents.
- The test protects the no-network sandbox boundary and exactly-once durable delivery.
