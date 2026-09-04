from dataclasses import dataclass


@dataclass(frozen=True)
class Salary:
    minimum: int | None = None
    maximum: int | None = None
    currency: str = "EUR"