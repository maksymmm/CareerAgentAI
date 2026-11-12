from enum import Enum


class JobApplicationStatus(str, Enum):
    """Lifecycle states supported by the application tracker."""

    SAVED = "saved"

    APPLIED = "applied"

    INTERVIEW = "interview"

    OFFER = "offer"

    REJECTED = "rejected"

    WITHDRAWN = "withdrawn"
