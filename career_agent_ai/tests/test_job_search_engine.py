from career_agent_ai.application.search.job_search_engine import JobSearchEngine


class ProviderA:

    def search(self, query):
        return ["a"]


class ProviderB:

    def search(self, query):
        return ["b"]


def test_search_engine_collects_results():
    engine = JobSearchEngine(
        [
            ProviderA(),
            ProviderB(),
        ]
    )

    result = engine.search("python")

    assert result.query == "python"
    assert result.jobs == ("a", "b")


def test_search_engine_count():
    engine = JobSearchEngine(
        [
            ProviderA(),
        ]
    )

    result = engine.search("python")

    assert result.count == 1