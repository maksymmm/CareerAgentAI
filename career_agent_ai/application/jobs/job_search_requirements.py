from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.jobs.employment_type import EmploymentType


@dataclass(frozen=True)
class JobSearchRequirements:
    """
    User's employment goal.

    This object describes what kind of job the autonomous
    CareerAgentAI should find for the user.
    """

    keyword: str = ""
    country: str = ""
    city: str = ""
    minimum_salary: int | None = None
    maximum_salary: int | None = None
    currency: str = "EUR"
    employment_type: EmploymentType | None = None
    remote_only: bool = False
    company: str = ""

    def is_defined(self) -> bool:
        return bool(
            self.keyword.strip()
            or self.country.strip()
            or self.city.strip()
            or self.minimum_salary is not None
            or self.maximum_salary is not None
            or self.employment_type is not None
            or self.remote_only
            or self.company.strip()
        )

    def normalized(self) -> "JobSearchRequirements":
        minimum_salary = self.minimum_salary
        maximum_salary = self.maximum_salary

        if (
            minimum_salary is not None
            and maximum_salary is not None
            and minimum_salary > maximum_salary
        ):
            minimum_salary, maximum_salary = (
                maximum_salary,
                minimum_salary,
            )

        currency = (
            self.currency.strip().upper()
            if self.currency
            else "EUR"
        )

        return JobSearchRequirements(
            keyword=self.keyword.strip(),
            country=self.country.strip(),
            city=self.city.strip(),
            minimum_salary=minimum_salary,
            maximum_salary=maximum_salary,
            currency=currency,
            employment_type=self.employment_type,
            remote_only=self.remote_only,
            company=self.company.strip(),
        )
