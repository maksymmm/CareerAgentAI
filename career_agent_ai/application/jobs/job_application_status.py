from enum import Enum


class JobApplicationStatus(str, Enum):
    SAVED = "saved"

    APPLIED = "applied"

    INTERVIEW = "interview"

    OFFER = "offer"

    REJECTED = "rejected"