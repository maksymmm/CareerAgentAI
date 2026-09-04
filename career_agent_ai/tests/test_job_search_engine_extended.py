from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.job_search_engine import (
    JobSearchEngine,
)
from career_agent_ai.application.search.job_provider import JobProvider


def make_job(
    title: str,
    company: str,
    city: str,
) -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id=company,
            name=company,
        ),
        location=Location(
            country="Germany",
            city=city,
        ),
        salary=Salary(
            minimum=50000,
            maximum=70000,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


class ProviderA(JobProvider):

    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:
        return (
            make_job(
                "Python Developer",
                "OpenAI",
                "Berlin",
            ),
            make_job(
                "Java Developer",
                "Google",
                "Munich",
            ),
        )


class ProviderB(JobProvider):

    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:
        return (
            make_job(
                "Python Developer",
                "OpenAI",
                "Berlin",
            ),
            make_job(
                "Data Engineer",
                "Microsoft",
                "Hamburg",
            ),
        )


def test_engine_accepts_job_providers():

    engine = JobSearchEngine(
        [
            ProviderA(),
            ProviderB(),
        ]
    )

    result = engine.search("developer")

    assert result.count == 3


def test_engine_deduplicates_provider_results():

    engine = JobSearchEngine(
        [
            ProviderA(),
            ProviderB(),
        ]
    )

    result = engine.search("python")

    assert tuple(
        job.title for job in result.jobs
    ) == (
        "Python Developer",
        "Java Developer",
        "Data Engineer",
    )


def test_engine_preserves_query():

    engine = JobSearchEngine(
        [ProviderA()]
    )

    result = engine.search("python")

    assert result.query == "python"


def test_engine_empty_providers():

    engine = JobSearchEngine([])

    result = engine.search("python")

    assert result.jobs == ()
    assert result.count == 0
