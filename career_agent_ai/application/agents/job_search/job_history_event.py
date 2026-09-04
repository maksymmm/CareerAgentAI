from dataclasses import dataclass


@dataclass(frozen=True)
class JobHistoryEvent:
    event_id: str

    user_id: str

    job_id: str

    action: str