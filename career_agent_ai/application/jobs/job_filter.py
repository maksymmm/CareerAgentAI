from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.jobs.employment_type import EmploymentType


@dataclass(frozen=True)
class JobFilter:
    keyword: str = ""
    country: str = ""
    city: str = ""
    remote_only: bool = False
    employment_type: EmploymentType | None = None
    company: str = ""
    minimum_salary: int | None = None
    maximum_salary: int | None = None
    currency: str = "EUR"

    def __post_init__(self) -> None:
        currency = (
            self.currency.strip().upper()
            if self.currency
            else "EUR"
        )

        object.__setattr__(
            self,
            "currency",
            currency,
        )

        if (
            self.minimum_salary is not None
            and self.minimum_salary < 0
        ):
            raise ValueError(
                "minimum_salary cannot be negative"
            )

        if (
            self.maximum_salary is not None
            and self.maximum_salary < 0
        ):
            raise ValueError(
                "maximum_salary cannot be negative"
            )

        if (
            self.minimum_salary is not None
            and self.maximum_salary is not None
            and self.minimum_salary > self.maximum_salary
        ):
            raise ValueError(
                "minimum_salary cannot exceed maximum_salary"
            )
