from enum import Enum


class PreVacancyAction(str, Enum):
    """Allowed actions after evaluating a pre-vacancy opportunity."""

    IGNORE = "ignore"
    MONITOR = "monitor"
    PREPARE_OUTREACH = "prepare_outreach"
