from career_agent_ai.application.jobs.employment_type import EmploymentType
from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_query import JobQuery
from career_agent_ai.application.jobs.job_sort import JobSort


def test_default_query():
    query = JobQuery()

    assert query.page == 1
    assert query.page_size == 20
    assert query.sort == JobSort.RELEVANCE


def test_query_filter():
    query = JobQuery(
        filters=JobFilter(
            keyword="python",
            city="Berlin",
            remote_only=True,
            employment_type=EmploymentType.FULL_TIME,
        ),
        page=2,
        page_size=10,
        sort=JobSort.SALARY,
    )

    assert query.filters.keyword == "python"
    assert query.filters.city == "Berlin"
    assert query.filters.remote_only
    assert query.filters.employment_type == EmploymentType.FULL_TIME

    assert query.page == 2
    assert query.page_size == 10
    assert query.sort == JobSort.SALARY