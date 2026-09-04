from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.job_source import JobSource
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.jobs.salary import Salary
from career_agent_ai.application.search.job_provider import JobProvider


class DummyProvider(JobProvider):

    def search(
        self,
        query: str,
    ) -> tuple[Job, ...]:

        return (
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
        )


def test_provider_search():

    provider = DummyProvider()

    result = provider.search("python")

    assert len(result) == 1
    assert result[0].title == "Python Developer"
