from enum import Enum


class JobSort(str, Enum):
    RELEVANCE = "relevance"

    NEWEST = "newest"

    TITLE = "title"

    COMPANY = "company"

    CITY = "city"

    SALARY = "salary"

    SALARY_HIGH = "salary_high"

    SALARY_LOW = "salary_low"
