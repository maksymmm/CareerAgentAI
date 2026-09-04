from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_repository import JobRepository
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.search_engine import SearchEngine


class DummyRepository(JobRepository):
    def __init__(self):
        self._jobs = (
            Job.create(
                title="Python Developer",
                company=Company(
                    company_id="1",
                    name="OpenAI",
                ),
                location=Location(
                    country="Germany",
                    city="Berlin",
                ),
                salary=Salary(
                    minimum=70000,
                    maximum=90000,
                ),
                employment_type=EmploymentType.FULL_TIME,
                source=JobSource.INTERNAL,
            ),
            Job.create(
                title="Java Developer",
                company=Company(
                    company_id="2",
                    name="Google",
                ),
                location=Location(
                    country="Germany",
                    city="Munich",
                ),
                salary=Salary(
                    minimum=50000,
                    maximum=70000,
                ),
                employment_type=EmploymentType.FULL_TIME,
                source=JobSource.INTERNAL,
            ),
        )

    def add(self, job):
        raise NotImplementedError

    def get(self, job_id):
        for job in self._jobs:
            if job.job_id == job_id:
                return job
        return None

    def all(self):
        return self._jobs

    def search(self, query):
        return self._jobs

    def clear(self):
        raise NotImplementedError


def test_search_engine_returns_jobs():
    engine = SearchEngine(DummyRepository())

    result = engine.search(
        JobQuery(
            filters=JobFilter(
                keyword="python",
            )
        )
    )

    assert len(result) == 2


def test_search_engine_empty_repository():
    class EmptyRepository(DummyRepository):
        def __init__(self):
            self._jobs = ()

    engine = SearchEngine(EmptyRepository())

    result = engine.search(JobQuery())

    assert result == ()