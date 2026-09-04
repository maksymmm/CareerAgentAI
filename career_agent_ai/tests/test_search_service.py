from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_repository import JobRepository
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.search_service import SearchService


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
            Job.create(
                title="Python Engineer",
                company=Company(
                    company_id="3",
                    name="Microsoft",
                ),
                location=Location(
                    country="Germany",
                    city="Hamburg",
                ),
                salary=Salary(
                    minimum=60000,
                    maximum=80000,
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
        return type(
            "Result",
            (),
            {
                "jobs": self._jobs,
                "total": len(self._jobs),
            },
        )()

    def clear(self):
        raise NotImplementedError


def test_search():
    service = SearchService(DummyRepository())

    response = service.search(
        JobQuery(
            filters=JobFilter(keyword="python"),
        )
    )

    assert response.count == 3


def test_search_sorts_salary_high():
    service = SearchService(DummyRepository())

    from career_agent_ai.application.jobs.job_sort import JobSort

    response = service.search(
        JobQuery(
            sort=JobSort.SALARY_HIGH,
        )
    )

    assert tuple(
        job.title for job in response.jobs
    ) == (
        "Python Developer",
        "Python Engineer",
        "Java Developer",
    )


def test_search_paginates():
    service = SearchService(DummyRepository())

    response = service.search(
        JobQuery(
            page=2,
            page_size=2,
        )
    )

    assert response.count == 1
    assert response.page == 2
    assert response.page_size == 2


def test_search_response_empty():
    class EmptyRepository(DummyRepository):
        def __init__(self):
            self._jobs = ()

        def search(self, query):
            return type(
                "Result",
                (),
                {
                    "jobs": (),
                    "total": 0,
                },
            )()

    service = SearchService(EmptyRepository())

    response = service.search(JobQuery())

    assert response.empty
    assert response.count == 0


def test_search_response_page_flags():
    service = SearchService(DummyRepository())

    response = service.search(
        JobQuery(
            page=1,
            page_size=2,
        )
    )

    assert response.has_previous_page is False
    assert response.has_next_page is True


def test_search_response_last_page():
    service = SearchService(DummyRepository())

    response = service.search(
        JobQuery(
            page=2,
            page_size=2,
        )
    )

    assert response.has_previous_page is True
    assert response.has_next_page is False
