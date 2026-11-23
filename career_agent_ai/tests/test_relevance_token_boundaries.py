from types import SimpleNamespace

import pytest

from career_agent_ai.application.jobs.job_filter import JobFilter
from career_agent_ai.application.jobs.job_relevance_scorer import JobRelevanceScorer


def job(title="", description="", salary=None, city=""):
    return SimpleNamespace(
        title=title,
        description=description,
        salary=salary,
        location=SimpleNamespace(country="Germany", city=city, remote=False),
        employment_type=None,
    )


def test_java_keyword_does_not_match_javascript_title_or_description():
    score = JobRelevanceScorer().score(
        job("JavaScript Engineer", "JavaScript and TypeScript"),
        JobFilter(keyword="Java"),
    )
    assert score.title == 0.0
    assert score.skills == 0.0


def test_python_keyword_does_not_match_pythonista():
    score = JobRelevanceScorer().score(
        job("Pythonista"),
        JobFilter(keyword="Python"),
    )
    assert score.title == 0.0


def test_keyword_matches_a_complete_token_next_to_punctuation():
    score = JobRelevanceScorer().score(
        job("Senior (Python) Developer"),
        JobFilter(keyword="Python"),
    )
    assert score.title == JobRelevanceScorer.TITLE_EXACT + JobRelevanceScorer.TITLE_TERM


@pytest.mark.parametrize(
    ("keyword", "title"),
    [
        ("C++", "Senior C++ Engineer"),
        ("C#", "C# Backend Developer"),
        (".NET", ".NET Platform Engineer"),
        ("Node.js", "Node.js Engineer"),
    ],
)
def test_identifier_punctuation_is_preserved(keyword, title):
    score = JobRelevanceScorer().score(job(title), JobFilter(keyword=keyword))
    assert score.title == JobRelevanceScorer.TITLE_EXACT + JobRelevanceScorer.TITLE_TERM


@pytest.mark.parametrize(
    ("keyword", "title", "description"),
    [
        ("Java", "Java-based Engineer", "Java-based backend services"),
        ("C++", "C++-based Engineer", "C++-based systems"),
        ("Node.js", "Node.js-based Engineer", "Node.js-based services"),
    ],
)
def test_hyphens_separate_complete_technology_identifiers(keyword, title, description):
    score = JobRelevanceScorer().score(
        job(title, description),
        JobFilter(keyword=keyword),
    )
    assert score.title == JobRelevanceScorer.TITLE_EXACT + JobRelevanceScorer.TITLE_TERM
    assert score.skills == JobRelevanceScorer.DESCRIPTION_PHRASE + JobRelevanceScorer.DESCRIPTION_TERM


def test_hyphen_does_not_split_a_longer_identifier():
    score = JobRelevanceScorer().score(
        job("JavaScript-based Engineer", "JavaScript-based systems"),
        JobFilter(keyword="Java"),
    )
    assert score.title == 0.0
    assert score.skills == 0.0


def test_city_matches_at_a_hyphen_boundary():
    score = JobRelevanceScorer().score(
        job(city="New-York"),
        JobFilter(city="New"),
    )
    assert score.location == JobRelevanceScorer.LOCATION_CITY / 2


def test_city_prefix_does_not_count_as_city_match():
    score = JobRelevanceScorer().score(
        job(city="Newcastle"),
        JobFilter(city="New"),
    )
    assert score.location == 0.0


def test_city_name_matches_at_a_token_boundary():
    score = JobRelevanceScorer().score(
        job(city="New York"),
        JobFilter(city="New"),
    )
    assert score.location == JobRelevanceScorer.LOCATION_CITY / 2


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "NaN",
        "Infinity",
        "invalid",
    ],
)
def test_invalid_salary_is_unknown(value):
    score = JobRelevanceScorer().score(
        job(salary=SimpleNamespace(minimum=value, maximum=value)),
        JobFilter(minimum_salary=50000),
    )
    assert score.salary == JobRelevanceScorer.SALARY_UNKNOWN


def test_valid_salary_still_matches():
    score = JobRelevanceScorer().score(
        job(salary=SimpleNamespace(minimum=60000, maximum=80000)),
        JobFilter(minimum_salary=70000),
    )
    assert score.salary == JobRelevanceScorer.SALARY_MINIMUM_MATCH
