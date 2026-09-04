from dataclasses import dataclass


@dataclass(frozen=True)
class JobNote:
    note_id: str

    user_id: str

    job_id: str

    text: str