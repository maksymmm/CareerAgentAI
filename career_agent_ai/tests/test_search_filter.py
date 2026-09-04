from career_agent_ai.application.search.search_filter import SearchFilter


def test_filter_defaults():
    f = SearchFilter()

    assert f.keyword == ""
    assert f.location == ""
    assert not f.remote_only


def test_filter_values():
    f = SearchFilter(
        keyword="python",
        location="Berlin",
        remote_only=True,
        company="OpenAI",
    )

    assert f.keyword == "python"
    assert f.location == "Berlin"
    assert f.remote_only
    assert f.company == "OpenAI"