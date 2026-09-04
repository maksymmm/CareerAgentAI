from career_agent_ai.application.jobs.company import Company
from career_agent_ai.application.jobs.job import Job
from career_agent_ai.application.jobs.location import Location
from career_agent_ai.application.search.search_pagination import SearchPagination


def make_job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        title=f"Job {job_id}",
        company=Company(
            company_id=job_id,
            name=f"Company {job_id}",
        ),
        location=Location(
            country="Germany",
            city="Berlin",
        ),
    )


def test_paginate_first_page():
    jobs = (
        make_job("1"),
        make_job("2"),
        make_job("3"),
        make_job("4"),
        make_job("5"),
    )

    result = SearchPagination().paginate(
        jobs,
        page=1,
        page_size=2,
    )

    assert tuple(job.job_id for job in result) == ("1", "2")


def test_paginate_second_page():
    jobs = (
        make_job("1"),
        make_job("2"),
        make_job("3"),
        make_job("4"),
        make_job("5"),
    )

    result = SearchPagination().paginate(
        jobs,
        page=2,
        page_size=2,
    )

    assert tuple(job.job_id for job in result) == ("3", "4")


def test_paginate_last_partial_page():
    jobs = (
        make_job("1"),
        make_job("2"),
        make_job("3"),
        make_job("4"),
        make_job("5"),
    )

    result = SearchPagination().paginate(
        jobs,
        page=3,
        page_size=2,
    )

    assert tuple(job.job_id for job in result) == ("5",)


def test_paginate_empty_page():
    jobs = (
        make_job("1"),
        make_job("2"),
    )

    result = SearchPagination().paginate(
        jobs,
        page=3,
        page_size=2,
    )

    assert result == ()


def test_paginate_invalid_page():
    try:
        SearchPagination().paginate(
            (),
            page=0,
            page_size=10,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")


def test_paginate_invalid_page_size():
    try:
        SearchPagination().paginate(
            (),
            page=1,
            page_size=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")