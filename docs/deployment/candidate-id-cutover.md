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
4. Set `CAREER_AGENT_CANDIDATE_ID_CUTOVER=drain-and-replace-v1` only after steps 1–3
   are satisfied, then start the new workers.
5. Verify the health gate and replay one sandbox request before restoring traffic.

Existing unscoped runs remain readable through the owner-checked legacy replay path.
New starts use only the owner-scoped durable ID.

## Rollback

Drain all new workers before restoring a pre-scope build. Never route candidate start
traffic to both generations at once. If the worker generation cannot be proven, keep
candidate start traffic disabled and leave approval/recovery traffic at the human gate.
