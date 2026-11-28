# ADR 0062: Candidate application tracker browser recovery

- Status: Accepted
- Date: 2026-11-28
- Scope: Candidate-facing read-only application tracking in the sandbox browser

## Context

The owner-scoped application projection already validates bounded keyset pages, status
filters, minimal application snapshots, and privacy-preserving lifecycle history.
The authenticated HTTP routes and browser client also validate those contracts. The
remaining production-readiness gap was an end-to-end browser proof that joins the
projection, HTTP adapter, request-local runtime, and durable SQLite data across a
runtime restart.

A storage-only test cannot prove that the browser actually requests the correct
owner-scoped routes, preserves the pagination contract, renders only allowlisted
fields, and restores the same view after the runtime is reconstructed.

## Decision

Add one Chromium acceptance that:

1. Seeds 26 synthetic applications for one candidate and one foreign application.
2. Advances one owned application through saved, applied, and interview states,
   retaining private timeline notes only in the local database.
3. Loads the authenticated browser tracker and verifies the 25-row keyset page plus
   the final one-row page.
4. Filters to the interview status and loads the exact owner-scoped application
   detail and lifecycle history.
5. Closes the first RuntimeCandidateApp and WSGI server, then opens a second runtime
   over the same SQLite file and repeats the filtered detail/history view.
6. Verifies that foreign rows and private timeline notes never enter the browser,
   provider tables are not initialized, and the database bytes remain unchanged.

The browser is allowed only read-only tracker operations. It cannot approve, submit,
withdraw, message, or modify an interview as part of this test.

## Consequences

- The application tracker is verified through its real candidate browser, authenticated
  WSGI boundary, request-local runtime, and file-backed SQLite projection.
- Pagination, status filtering, detail, history, owner isolation, and restart behavior
  are covered by one bounded acceptance.
- The regression uses synthetic identifiers and local SQLite only.
- No production credentials, real applications, employer contact, outbound messages,
  network providers, or live calendar mutations are involved.
- The read-only guarantee is checked byte-for-byte and by asserting that the candidate
  tracker request path does not create sandbox-provider or external-action operation tables.

## Verification

The exact-head GitHub Actions run must pass the complete pytest suite, the pytest-cov
90% gate, and the independent coverage CLI 90% gate. Codex code and security reviews
are required for the exact final commit before merge.
