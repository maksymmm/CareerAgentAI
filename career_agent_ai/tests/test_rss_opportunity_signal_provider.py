from __future__ import annotations

from datetime import datetime, timezone

import pytest

from career_agent_ai.application.career.rss_opportunity_signal_provider import (
    CompanyNewsFeed,
    NewsSignalRule,
    RSSOpportunitySignalProvider,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def feed(company: str = "Acme GmbH", url: str = "https://example.com/news.xml"):
    return CompanyNewsFeed(
        company=company,
        feed_url=url,
        rules=RSSOpportunitySignalProvider.DEFAULT_RULES,
    )


def test_rss_provider_emits_source_backed_signal_with_publication_time(monkeypatch):
    raw = b"""<?xml version="1.0"?>
    <rss><channel><item>
      <title>Acme raises Series B funding</title>
      <description>Investment will support expansion.</description>
      <link>https://example.com/news/series-b</link>
      <guid>story-123</guid>
      <pubDate>Fri, 25 Sep 2026 10:30:00 +0000</pubDate>
    </item></channel></rss>"""

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        lambda request, timeout: FakeResponse(raw),
    )
    provider = RSSOpportunitySignalProvider(
        (feed(),),
        min_interval_seconds=0,
        now=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
    )

    signals = provider.collect()

    funding = next(signal for signal in signals if signal.signal_type == "funding")
    assert funding.company == "Acme GmbH"
    assert funding.source == "https://example.com/news/series-b"
    assert funding.observed_at == datetime(2026, 9, 25, 10, 30, tzinfo=timezone.utc)
    assert funding.metadata["feed_url"] == "https://example.com/news.xml"
    assert funding.metadata["external_id"] == "story-123"
    assert "series b" in funding.metadata["matched_keywords"]
    assert provider.last_errors == ()


def test_atom_provider_supports_href_links_and_fetched_observation_time(monkeypatch):
    raw = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Acme launches new product</title>
        <summary>Product update</summary>
        <link href="https://example.com/news/product"/>
        <id>tag:example.com,2026:product</id>
      </entry>
    </feed>"""
    observed = datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        lambda request, timeout: FakeResponse(raw),
    )
    provider = RSSOpportunitySignalProvider(
        (feed(),),
        min_interval_seconds=0,
        now=lambda: observed,
    )

    signals = provider.collect()

    signal = next(item for item in signals if item.signal_type == "new_product")
    assert signal.source == "https://example.com/news/product"
    assert signal.observed_at == observed
    assert signal.metadata["observed_via_fetch_at"] == observed.isoformat()


def test_provider_does_not_fabricate_signal_without_explicit_keyword_match(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Acme publishes annual holiday calendar</title>
      <link>https://example.com/news/calendar</link>
      <guid>calendar-1</guid>
    </item></channel></rss>"""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        lambda request, timeout: FakeResponse(raw),
    )

    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    assert provider.collect() == ()


def test_provider_deduplicates_duplicate_feed_entries(monkeypatch):
    raw = b"""<rss><channel>
      <item><title>Acme launches new product</title>
        <link>https://example.com/news/product</link><guid>same-story</guid></item>
      <item><title>Acme launches new product</title>
        <link>https://example.com/news/product</link><guid>same-story</guid></item>
    </channel></rss>"""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        lambda request, timeout: FakeResponse(raw),
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    signals = provider.collect()

    assert len([item for item in signals if item.signal_type == "new_product"]) == 1


def test_provider_rate_limits_between_configured_sources(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Launches new product</title><guid>x</guid>
    </item></channel></rss>"""

    class Clock:
        def __init__(self) -> None:
            self.value = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.value

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.value += seconds

    clock = Clock()
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        lambda request, timeout: FakeResponse(raw),
    )
    provider = RSSOpportunitySignalProvider(
        (
            feed("Alpha", "https://alpha.example.com/feed.xml"),
            feed("Beta", "https://beta.example.com/feed.xml"),
        ),
        min_interval_seconds=0.5,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    provider.collect()

    assert clock.sleeps == [pytest.approx(0.5)]


def test_provider_is_fail_soft_and_reports_sanitized_source_error(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Beta launches new product</title><guid>beta-1</guid>
    </item></channel></rss>"""

    def fake_urlopen(request, timeout):
        if "alpha.example.com" in request.full_url:
            raise OSError("network unavailable")
        return FakeResponse(raw)

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        fake_urlopen,
    )
    provider = RSSOpportunitySignalProvider(
        (
            feed("Alpha", "https://alpha.example.com/feed.xml"),
            feed("Beta", "https://beta.example.com/feed.xml"),
        ),
        min_interval_seconds=0,
    )

    signals = provider.collect()

    assert len(signals) == 1
    assert signals[0].company == "Beta"
    assert len(provider.last_errors) == 1
    assert provider.last_errors[0].source == "https://alpha.example.com/feed.xml"
    assert "failed to fetch configured news feed" in provider.last_errors[0].error


def test_provider_bounds_response_size(monkeypatch):
    raw = b"x" * 101
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider.urlopen",
        lambda request, timeout: FakeResponse(raw),
    )
    provider = RSSOpportunitySignalProvider(
        (feed(),),
        max_response_bytes=100,
        min_interval_seconds=0,
    )

    assert provider.collect() == ()
    assert "size limit" in provider.last_errors[0].error


@pytest.mark.parametrize(
    "url",
    (
        "http://example.com/feed.xml",
        "https://localhost/feed.xml",
        "https://127.0.0.1/feed.xml",
        "not-a-url",
    ),
)
def test_feed_configuration_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        CompanyNewsFeed(
            company="Acme",
            feed_url=url,
            rules=RSSOpportunitySignalProvider.DEFAULT_RULES,
        )


def test_rule_and_provider_configuration_validation():
    with pytest.raises(ValueError):
        NewsSignalRule("funding", (), 0.8)
    with pytest.raises(ValueError):
        NewsSignalRule("funding", ("funding",), 1.1)
    with pytest.raises(ValueError):
        RSSOpportunitySignalProvider((feed(),), timeout=0)
    with pytest.raises(ValueError):
        RSSOpportunitySignalProvider((feed(),), max_entries_per_feed=0)
