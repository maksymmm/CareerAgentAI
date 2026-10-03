# Codex Automation Prompt

Use the repository instructions in `AGENTS.md` and the skill
`.agents/skills/careeragent-autonomous-delivery/SKILL.md`.

Continue building CareerAgentAI autonomously.

For each run:
1. Refresh the repository and read `docs/exec-plans/active/autonomy.md`.
2. If there is an unfinished, unblocked item, take the highest-priority one.
3. Implement one coherent production-ready unit end to end.
4. Add/update tests and run the full verification command from `AGENTS.md`.
5. Fix regressions until green.
6. Update documentation and the active autonomy plan.
7. Commit the completed unit to the current feature branch.
8. Do not merge to `main`.
9. Do not send real applications, email, recruiter messages, calendar responses, or use production credentials.
10. If the next item requires a human gate, credentials, irreversible external action, or a product decision not covered by the repository specification, stop and report the exact blocker instead of guessing.

If the current highest-priority item is already complete, advance to the next item. Do not spend the run only summarizing status when implementation is possible.
