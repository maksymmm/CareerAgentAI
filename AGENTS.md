# AGENTS.md

CareerAgentAI is an autonomous career-agent project. Treat this file as a map, not as the complete specification.

## Source of truth

Read these before substantial changes:
- `README.md` — current architecture and implemented capabilities.
- `ENGINEERING_RULES.md` — mandatory engineering standards.
- `PROJECT_PLAN.md` — product roadmap.
- `SPEC.md` — product specification as it evolves.
- `docs/exec-plans/active/autonomy.md` — current autonomous implementation queue.

## Mission

Build a production-quality career agent that can:
1. understand candidate goals and constraints;
2. discover suitable jobs and pre-vacancy opportunities;
3. prepare resumes, applications, and employer outreach;
4. track applications and recruiter communication;
5. coordinate interviews and trial days;
6. involve the human only when approval or human participation is required.

The agent must never silently cross consequential external-action boundaries.

## Autonomous work loop

When asked to continue the project autonomously:

1. Read `docs/exec-plans/active/autonomy.md`.
2. Select the highest-priority unfinished item that is not blocked.
3. Inspect the relevant current implementation and tests before editing.
4. Implement one coherent production-ready unit end to end.
5. Add or update tests for success, failure, restart/idempotency, and validation paths where relevant.
6. Run the full required verification command.
7. Fix regressions until verification passes or a genuine blocker remains.
8. Update documentation and the active execution plan.
9. Commit the completed unit on the current feature branch.
10. Continue with the next safe item when the task explicitly authorizes ongoing autonomous work.

Do not spend a turn only describing what should be implemented when the repository and tools allow implementation.

## Branch and Git rules

- Never commit directly to `main`.
- Never merge a pull request without explicit human approval.
- Preserve existing public contracts unless the active task requires a deliberate migration.
- Do not rewrite or amend unrelated history.
- Keep commits focused and descriptive.
- Leave the working tree clean after successful work.

## Verification

For code changes, run:

```bash
python -m pytest \
  --cov=career_agent_ai \
  --cov-report=term-missing \
  --cov-fail-under=90
```

Do not claim success unless the relevant tests actually ran and passed.

## Architecture

- Follow Clean Architecture and SOLID.
- Prefer composition and dependency injection.
- Keep provider/infrastructure details behind explicit interfaces.
- Keep domain/application logic deterministic where practical.
- Avoid global mutable state.
- Use strong typing and public docstrings.
- Avoid duplicate logic and oversized functions.
- Do not use pickle or unsafe deserialization for persisted state.

## Persistence and external actions

- Durable state must be restart-safe.
- External actions must be idempotent before real sending/applying is enabled.
- Use stable operation IDs/idempotency keys for consequential actions.
- Persist intent before performing a consequential external action and persist the outcome after it completes.
- On recovery, reconcile ambiguous in-flight actions instead of blindly repeating them.
- Keep email/application/scheduling integrations behind adapters.
- Tests must use fakes or sandboxes, never real recipients or production credentials.

## Human gates

Require explicit human approval before:
- sending a real application or outreach message;
- accepting, declining, or rescheduling an interview;
- changing production credentials or permissions;
- destructive data migrations;
- merging to `main`;
- any action with material external consequences that cannot be safely reversed.

## Security

- Never hardcode secrets.
- Never print secrets in logs or test output.
- Validate and sanitize external input.
- Use parameterized SQL.
- Minimize network and filesystem permissions.
- Stop and report a blocker when credentials or privileged access are required.

## Codex skill

For autonomous implementation work, use the repository skill:
`.agents/skills/careeragent-autonomous-delivery/SKILL.md`.

Its workflow is authoritative for repeatable implementation cycles; this file remains the high-level project map.
