from career_agent_ai.application.jobs.in_memory_job_bookmark_repository import (
    InMemoryJobBookmarkRepository,
)
from career_agent_ai.application.jobs.job_bookmark import JobBookmark


def test_add_bookmark():
    repo = InMemoryJobBookmarkRepository()

    repo.add(
        JobBookmark(
            user_id="user",
            job_id="job1",
        )
    )

    assert repo.exists("user", "job1")


def test_duplicate():
    repo = InMemoryJobBookmarkRepository()

    bookmark = JobBookmark(
        user_id="user",
        job_id="job1",
    )

    repo.add(bookmark)
    repo.add(bookmark)

    assert len(repo.list("user")) == 1


def test_remove():
    repo = InMemoryJobBookmarkRepository()

    repo.add(
        JobBookmark(
            user_id="user",
            job_id="job1",
        )
    )

    repo.remove(
        "user",
        "job1",
    )

    assert not repo.exists(
        "user",
        "job1",
    )


def test_multiple_users():
    repo = InMemoryJobBookmarkRepository()

    repo.add(JobBookmark("u1", "a"))
    repo.add(JobBookmark("u1", "b"))
    repo.add(JobBookmark("u2", "c"))

    assert len(repo.list("u1")) == 2
    assert len(repo.list("u2")) == 1