# ADR 0002: Provider-evidence-verified message reconciliation

Status: accepted (candidate sandbox). Scope: `communication.send` and
`communication.reply`.

## Context

A message provider may time out after accepting a delivery, or a connection may fail
before any effect. Trusting an operator's `delivered` boolean can either duplicate a
real message or invent a successful delivery.

## Decision

Candidate sandbox communication uses a SQLite-backed, no-network provider. Each
operation ID is durably bound to a canonical fingerprint of the action and complete
message intent. Attempts, provider messages, and successful deliveries are stored in
additive tables. Replaying the same key and intent returns the one recorded delivery;
reusing a key with another intent is rejected.

Reconciliation requires provider evidence:

| Claim | `PRESENT` | `ABSENT_CONFIRMED` | `UNKNOWN` / error |
|---|---|---|---|
| `delivered=true` | Accept only when the exact outbound message matches | Reject | Reject |
| `delivered=false` | Reject | Accept | Reject |

Evidence lookup takes a SQLite writer lock, so it cannot mistake an in-flight effect
for confirmed absence. Existing transactions and lock failures fail closed. Rejection
uses the existing conflict response and leaves operation, message, loop phase, and
state version unchanged.

## Consequences

- A timeout after provider effect can only reconcile as delivered and cannot trigger
  another provider call.
- A connection interruption committed without effect can reconcile as not delivered,
  reopen the same intent, and retry safely.
- The runtime remains a sandbox: there is no network access, provider credential, or
  real recruiter message.
- Legacy/in-memory compositions remain operator-attested unless they explicitly
  require evidence.
- Interview/calendar reconciliation remains the next equivalent hardening boundary.
