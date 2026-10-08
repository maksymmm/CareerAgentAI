---
name: careeragent-autonomous-delivery
description: Implement the next safe CareerAgentAI roadmap unit end to end, including code, tests, verification, documentation, and a focused commit.
---

# CareerAgentAI autonomous delivery

Use this skill when continuing CareerAgentAI implementation with minimal supervision.

## Inputs

Read:
- `AGENTS.md`
- `ENGINEERING_RULES.md`
- `README.md`
- `docs/exec-plans/active/autonomy.md`
- the current implementation and tests relevant to the selected work item

## Workflow

1. Choose the highest-priority unfinished, unblocked item from the active autonomy plan.
2. Define a narrow implementation unit with observable acceptance criteria.
3. Inspect existing contracts and repository patterns before writing code.
4. Implement the complete unit; do not leave placeholders, TODO-only scaffolding, or knowingly dead code.
5. Add tests that prove the acceptance criteria and important failure/recovery behavior.
6. Run the full project verification command from `AGENTS.md`.
7. If verification fails because of the change, diagnose and fix it. Repeat until green.
8. Update `README.md` when architecture or capability changes.
9. Update `docs/exec-plans/active/autonomy.md`:
   - mark completed acceptance criteria;
   - record blockers or follow-up debt;
   - keep the next priority explicit.
10. Commit the coherent unit to the current feature branch.

## Autonomous continuation

After one unit is green and committed, continue to the next priority only when the initiating task authorizes ongoing autonomous work.

Before each new unit:
- refresh repository state;
- ensure no unresolved test failure exists;
- avoid overlapping unfinished changes.

## Safety boundaries

Do not autonomously:
- merge to `main`;
- send real emails, applications, recruiter messages, or calendar responses;
- use production credentials;
- delete user data;
- run destructive migrations;
- lower test coverage requirements;
- bypass human approval gates.

If a roadmap item requires one of those actions, implement the adapter, dry-run path, validation, and tests, then stop at the human gate.

## Completion report

A completed unit should leave:
- production code;
- tests;
- passing verification;
- updated documentation;
- an updated execution plan;
- a focused commit;
- no uncommitted changes.

Report only concrete completed work, actual verification results, and genuine blockers.
