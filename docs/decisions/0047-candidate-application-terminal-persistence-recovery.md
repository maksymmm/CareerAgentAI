# ADR 0047: Candidate application recovery after terminal persistence failure

- Status: Proposed
- Date: 2026-11-27

## Context

A candidate can approve one exact application artifact and the SQLite sandbox provider
can commit the submission successfully, while the subsequent local transition of the
durable external-action operation from \`in_progress\` to \`succeeded\` fails. The
provider effect then exists even though the candidate workflow cannot safely treat the
operation as complete or retry it.

A provider timeout already exercises an ambiguous provider response. It does not cover
this distinct boundary: the provider returned success, but the local terminal status
write failed after the effect was committed.

## Decision

Add a Chromium acceptance test using \`RuntimeCandidateApp\`, authenticated loopback
WSGI, the file-backed SQLite sandbox submission provider, and two runtime/browser
sessions over the same database.

- Start an owner-scoped candidate run and explicitly approve the exact application
  artifact in the browser.
- Inject a SQLite trigger that rejects only the
  \`application.submit: in_progress -> succeeded\` terminal status write.
- Require the provider's one successful attempt and one durable submission effect to
  remain present while the external-action operation is conservatively classified as
  \`reconciliation_required\`.
- Restart the runtime and browser. Status reads must leave intent, attempt, submission,
  replay count and provider plan-call count unchanged.
- Keep reconciliation controls disabled until the candidate explicitly attests the
  provider outcome and supplies the exact provider submission ID.
- Reconcile through the authenticated candidate UI, then require a complete candidate
  run, an applied candidate application, one provider attempt, one durable effect and
  zero replays.

The CI workflow also runs an independent two-decimal coverage CLI gate after pytest,
even when the test step fails without cancelling the job.

## Safety and consequences

Only synthetic candidate/application data, a local loopback server, Chromium and the
no-network SQLite sandbox provider are used. No production credentials, real
applications, recruiter messages, network provider or live calendar action is used.
The change adds no production adapter, schema migration or new external-action route.
