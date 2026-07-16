# Engineering Rules

These rules apply to every contribution.

## General

- Production-ready code only.
- No placeholders.
- No TODOs without implementation.
- No duplicated code.
- Follow SOLID principles.
- Follow Clean Architecture.
- Prefer composition over inheritance.
- Strong typing everywhere.
- Every public function must have a docstring.
- Keep functions small and focused.

---

## Testing

Every feature must include tests.

Minimum coverage: 90%.

---

## Security

Never hardcode secrets.

Use environment variables.

Validate every external input.

Sanitize user-generated content.

---

## API

REST first.

Consistent response models.

OpenAPI documentation required.

---

## Database

Use Alembic migrations.

No raw SQL unless justified.

Indexes where necessary.

---

## AI

Models must be abstracted.

Switching between providers should require minimal code changes.

---

## Git

One feature per branch.

One pull request per feature.

No direct commits to main.

---

## Documentation

Every new module must update documentation.

README must stay current.