# Candidate run-ID cutover

Owner-scoped candidate run IDs are not wire-compatible with workers that persist the
caller-supplied ID directly. A mixed old/new worker pool can execute the same logical
request twice. This release therefore requires a **drain-and-replace** cutover; rolling
deployment is prohibited.

## Required release sequence

1. Stop routing new candidate API requests to every pre-scope worker.
2. Drain in-flight requests and confirm that no pre-scope worker remains able to serve
   `POST /v1/candidate/runs`.
3. Deploy only the new build to the entire candidate API pool.
4. Set `CAREER_AGENT_CANDIDATE_ID_CUTOVER=drain-and-replace-scoped-v2` only after steps 1–3
   are satisfied, then start the new workers.
5. Verify the health gate and replay one sandbox request before restoring traffic.

Existing unscoped runs remain readable through the owner-checked legacy replay path.
Transitional SHA-256 scoped runs also remain replayable after owner and canonical-request
verification. New starts use only the `scoped-v2:` generation namespace, whose keys are
longer than the legacy endpoint's accepted caller-ID range and therefore cannot be
pre-seeded through that endpoint.

## Rollback

After any `scoped-v2:` start has been accepted, rollback to a pre-scope build is
prohibited: draining workers does not make a later raw-ID retry safe. Keep candidate
start traffic disabled and roll forward with a corrected scoped build. Approval and
recovery traffic may remain at the human gate. A pre-scope build may be restored only
before traffic was enabled and with evidence that no scoped start was persisted.
