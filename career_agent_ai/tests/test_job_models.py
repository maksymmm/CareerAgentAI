from dataclasses import FrozenInstanceError

import pytest

from career_agent_ai.application.jobs import (
    Company,
    EmploymentType,
    Job,
    JobSource,
    Location,
    Salary,
)


def test_job_creation():
    job = Job.create(
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
            minimum=60000,
            maximum=80000,
        ),
        employment_type=EmploymentType.FULL_TIME,
        source=JobSource.INTERNAL,
    )

    assert job.title == "Python Developer"
    assert job.company.name == "OpenAI"
    assert job.location.city == "Berlin"


def test_models_are_immutable():
    company = Company(
        company_id="1",
        name="OpenAI",
    )

    with pytest.raises(FrozenInstanceError):
        company.name = "Google"