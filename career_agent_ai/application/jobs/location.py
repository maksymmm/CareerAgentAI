from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    country: str
    city: str
    region: str = ""
    remote: bool = False