from enum import Enum


class JobSource(str, Enum):
    INTERNAL = "internal"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    STEPSTONE = "stepstone"
    XING = "xing"
    OTHER = "other"