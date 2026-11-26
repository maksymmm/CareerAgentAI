# ADR 0029: Candidate message no-effect recovery across runtime restart

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate career loop already models a separately approved recruiter/employer
message, but the authenticated candidate start contract did not expose the optional
sender, recipient, subject, and body fields. The sandbox runtime also did not expose
the SQLite communication provider's deterministic outcome plan, so the complete
candidate HTTP path could not prove a definite no-delivery retry after process loss.

## Decision

- Extend the strict candidate start request with optional paired sender/recipient
  identifiers and bounded message subject/body fields. The browser sandbox exposes
  the same optional inputs. The exact message still requires its own version-bound
  candidate approval before delivery.
- Inject a deterministic outcome plan into the SQLite communication provider through
  the sandbox runtime only; the default provider remains local and no-network.
- Add an end-to-end RuntimeCandidateApp/HTTP regression that records NO_EFFECT for
  the first delivery attempt, closes and recreates the runtime on the same SQLite
  database, verifies owner isolation and provider-inert status/prompt reads, then
  requires an explicit authenticated POST /continue before retrying the same
  operation ID and message intent.
- Assert one successful durable delivery, stable intent fingerprint, outbound
  message state, and terminal restart replay without another provider attempt.

## Consequences

The candidate HTTP/UI workflow can now reach its existing message approval gate,
and definite no-effect recovery is exercised across a real runtime restart using
the SQLite sandbox provider. The test does not send a real message or establish
retry semantics for a future external mailbox provider.
