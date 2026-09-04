from career_agent_ai.application.jobs import (
    Company,
    EmploymentType,
    Job,
    JobSource,
    Location,
    Salary,
)
from career_agent_ai.application.jobs.in_memory_job_repository import (
    InMemoryJobRepository,
)
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery


def make_job(
    title: str,
    company: str = "OpenAI",
    city: str = "Berlin",
    country: str = "Germany",
    employment_type: EmploymentType = EmploymentType.FULL_TIME,
    description: str = "",
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id=title,
            name=company,
        ),
        location=Location(
            country=country,
            city=city,
        ),
        salary=Salary(
            minimum=50000,
            maximum=70000,
        ),
        employment_type=employment_type,
        source=JobSource.INTERNAL,
        description=description,
    )


def test_add_job():
    repo = InMemoryJobRepository()

    job = make_job("Python")

    repo.add(job)

    assert repo.get(job.job_id) == job


def test_all_jobs():
    repo = InMemoryJobRepository()

    repo.add(make_job("Python"))
    repo.add(make_job("Java"))

    assert len(repo.all()) == 2


def test_search_by_keyword():
    repo = InMemoryJobRepository()

    repo.add(make_job("Python Developer"))
    repo.add(make_job("Java Engineer"))

    result = repo.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
            )
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Python Developer"


def test_search_by_country():
    repo = InMemoryJobRepository()

    repo.add(
        make_job(
            "Berlin Job",
            country="Germany",
        )
    )

    repo.add(
        make_job(
            "Paris Job",
            country="France",
        )
    )

    result = repo.search(
        JobQuery(
            filters=JobFilter(
                country="Germany",
            )
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Berlin Job"


def test_search_by_city():
    repo = InMemoryJobRepository()

    repo.add(make_job("Berlin Job", city="Berlin"))
    repo.add(make_job("Munich Job", city="Munich"))

    result = repo.search(
        JobQuery(
            filters=JobFilter(
                city="Berlin",
            )
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Berlin Job"


def test_search_by_company():
    repo = InMemoryJobRepository()

    repo.add(
        make_job(
            "Python",
            company="OpenAI",
        )
    )

    repo.add(
        make_job(
            "Java",
            company="Google",
        )
    )

    result = repo.search(
        JobQuery(
            filters=JobFilter(
                company="OpenAI",
            )
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Python"


def test_search_by_employment_type():
    repo = InMemoryJobRepository()

    repo.add(
        make_job(
            "Full Time",
            employment_type=EmploymentType.FULL_TIME,
        )
    )

    repo.add(
        make_job(
            "Part Time",
            employment_type=EmploymentType.PART_TIME,
        )
    )

    result = repo.search(
        JobQuery(
            filters=JobFilter(
                employment_type=EmploymentType.FULL_TIME,
            )
        )
    )

    assert result.total == 1
    assert result.jobs[0].title == "Full Time"


def test_search_keyword_in_description():
    repo = InMemoryJobRepository()

    repo.add(
        make_job(
            "Developer",
            description="Python backend developer",
        )
    )

    result = repo.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
            )
        )
    )

    assert result.total == 1


def test_clear():
    repo = InMemoryJobRepository()

    repo.add(make_job("Python"))

    repo.clear()

    assert len(repo.all()) == 0
