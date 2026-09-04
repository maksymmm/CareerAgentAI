from dataclasses import dataclass


@dataclass(frozen=True)
class JobBookmark:
    user_id: str

    job_id: str