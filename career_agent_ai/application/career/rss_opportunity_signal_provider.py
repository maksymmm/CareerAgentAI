"""Production-capable opportunity signals backed by company-owned RSS/Atom feeds."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Callable, Iterable
from urllib.parse import urlparse

from .opportunity_signal import OpportunitySignal
from .signal_deduplicator import OpportunitySignalDeduplicator


@dataclass(frozen=True)
class NewsSignalRule:
    """Deterministic rule mapping explicit feed text to one signal type."""

    signal_type: str
    keywords: tuple[str, ...]
    strength: float

    def __post_init__(self) -> None:
        if not isinstance(self.signal_type, str):
            raise TypeError("signal_type must be text.")
        if not isinstance(self.keywords, tuple) or any(
            not isinstance(keyword, str) for keyword in self.keywords
        ):
            raise TypeError("keywords must be a tuple of text values.")
        signal_type = self.signal_type.strip()
        keywords = tuple(
            keyword.strip().casefold()
            for keyword in self.keywords
            if keyword.strip()
        )
        if not signal_type:
            raise ValueError("signal_type must not be empty.")
        if not keywords:
            raise ValueError("keywords must contain at least one value.")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("strength must be between 0 and 1.")
        object.__setattr__(self, "signal_type", signal_type)
        object.__setattr__(self, "keywords", keywords)


@dataclass(frozen=True)
class CompanyNewsFeed:
    """Trusted configuration for one employer-owned public news feed."""

    company: str
    feed_url: str
    rules: tuple[NewsSignalRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.company, str):
            raise TypeError("company must be text.")
        company = self.company.strip()
        if not company:
            raise ValueError("company must not be empty.")
        feed_url = _validate_feed_url(self.feed_url)
        rules = tuple(self.rules)
        if not rules or any(not isinstance(rule, NewsSignalRule) for rule in rules):
            raise ValueError("rules must contain NewsSignalRule values.")
        object.__setattr__(self, "company", company)
        object.__setattr__(self, "feed_url", feed_url)
        object.__setattr__(self, "rules", rules)


@dataclass(frozen=True)
class SignalSourceError:
    """One sanitized collection failure for a configured external source."""

    source: str
    error: str


class RSSOpportunitySignalProvider:
    """Collect verifiable signals from configured company RSS/Atom feeds.

    The adapter performs real HTTPS GET requests in production, applies only
    explicit deterministic keyword rules, rate-limits requests, bounds response
    size, records source provenance, and skips failed feeds without fabricating
    observations.
    """

    DEFAULT_RULES = (
        NewsSignalRule(
            "funding",
            ("funding", "funding round", "series a", "series b", "investment"),
            0.75,
        ),
        NewsSignalRule(
            "leadership_hire",
            ("appointed", "appoints", "joins as", "chief officer", "vice president"),
            0.70,
        ),
        NewsSignalRule(
            "team_growth",
            ("expands team", "team expansion", "hiring", "new office", "expansion"),
            0.60,
        ),
        NewsSignalRule(
            "new_product",
            ("launches", "launched", "new product", "introduces"),
            0.55,
        ),
    )

    def __init__(
        self,
        feeds: Iterable[CompanyNewsFeed],
        *,
        timeout: float = 15.0,
        min_interval_seconds: float = 0.25,
        max_response_bytes: int = 2_000_000,
        max_entries_per_feed: int = 100,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        resolver: Callable[[str, int], tuple[str, ...]] | None = None,
        deduplicator: OpportunitySignalDeduplicator | None = None,
    ) -> None:
        configured = tuple(feeds)
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0.")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative.")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive.")
        if max_entries_per_feed < 1:
            raise ValueError("max_entries_per_feed must be positive.")
        self._feeds = configured
        self._timeout = timeout
        self._min_interval = min_interval_seconds
        self._max_response_bytes = max_response_bytes
        self._max_entries = max_entries_per_feed
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._now = now
        self._resolver = resolver or _resolve_host_addresses
        self._deduplicator = deduplicator or OpportunitySignalDeduplicator()
        self._last_request_at: float | None = None
        self._last_errors: tuple[SignalSourceError, ...] = ()

    @property
    def last_errors(self) -> tuple[SignalSourceError, ...]:
        """Return sanitized source failures from the most recent collection."""
        return self._last_errors

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Fetch configured feeds and return only source-backed matched signals."""
        signals: list[OpportunitySignal] = []
        errors: list[SignalSourceError] = []
        for feed in self._feeds:
            try:
                fetched_at = self._utc_now()
                raw = self._fetch(feed.feed_url)
                entries = self._parse_entries(raw)
                signals.extend(
                    self._signals_from_entries(feed, entries, fetched_at)
                )
            except Exception as exc:
                errors.append(
                    SignalSourceError(
                        source=feed.feed_url,
                        error=self._safe_error(exc),
                    )
                )
        self._last_errors = tuple(errors)
        return self._deduplicator.deduplicate(signals)

    def _fetch(self, url: str) -> bytes:
        addresses = self._validated_connection_addresses(url)
        self._rate_limit()
        try:
            body = _read_feed(
                url,
                addresses,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
            )
        except Exception as exc:
            raise RuntimeError("failed to fetch configured news feed") from exc
        finally:
            self._last_request_at = self._monotonic()

        if len(body) > self._max_response_bytes:
            raise RuntimeError("feed response exceeds configured size limit")
        return body

    def _validated_connection_addresses(self, url: str) -> tuple[str, ...]:
        """Resolve once and return only public addresses that may be connected."""
        parsed = urlparse(_validate_feed_url(url))
        host = parsed.hostname
        if host is None:
            raise ValueError("feed_url must contain a host.")
        try:
            port = parsed.port or 443
        except ValueError as exc:
            raise ValueError("feed_url contains an invalid port.") from exc
        try:
            addresses = tuple(self._resolver(host, port))
        except Exception as exc:
            raise RuntimeError("failed to resolve configured feed host") from exc
        if not addresses:
            raise RuntimeError("configured feed host resolved to no addresses")
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as exc:
                raise RuntimeError("configured feed host resolved to an invalid address") from exc
            if not _is_public_address(address):
                raise RuntimeError(
                    "configured feed host resolved to a non-public network address"
                )
        return tuple(dict.fromkeys(addresses))

    def _rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        remaining = self._min_interval - elapsed
        if remaining > 0:
            self._sleeper(remaining)

    def _signals_from_entries(
        self,
        feed: CompanyNewsFeed,
        entries: tuple[dict[str, str], ...],
        fetched_at: datetime,
    ) -> tuple[OpportunitySignal, ...]:
        result: list[OpportunitySignal] = []
        for entry in entries[: self._max_entries]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            text = f"{title} {summary}".casefold()
            if not text.strip():
                continue
            raw_link = entry.get("link", "").strip()
            entry_url = self._provenance_url(raw_link, feed.feed_url)
            published_at = self._entry_time(entry)
            raw_external_id = entry.get("id", "").strip()
            external_id = (
                raw_external_id
                or (raw_link if entry_url == raw_link else "")
                or hashlib.sha256(
                    (
                        text
                        + "|"
                        + (entry.get("published") or entry.get("updated") or "")
                    ).encode("utf-8")
                ).hexdigest()
            )
            observed_at = fetched_at
            for rule in feed.rules:
                matched = tuple(
                    keyword for keyword in rule.keywords if keyword in text
                )
                if not matched:
                    continue
                result.append(
                    OpportunitySignal(
                        company=feed.company,
                        signal_type=rule.signal_type,
                        strength=rule.strength,
                        observed_at=observed_at,
                        source=entry_url,
                        metadata={
                            "provider": "rss",
                            "feed_url": feed.feed_url,
                            "external_id": external_id,
                            "title": title,
                            "matched_keywords": matched,
                            "observed_via_fetch_at": fetched_at.isoformat(),
                            "source_published_at": (
                                None if published_at is None else published_at.isoformat()
                            ),
                        },
                    )
                )
        return tuple(result)

    @staticmethod
    def _parse_entries(raw: bytes) -> tuple[dict[str, str], ...]:
        lowered = raw.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise RuntimeError("feed XML declarations are not allowed")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise RuntimeError("feed returned malformed XML") from exc

        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1].casefold()

        entries: list[dict[str, str]] = []
        for element in root.iter():
            if local_name(element.tag) not in {"item", "entry"}:
                continue
            values: dict[str, str] = {}
            for child in list(element):
                name = local_name(child.tag)
                if name == "link":
                    href = child.attrib.get("href", "").strip()
                    text = "".join(child.itertext()).strip()
                    values["link"] = href or text
                elif name in {
                    "title",
                    "description",
                    "summary",
                    "content",
                    "guid",
                    "id",
                    "pubdate",
                    "published",
                    "updated",
                }:
                    value = unescape(" ".join("".join(child.itertext()).split()))
                    if name in {"description", "content"}:
                        values.setdefault("summary", value)
                    elif name == "guid":
                        values.setdefault("id", value)
                    elif name == "pubdate":
                        values.setdefault("published", value)
                    else:
                        values[name] = value
            entries.append(values)
        return tuple(entries)

    @staticmethod
    def _provenance_url(value: str, fallback: str) -> str:
        """Return a safe absolute HTTP(S) provenance URL or the configured feed URL."""
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
        return fallback

    @staticmethod
    def _entry_time(entry: dict[str, str]) -> datetime | None:
        raw = (entry.get("published") or entry.get("updated") or "").strip()
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                return None
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now() must return a timezone-aware datetime.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _safe_error(error: Exception) -> str:
        text = str(error).strip() or type(error).__name__
        return text[:500]


