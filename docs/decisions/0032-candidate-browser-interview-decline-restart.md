# ADR 0032: Candidate browser interview decline across runtime restart

- Status: Proposed
- Date: 2026-11-26

## Context

The candidate workflow exposes an interview approval gate after application and message decisions. Declining the exact version-bound interview snapshot must not invoke the calendar provider, mutate the schedule event, or reopen after runtime and browser restart.

## Decision

Add a Chromium acceptance contract using RuntimeCandidateApp, authenticated loopback WSGI, and the no-network SQLite calendar provider. Approve the application and message through the browser; inspect and decline the interview prompt; verify empty calendar intent/attempt/effect ledgers and an unchanged proposed event; restart runtime and browser against the same SQLite database; verify terminal FAILED state, read-only event browsing, no effect on continue, and foreign-owner isolation.

The test uses synthetic candidate data, .example.test message identities, loopback HTTP, Chromium, and SQLite sandbox providers only. No production credentials, real calendar provider, live event mutation, real application, or outbound message is used.

## Consequences

- Browser decline is covered at the interview approval boundary.
- Runtime restart cannot silently reopen the candidate's interview decision.
- The calendar provider ledger remains empty for the declined event.
