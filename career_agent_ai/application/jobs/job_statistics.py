from dataclasses import dataclass


@dataclass(frozen=True)
class JobStatistics:
    total_jobs: int

    average_salary: float

    remote_jobs: int