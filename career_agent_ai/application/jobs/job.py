from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4
from typing import Any


@dataclass(frozen=True)
class Job:
    job_id: str
    title: str
    company: Any
    location: Any

    salary: Any = None
    employment_type: Any = None
    source: Any = None

    user_id: str = ""
    url: str = ""
    description: str = ""

    created_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        title,
        company,
        location,
        salary=None,
        employment_type=None,
        source=None,
        user_id="",
        url="",
        description="",
        job_id: str | None = None,
        created_at: datetime | None = None,
        **kwargs,
    ) -> "Job":
        return cls(
            job_id=job_id if job_id is not None else str(uuid4()),
            title=title,
            company=company,
            location=location,
            salary=salary,
            employment_type=employment_type,
            source=source,
            user_id=user_id,
            url=url,
            description=description,
            created_at=(
                created_at
                if created_at is not None
                else datetime.now(UTC)
            ),
        )
