# Sprint 3 — Multi Agent Runtime

Mission:

Refactor the current Agent Brain so it becomes only an orchestrator.

Create independent agents.

Required agents:

- SearchAgent
- EmployerAgent
- ResumeAgent
- ApplyAgent
- LinkedInAgent
- InterviewAgent

Requirements:

- Every agent has its own class.

- Every agent exposes:

execute(task)

supports(task)

status()

- Brain never performs business logic.

- Brain only dispatches tasks.

- Agents communicate only through EventBus.

- Follow Clean Architecture.

- Full typing.

- Unit tests.

- Production quality.

No placeholders.

Generate every required file.

Update README.