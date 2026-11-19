from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Lock

import pytest

from career_agent_ai.application.career.rss_opportunity_signal_provider import (
    CompanyNewsFeed,
    NewsSignalRule,
    RSSOpportunitySignalProvider,
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Keep provider tests offline while exercising pre-connect DNS validation."""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._resolve_host_addresses",
        lambda host, port: ("93.184.216.34",),
    )


def feed(company: str = "Acme GmbH", url: str = "https://example.com/news.xml"):
    return CompanyNewsFeed(
        company=company,
        feed_url=url,
        rules=RSSOpportunitySignalProvider.DEFAULT_RULES,
    )


def test_rss_provider_emits_source_backed_signal_with_observation_and_publication_times(monkeypatch):
    raw = b"""<?xml version="1.0"?>
    <rss><channel><item>
      <title>Acme raises Series B funding</title>
      <description>Investment will support expansion.</description>
      <link>https://example.com/news/series-b</link>
      <guid>story-123</guid>
      <pubDate>Fri, 25 Sep 2026 10:30:00 +0000</pubDate>
    </item></channel></rss>"""

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
    )
    fetched_at = datetime(2026, 9, 26, tzinfo=timezone.utc)
    provider = RSSOpportunitySignalProvider(
        (feed(),),
        min_interval_seconds=0,
        now=lambda: fetched_at,
    )

    signals = provider.collect()

    funding = next(signal for signal in signals if signal.signal_type == "funding")
    assert funding.company == "Acme GmbH"
    assert funding.source == "https://example.com/news/series-b"
    assert funding.observed_at == fetched_at
    assert funding.metadata["feed_url"] == "https://example.com/news.xml"
    assert funding.metadata["external_id"] == "story-123"
    assert funding.metadata["source_published_at"] == "2026-09-25T10:30:00+00:00"
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
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
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




def test_atom_provider_prefers_alternate_link_over_self_or_enclosure(monkeypatch):
    raw = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Acme launches new product</title>
        <link rel="self" href="https://example.com/feed/entry/1"/>
        <link href="https://example.com/news/article"/>
        <link rel="enclosure" href="https://example.com/media/video.mp4"/>
        <id>article-1</id>
      </entry>
    </feed>"""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    signals = provider.collect()

    signal = next(item for item in signals if item.signal_type == "new_product")
    assert signal.source == "https://example.com/news/article"


def test_provider_records_observation_after_fetch_completes(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Acme launches new product</title><guid>timed-1</guid>
    </item></channel></rss>"""
    times = iter(
        (
            datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 26, 10, 0, 5, tzinfo=timezone.utc),
        )
    )

    def delayed_read(url, addresses, timeout, max_response_bytes):
        next(times)
        return raw

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        delayed_read,
    )
    provider = RSSOpportunitySignalProvider(
        (feed(),),
        min_interval_seconds=0,
        now=lambda: next(times),
    )

    signals = provider.collect()

    assert signals[0].observed_at == datetime(
        2026, 9, 26, 10, 0, 5, tzinfo=timezone.utc
    )
    assert signals[0].metadata["observed_via_fetch_at"] == (
        "2026-09-26T10:00:05+00:00"
    )

def test_provider_does_not_fabricate_signal_without_explicit_keyword_match(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Acme publishes annual holiday calendar</title>
      <link>https://example.com/news/calendar</link>
      <guid>calendar-1</guid>
    </item></channel></rss>"""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
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
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
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
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
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


def test_provider_serializes_concurrent_rate_limit_reservations():
    barrier = Barrier(2)
    recorded: list[float] = []
    recorded_lock = Lock()

    def sleeper(seconds: float) -> None:
        with recorded_lock:
            recorded.append(seconds)

    provider = RSSOpportunitySignalProvider(
        (feed(),),
        min_interval_seconds=0.5,
        monotonic=lambda: 0.0,
        sleeper=sleeper,
    )

    def reserve() -> None:
        barrier.wait(timeout=5)
        provider._rate_limit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)

    assert recorded == [pytest.approx(0.5)]
    assert provider._next_request_at == pytest.approx(1.0)


def test_provider_is_fail_soft_and_reports_sanitized_source_error(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Beta launches new product</title><guid>beta-1</guid>
    </item></channel></rss>"""

    def fake_read_feed(url, addresses, timeout, max_response_bytes):
        if "alpha.example.com" in url:
            raise OSError("network unavailable")
        return raw

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        fake_read_feed,
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
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
    )
    provider = RSSOpportunitySignalProvider(
        (feed(),),
        max_response_bytes=100,
        min_interval_seconds=0,
    )

    assert provider.collect() == ()
    assert "size limit" in provider.last_errors[0].error




def test_provider_falls_back_to_feed_provenance_for_unsafe_entry_link(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Acme launches new product</title>
      <link>javascript:alert(1)</link>
    </item></channel></rss>"""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    signals = provider.collect()

    assert signals[0].source == "https://example.com/news.xml"
    assert len(signals[0].metadata["external_id"]) == 64


def test_provider_rejects_doctype_and_entity_declarations(monkeypatch):
    raw = b"""<!DOCTYPE rss [<!ENTITY xxe "unsafe">]>
    <rss><channel><item><title>&xxe;</title></item></channel></rss>"""
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    assert provider.collect() == ()
    assert "XML declarations" in provider.last_errors[0].error





def test_provider_rejects_utf16_doctype_and_entity_declarations(monkeypatch):
    text = """<?xml version="1.0" encoding="UTF-16"?>
    <!DOCTYPE rss [<!ENTITY xxe "unsafe">]>
    <rss><channel><item><title>&xxe;</title></item></channel></rss>"""
    raw = text.encode("utf-16")
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        lambda url, addresses, timeout, max_response_bytes: raw,
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    assert provider.collect() == ()
    assert "XML declarations" in provider.last_errors[0].error

def test_provider_passes_only_prevalidated_addresses_to_network_reader(monkeypatch):
    raw = b"""<rss><channel><item>
      <title>Acme launches new product</title><guid>one</guid>
    </item></channel></rss>"""
    seen: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._resolve_host_addresses",
        lambda host, port: ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
    )

    def fake_read(url, addresses, timeout, max_response_bytes):
        seen.append(addresses)
        return raw

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        fake_read,
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    signals = provider.collect()

    assert len(signals) == 1
    assert seen == [
        ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")
    ]


def test_provider_rejects_private_dns_target_before_opening_connection(monkeypatch):
    opened = False

    def must_not_open(url, addresses, timeout, max_response_bytes):
        nonlocal opened
        opened = True
        return b"<rss/>"

    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._resolve_host_addresses",
        lambda host, port: ("10.0.0.5",),
    )
    monkeypatch.setattr(
        "career_agent_ai.application.career.rss_opportunity_signal_provider._read_feed",
        must_not_open,
    )
    provider = RSSOpportunitySignalProvider((feed(),), min_interval_seconds=0)

    assert provider.collect() == ()
    assert opened is False
    assert "non-public network address" in provider.last_errors[0].error


def test_feed_configuration_rejects_embedded_credentials():
    with pytest.raises(ValueError, match="credentials"):
        CompanyNewsFeed(
            company="Acme",
            feed_url="https://user:secret@example.com/feed.xml",
            rules=RSSOpportunitySignalProvider.DEFAULT_RULES,
        )

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
    with pytest.raises(TypeError):
        NewsSignalRule(42, ("funding",), 0.8)
    with pytest.raises(TypeError):
        NewsSignalRule("funding", ("funding", 42), 0.8)
    with pytest.raises(ValueError):
        NewsSignalRule("funding", (), 0.8)
    with pytest.raises(ValueError):
        NewsSignalRule("funding", ("funding",), 1.1)
    with pytest.raises(ValueError):
        RSSOpportunitySignalProvider((feed(),), timeout=0)
    with pytest.raises(ValueError):
        RSSOpportunitySignalProvider((feed(),), max_entries_per_feed=0)