def _validate_feed_url(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("feed_url must be text.")
    normalized = value.strip()
    if len(normalized) > 2_048:
        raise ValueError("feed_url is too long.")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("feed_url must be an absolute HTTPS URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("feed_url must not contain embedded credentials.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("feed_url contains an invalid port.") from exc
    host = parsed.hostname.casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("feed_url must not target localhost.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not _is_public_address(address):
        raise ValueError("feed_url must not target a non-public IP address.")
    return normalized


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_host_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve a feed host immediately before connecting."""
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(row[4][0]) for row in rows))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to one prevalidated IP while verifying the hostname."""

    def __init__(
        self,
        host: str,
        port: int,
        address: str,
        *,
        timeout: float,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _read_feed(
    url: str,
    addresses: tuple[str, ...],
    *,
    timeout: float,
    max_response_bytes: int,
) -> bytes:
    """GET one feed without redirects or a second DNS lookup."""
    parsed = urlparse(_validate_feed_url(url))
    host = parsed.hostname
    if host is None:
        raise ValueError("feed_url must contain a host.")
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    last_network_error: Exception | None = None
    for address in addresses:
        connection = _PinnedHTTPSConnection(
            host,
            port,
            address,
            timeout=timeout,
        )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": (
                        "application/rss+xml, application/atom+xml, "
                        "application/xml, text/xml"
                    ),
                    "User-Agent": "CareerAgentAI/1.0",
                },
            )
            response = connection.getresponse()
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"feed returned HTTP {response.status}")
            return response.read(max_response_bytes + 1)
        except (OSError, TimeoutError, ConnectionError) as exc:
            last_network_error = exc
        finally:
            connection.close()

    if last_network_error is not None:
        raise RuntimeError("all validated feed addresses failed") from last_network_error
    raise RuntimeError("configured feed host has no connectable public address")
