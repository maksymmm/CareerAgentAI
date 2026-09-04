from __future__ import annotations

from career_agent_ai.application.jobs.job import Job


class JobDeduplicator:

    def deduplicate(
        self,
        jobs: tuple[Job, ...],
    ) -> tuple[Job, ...]:

        seen_ids: set[str] = set()
        seen_keys: set[tuple[str, str, str]] = set()

        result: list[Job] = []

        for job in jobs:

            # Backward compatibility:
            # providers may temporarily return arbitrary values.
            # Only Job objects participate in job-level deduplication.
            if not isinstance(job, Job):
                result.append(job)
                continue

            job_id = job.job_id

            if job_id and job_id in seen_ids:
                continue

            if job_id:
                seen_ids.add(job_id)

            company_name = ""

            if job.company is not None:
                company_name = (
                    getattr(job.company, "name", "") or ""
                ).strip().lower()

            city = ""

            if job.location is not None:
                city = (
                    getattr(job.location, "city", "") or ""
                ).strip().lower()

            key = (
                (job.title or "").strip().lower(),
                company_name,
                city,
            )

            if key in seen_keys:
                continue

            seen_keys.add(key)
            result.append(job)

        return tuple(result)
