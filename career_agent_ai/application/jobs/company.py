from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    website: str = ""
    industry: str = ""