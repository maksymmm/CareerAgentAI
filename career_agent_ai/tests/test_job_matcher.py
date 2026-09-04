from career_agent_ai.application.jobs import (
    Company,
    EmploymentType,
    Job,
    JobSource,
    Location,
    Salary,
)
from career_agent_ai.application.jobs.job_matcher import JobMatcher


def make_job(title: str, company: str = "OpenAI") -> Job:
    return Job.create(
        title=title,
        company=Company(
            company_id="1",
            name=company,
        ),
        location=Location(
            country="Germany",
            city="Berlin",
        ),
        salary=Salary(
            minimum=50000,
            maximum=70000,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )


def test_match_title():
    matcher = JobMatcher()

    result = matcher.match(
        "python",
        (
            make_job("Python Developer"),
            make_job("Java Developer"),
        ),
    )

    assert len(result) == 1
    assert result[0].job.title == "Python Developer"


def test_match_company():
    matcher = JobMatcher()

    result = matcher.match(
        "google",
        (
            make_job("Python", "Google"),
            make_job("Python", "OpenAI"),
        ),
    )

    assert len(result) == 1
    assert result[0].job.company.name == "Google"


def test_match_sorted():
    matcher = JobMatcher()

    result = matcher.match(
        "python",
        (
            make_job("Java", "Python Inc"),
            make_job("Python Developer", "OpenAI"),
        ),
    )

    assert result[0].score >= result[1].score