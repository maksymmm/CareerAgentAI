# Mission 006 — Job Provider Plugin System

## Goal

CareerAgent AI must never depend on one job website.

Every job source must be an independent plugin.

The Agent Brain never knows whether jobs come from:

- LinkedIn
- Indeed
- StepStone
- Xing
- Greenhouse
- Lever
- Workday
- company career pages
- future providers

Everything is a Provider.

---

# Architecture

Create:

career_agent_ai/
    providers/
        base.py
        registry.py
        linkedin.py
        indeed.py
        stepstone.py
        greenhouse.py

---

# Provider Interface

Every provider implements:

class JobProvider

Methods:

search_jobs(query)

get_job(job_id)

apply(job)

health()

provider_name()

---

# Registry

Create ProviderRegistry.

Responsibilities:

- register(provider)

- unregister(provider)

- get_provider(name)

- all_providers()

---

# Search Agent

SearchAgent must never search directly.

Instead:

for provider in registry:

    provider.search_jobs(...)

collect results

remove duplicates

sort by score

return unified list

---

# Future Proof

Adding a new provider should require exactly ONE file.

Nothing else changes.

---

# Tests

Write tests proving:

✔ multiple providers work

✔ providers register dynamically

✔ duplicate jobs removed

✔ provider failure doesn't stop others

✔ registry works

---

No shortcuts.

No TODO.

No placeholder code.

Production quality only.