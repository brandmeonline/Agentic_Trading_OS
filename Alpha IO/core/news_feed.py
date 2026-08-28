"""
News and filing ingestion layer.

The missing half of the terminal: a deterministic, dependency-light retrieval
pipeline that turns public RSS/Atom feeds and SEC EDGAR filings into normalized
records, and maintains a rolling corpus that measures how crowded a topic is.

Design constraints (see docs/ULTRA_PLAN.md):
- Standard library only. Retrieval and normalization never call a model.
- Fan-out is bounded by source count and rate limits, not by a worker target.
- Nothing here reaches an execution path. The corpus is read by
  ``AsymmetryIndex`` and ``SignalRouter``; routing decisions stay where they are.
"""

from __future__ import annotations

import hashlib
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

USER_AGENT = "AgenticTradingOS/1.0 (+https://github.com/brandmeonline/Agentic_Trading_OS)"


# =============================================================================
# Errors
# =============================================================================

class FeedError(Exception):
    """Base class for ingestion errors."""


class FeedParseError(FeedError):
    """Raised when a document is not parseable as RSS or Atom."""


class FeedFetchError(FeedError):
    """Raised when a source could not be retrieved after retries."""


# =============================================================================
# Sources
# =============================================================================

class SourceKind(Enum):
    """Document format served by a source."""
    RSS = "rss"
    ATOM = "atom"
    AUTO = "auto"


class SourceCategory(Enum):
    """What part of the funnel a source feeds."""
    WIRE = "wire"
    MACRO = "macro"
    FILING = "filing"
    CRYPTO = "crypto"
    SOCIAL = "social"


@dataclass
class FeedSource:
    """A pollable document source.

    ``credibility`` is keyed to ``NewsProcessor.SOURCE_WEIGHTS`` in
    ``core.nlp_engine`` so that a record's weight survives the hand-off.
    ``min_interval_seconds`` is the source's own politeness floor; the scheduler
    never polls it faster regardless of how often it is asked to.
    """
    name: str
    url: str
    category: SourceCategory = SourceCategory.WIRE
    kind: SourceKind = SourceKind.AUTO
    credibility: float = 0.3
    min_interval_seconds: float = 300.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("FeedSource.name must be non-empty")
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"FeedSource.url must be http(s): {self.url!r}")
        if not 0.0 <= self.credibility <= 1.0:
            raise ValueError("FeedSource.credibility must be in [0, 1]")
        if self.min_interval_seconds < 0:
            raise ValueError("FeedSource.min_interval_seconds must be >= 0")

    @property
    def host(self) -> str:
        return urllib.parse.urlparse(self.url).netloc


# SEC asks that automated clients identify themselves; EDGAR full-text Atom is
# public and needs no key. Wire feeds below are the publishers' own public RSS.
DEFAULT_SOURCES: Tuple[FeedSource, ...] = (
    FeedSource("cnbc-markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
               SourceCategory.WIRE, credibility=0.85, min_interval_seconds=180),
    FeedSource("cnbc-economy", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
               SourceCategory.MACRO, credibility=0.85, min_interval_seconds=300),
    FeedSource("marketwatch-top", "https://feeds.content.dowjones.io/public/rss/mw_topstories",
               SourceCategory.WIRE, credibility=0.8, min_interval_seconds=180),
    FeedSource("federalreserve-press", "https://www.federalreserve.gov/feeds/press_monetary.xml",
               SourceCategory.MACRO, credibility=1.0, min_interval_seconds=600),
    FeedSource("sec-edgar-8k", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K&dateb=&owner=include&count=40&output=atom",
               SourceCategory.FILING, kind=SourceKind.ATOM, credibility=1.0, min_interval_seconds=600),
)


# =============================================================================
# Records
# =============================================================================

_WHITESPACE = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
_TICKER = re.compile(r"\b([A-Z]{2,5})\b")

# Tokens that look like tickers but are not, in headline-cased financial text.
_TICKER_STOPWORDS = frozenset({
    "CEO", "CFO", "COO", "CTO", "IPO", "ETF", "GDP", "CPI", "PPI", "FED", "ECB",
    "BOJ", "SEC", "FOMC", "USA", "EU", "UK", "AI", "US", "NYSE", "AND", "THE",
    "FOR", "NEW", "NOT", "BUT", "ALL", "OUT", "OFF", "WSJ", "CNBC", "API",
})


def _clean(text: Optional[str]) -> str:
    """Strip markup and collapse whitespace."""
    if not text:
        return ""
    return _WHITESPACE.sub(" ", _TAGS.sub(" ", text)).strip()


@dataclass
class NewsItem:
    """One normalized document from any source kind."""
    item_id: str
    title: str
    summary: str
    url: str
    source: str
    category: SourceCategory
    credibility: float
    published: datetime
    fetched: datetime
    entities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "summary": self.summary[:500],
            "url": self.url,
            "source": self.source,
            "category": self.category.value,
            "credibility": self.credibility,
            "published": self.published.isoformat(),
            "fetched": self.fetched.isoformat(),
            "entities": self.entities,
        }

    @property
    def text(self) -> str:
        """Title plus summary, for matching and sentiment."""
        return f"{self.title} {self.summary}".strip()


def content_id(title: str, url: str) -> str:
    """Stable identity for a document.

    Keyed on title and link rather than on the feed's own GUID, because
    publishers reissue GUIDs on edit and mirror the same story across feeds.
    """
    digest = hashlib.sha256()
    digest.update(_clean(title).lower().encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(url.strip().lower().encode("utf-8", "replace"))
    return digest.hexdigest()[:32]


def extract_tickers(text: str, limit: int = 8) -> List[str]:
    """Best-effort ticker extraction from headline-cased text.

    Deliberately conservative: this feeds a *denominator*, so a false positive
    inflates a mention count and suppresses an asymmetry score. Missing a ticker
    is the cheaper error.
    """
    seen: List[str] = []
    for match in _TICKER.finditer(text):
        token = match.group(1)
        if token in _TICKER_STOPWORDS or token in seen:
            continue
        seen.append(token)
        if len(seen) >= limit:
            break
    return seen


# =============================================================================
# Parsing
# =============================================================================

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_timestamp(raw: Optional[str], fallback: datetime) -> datetime:
    """Parse RFC-822 or ISO-8601 into an aware UTC datetime."""
    if not raw:
        return fallback
    raw = raw.strip()

    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    iso = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class FeedParser:
    """RSS 2.0 and Atom parser built on the standard library.

    Tolerant of missing optional fields; strict about documents that are not
    feeds at all, which surface as ``FeedParseError`` rather than an empty list —
    an empty feed and an HTML error page must not look alike.
    """

    def parse(
        self,
        document: str,
        source: FeedSource,
        now: Optional[datetime] = None,
    ) -> List[NewsItem]:
        now = now or datetime.now(timezone.utc)

        try:
            root = ET.fromstring(document)
        except ET.ParseError as exc:
            raise FeedParseError(f"{source.name}: malformed XML ({exc})") from exc

        tag = root.tag.lower()
        if tag.endswith("rss") or root.find("channel") is not None:
            entries = self._parse_rss(root)
        elif tag.endswith("feed"):
            entries = self._parse_atom(root)
        else:
            raise FeedParseError(f"{source.name}: not an RSS or Atom document (root={root.tag!r})")

        items: List[NewsItem] = []
        for title, summary, link, stamp in entries:
            title = _clean(title)
            if not title:
                continue
            summary = _clean(summary)
            published = _parse_timestamp(stamp, now)
            items.append(
                NewsItem(
                    item_id=content_id(title, link),
                    title=title,
                    summary=summary,
                    url=link,
                    source=source.name,
                    category=source.category,
                    credibility=source.credibility,
                    published=published,
                    fetched=now,
                    entities=extract_tickers(f"{title} {summary}"),
                )
            )
        return items

    @staticmethod
    def _parse_rss(root: ET.Element) -> List[Tuple[str, str, str, Optional[str]]]:
        channel = root.find("channel")
        container = channel if channel is not None else root
        out = []
        for item in container.findall("item"):
            out.append((
                item.findtext("title", default=""),
                item.findtext("description", default=""),
                (item.findtext("link", default="") or "").strip(),
                item.findtext("pubDate"),
            ))
        return out

    @staticmethod
    def _parse_atom(root: ET.Element) -> List[Tuple[str, str, str, Optional[str]]]:
        out = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            link = ""
            for candidate in entry.findall(f"{_ATOM_NS}link"):
                href = candidate.get("href", "")
                rel = candidate.get("rel", "alternate")
                if href and rel == "alternate":
                    link = href
                    break
                if href and not link:
                    link = href

            summary = entry.findtext(f"{_ATOM_NS}summary", default="")
            if not summary:
                summary = entry.findtext(f"{_ATOM_NS}content", default="")

            stamp = entry.findtext(f"{_ATOM_NS}updated") or entry.findtext(f"{_ATOM_NS}published")
            out.append((
                entry.findtext(f"{_ATOM_NS}title", default=""),
                summary,
                link.strip(),
                stamp,
            ))
        return out


# =============================================================================
# Fetching
# =============================================================================

@dataclass
class FetchResult:
    """Outcome of one conditional GET."""
    source: FeedSource
    body: Optional[str]
    not_modified: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ConditionalFetcher:
    """HTTP GET with ETag / Last-Modified revalidation and bounded retry.

    Conditional requests matter more here than anywhere else in the codebase:
    a wire feed polled every three minutes is unchanged on the large majority of
    polls, and a ``304`` costs one round trip and no parsing.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self._sleep = sleep_fn
        self._validators: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self._ssl_context = ssl.create_default_context()

    def validators_for(self, url: str) -> Dict[str, str]:
        with self._lock:
            return dict(self._validators.get(url, {}))

    def fetch(self, source: FeedSource) -> FetchResult:
        request = urllib.request.Request(source.url)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "application/atom+xml, application/rss+xml, application/xml;q=0.9, */*;q=0.5")

        for header, value in self.validators_for(source.url).items():
            request.add_header(header, value)

        last_error: Optional[str] = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                    body = response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")
                    self._remember(source.url, response.headers)
                    return FetchResult(source, body)

            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    return FetchResult(source, None, not_modified=True)
                last_error = f"HTTP {exc.code}"
                if exc.code == 429 or exc.code >= 500:
                    self._sleep(self.backoff_base * (2 ** attempt))
                    continue
                break

            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                last_error = str(exc)
                self._sleep(self.backoff_base * (2 ** attempt))

        return FetchResult(source, None, error=last_error or "fetch failed")

    def _remember(self, url: str, headers: Any) -> None:
        validators: Dict[str, str] = {}
        etag = headers.get("ETag")
        if etag:
            validators["If-None-Match"] = etag
        last_modified = headers.get("Last-Modified")
        if last_modified:
            validators["If-Modified-Since"] = last_modified
        if validators:
            with self._lock:
                self._validators[url] = validators


# =============================================================================
# Scheduling
# =============================================================================

class RateLimitedScheduler:
    """Bounded parallel polling that respects each source's own interval.

    The repo's first worker pool. Coverage is a function of how many distinct
    sources exist and how fast each permits polling — adding workers past that
    point buys nothing, so ``max_workers`` is deliberately small.
    """

    def __init__(
        self,
        max_workers: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self.max_workers = max_workers
        self._clock = clock
        self._last_poll: Dict[str, float] = {}
        self._lock = threading.Lock()

    def due(self, sources: Iterable[FeedSource]) -> List[FeedSource]:
        """Sources that are enabled and past their minimum interval."""
        now = self._clock()
        ready = []
        with self._lock:
            for source in sources:
                if not source.enabled:
                    continue
                last = self._last_poll.get(source.name)
                if last is None or (now - last) >= source.min_interval_seconds:
                    ready.append(source)
        return ready

    def mark_polled(self, source: FeedSource) -> None:
        with self._lock:
            self._last_poll[source.name] = self._clock()

    def run(
        self,
        sources: Iterable[FeedSource],
        work: Callable[[FeedSource], FetchResult],
    ) -> List[FetchResult]:
        """Poll every due source in parallel, bounded by ``max_workers``."""
        ready = self.due(sources)
        if not ready:
            return []

        results: List[FetchResult] = []
        workers = min(self.max_workers, len(ready))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="news-feed") as pool:
            futures = {}
            for source in ready:
                self.mark_polled(source)
                futures[pool.submit(work, source)] = source

            for future in as_completed(futures):
                source = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - one bad source must not stop the sweep
                    results.append(FetchResult(source, None, error=f"{type(exc).__name__}: {exc}"))
        return results


# =============================================================================
# Corpus
# =============================================================================

@dataclass
class CrowdingStats:
    """What the corpus knows about how crowded a term is."""
    term: str
    mentions: int
    sources: int
    crowd_sentiment: float
    window_hours: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "mentions": self.mentions,
            "sources": self.sources,
            "crowd_sentiment": round(self.crowd_sentiment, 4),
            "window_hours": self.window_hours,
        }


class NewsCorpus:
    """Rolling, deduplicated window of ingested documents.

    Exists to answer two questions that ``AsymmetryIndex`` currently takes as
    caller-supplied literals: how many outlets are talking about this, and how
    one-sided are they. Everything else here is in service of those two.
    """

    def __init__(
        self,
        window_hours: float = 24.0,
        max_items: int = 20000,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if window_hours <= 0:
            raise ValueError("window_hours must be > 0")
        self.window = timedelta(hours=window_hours)
        self.window_hours = window_hours
        self.max_items = max_items
        self._clock = clock
        self._items: Deque[NewsItem] = deque()
        self._seen: Dict[str, datetime] = {}
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def add(self, item: NewsItem) -> bool:
        """Add one document. Returns False if it was already present."""
        with self._lock:
            if item.item_id in self._seen:
                return False
            self._seen[item.item_id] = item.published
            self._items.append(item)
            if len(self._items) > self.max_items:
                evicted = self._items.popleft()
                self._seen.pop(evicted.item_id, None)
            return True

    def extend(self, items: Iterable[NewsItem]) -> int:
        """Add many. Returns the count actually stored."""
        return sum(1 for item in items if self.add(item))

    def purge(self, now: Optional[datetime] = None) -> int:
        """Drop documents older than the window. Returns the count removed."""
        now = now or self._clock()
        cutoff = now - self.window
        removed = 0
        with self._lock:
            while self._items and self._items[0].published < cutoff:
                evicted = self._items.popleft()
                self._seen.pop(evicted.item_id, None)
                removed += 1
        return removed

    def recent(self, now: Optional[datetime] = None) -> List[NewsItem]:
        """Documents inside the window, oldest first."""
        now = now or self._clock()
        cutoff = now - self.window
        with self._lock:
            return [item for item in self._items if item.published >= cutoff]

    def matches(self, term: str, now: Optional[datetime] = None) -> List[NewsItem]:
        """In-window documents mentioning ``term`` as a whole word or entity."""
        needle = term.strip().lower()
        if not needle:
            return []
        pattern = re.compile(rf"\b{re.escape(needle)}\b")
        out = []
        for item in self.recent(now):
            if any(entity.lower() == needle for entity in item.entities):
                out.append(item)
            elif pattern.search(item.text.lower()):
                out.append(item)
        return out

    def mention_count(self, term: str, now: Optional[datetime] = None) -> int:
        """How many in-window documents mention the term.

        This is the ``news_count`` that ``AsymmetryIndex`` has been guessing.
        """
        return len(self.matches(term, now))

    def mention_velocity(
        self,
        term: str,
        hours: float = 1.0,
        now: Optional[datetime] = None,
    ) -> float:
        """Mentions per hour over the trailing ``hours``.

        A rate rather than a count, so an alert threshold means the same thing
        whatever window a caller asks for. This is what catches a story
        breaking, which a cumulative mention count cannot: 20 mentions spread
        over a day and 20 in the last ten minutes are the same number and very
        different events.
        """
        if hours <= 0:
            raise ValueError("hours must be > 0")
        now = now or self._clock()
        cutoff = now - timedelta(hours=hours)
        recent = [item for item in self.matches(term, now) if item.published >= cutoff]
        return len(recent) / hours

    def crowd_sentiment(
        self,
        term: str,
        now: Optional[datetime] = None,
        analyzer: Optional[Callable[[str], float]] = None,
    ) -> float:
        """Credibility-weighted directional agreement about a term, in [0, 1].

        0.5 means either silence or a genuine split. Values near 0 or 1 mean the
        crowd has already made up its mind, which is what suppresses an
        asymmetry score. ``analyzer`` maps text to a score in [-1, 1]; without
        one, a lexicon fallback is used so the corpus never hard-depends on the
        NLP engine.
        """
        items = self.matches(term, now)
        if not items:
            return 0.5

        score_fn = analyzer or _lexicon_polarity
        weighted, weight_total = 0.0, 0.0
        for item in items:
            weight = max(item.credibility, 0.05)
            weighted += score_fn(item.text) * weight
            weight_total += weight

        if weight_total == 0:
            return 0.5
        return max(0.0, min(1.0, (weighted / weight_total + 1.0) / 2.0))

    def crowding(
        self,
        term: str,
        now: Optional[datetime] = None,
        analyzer: Optional[Callable[[str], float]] = None,
    ) -> CrowdingStats:
        """Both measurements plus source breadth, in one pass-worth of work."""
        items = self.matches(term, now)
        return CrowdingStats(
            term=term,
            mentions=len(items),
            sources=len({item.source for item in items}),
            crowd_sentiment=self.crowd_sentiment(term, now, analyzer),
            window_hours=self.window_hours,
        )

    def stats(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        items = self.recent(now)
        return {
            "items_in_window": len(items),
            "items_stored": len(self),
            "sources": sorted({item.source for item in items}),
            "window_hours": self.window_hours,
            "oldest": items[0].published.isoformat() if items else None,
            "newest": items[-1].published.isoformat() if items else None,
        }


_POSITIVE = frozenset({
    "beat", "beats", "surge", "surges", "rally", "rallies", "gain", "gains", "up",
    "upgrade", "upgraded", "strong", "record", "growth", "profit", "bullish",
    "outperform", "raises", "raised", "expansion", "approval", "wins",
})
_NEGATIVE = frozenset({
    "miss", "misses", "plunge", "plunges", "fall", "falls", "drop", "drops",
    "down", "downgrade", "downgraded", "weak", "loss", "losses", "bearish",
    "underperform", "cuts", "cut", "probe", "lawsuit", "recall", "halt", "warns",
})


#: Matched polarity words needed before the lexicon is half-confident. Without
#: this damping, ``(pos - neg) / (pos + neg)`` returns exactly ±1.0 for a
#: headline containing a single polarity word, so crowd sentiment saturates on
#: almost every real term. Saturated crowd sentiment pins the asymmetry index's
#: rarity term at its floor and crushes the whole score range — see
#: docs/ULTRA_PLAN.md Phase 2.1.
_POLARITY_HALF_EVIDENCE = 2.0


def _lexicon_polarity(text: str) -> float:
    """Dependency-free polarity in [-1, 1]. Fallback when no analyzer is given.

    Direction comes from the lexicon balance; magnitude is damped by how much
    evidence there actually was, so one matched word is a weak opinion and five
    is a strong one.
    """
    tokens = re.findall(r"[a-z']+", text.lower())
    if not tokens:
        return 0.0
    positive = sum(1 for token in tokens if token in _POSITIVE)
    negative = sum(1 for token in tokens if token in _NEGATIVE)
    matched = positive + negative
    if matched == 0 or positive == negative:
        return 0.0

    direction = (positive - negative) / float(matched)
    confidence = matched / (matched + _POLARITY_HALF_EVIDENCE)
    return direction * confidence


# =============================================================================
# Service
# =============================================================================

@dataclass
class IngestStats:
    """Outcome of one sweep across all due sources."""
    polled: int = 0
    fetched: int = 0
    not_modified: int = 0
    failed: int = 0
    parsed: int = 0
    stored: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "polled": self.polled,
            "fetched": self.fetched,
            "not_modified": self.not_modified,
            "failed": self.failed,
            "parsed": self.parsed,
            "stored": self.stored,
            "errors": self.errors[:10],
        }


class NewsFeedService:
    """Composes sources, fetcher, parser and corpus into one pollable unit.

    ``poll_once`` is the whole contract: it is synchronous, returns what it
    stored, and never raises for a single bad source. ``start``/``stop`` wrap it
    in a background loop for callers that want one.
    """

    def __init__(
        self,
        sources: Optional[Iterable[FeedSource]] = None,
        corpus: Optional[NewsCorpus] = None,
        fetcher: Optional[ConditionalFetcher] = None,
        parser: Optional[FeedParser] = None,
        scheduler: Optional[RateLimitedScheduler] = None,
        nlp_engine: Optional[Any] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.sources: List[FeedSource] = list(sources if sources is not None else DEFAULT_SOURCES)
        self.corpus = corpus or NewsCorpus(clock=clock)
        self.fetcher = fetcher or ConditionalFetcher()
        self.parser = parser or FeedParser()
        self.scheduler = scheduler or RateLimitedScheduler()
        self.nlp_engine = nlp_engine
        self._clock = clock
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_stats = IngestStats()
        # An empty corpus means one of two very different things: nobody has
        # polled yet, or we polled and there was nothing. The UI must not
        # present the first as the second.
        self.poll_count = 0
        self.last_poll_at: Optional[datetime] = None

    def add_source(self, source: FeedSource) -> None:
        if any(existing.name == source.name for existing in self.sources):
            raise ValueError(f"duplicate source name: {source.name!r}")
        self.sources.append(source)

    def poll_once(self) -> Tuple[List[NewsItem], IngestStats]:
        """Poll every due source once. Returns newly stored items and stats."""
        stats = IngestStats()
        now = self._clock()

        results = self.scheduler.run(self.sources, self.fetcher.fetch)
        stats.polled = len(results)

        stored: List[NewsItem] = []
        for result in results:
            if result.not_modified:
                stats.not_modified += 1
                continue
            if not result.ok or result.body is None:
                stats.failed += 1
                stats.errors.append(f"{result.source.name}: {result.error}")
                continue

            stats.fetched += 1
            try:
                items = self.parser.parse(result.body, result.source, now=now)
            except FeedParseError as exc:
                stats.failed += 1
                stats.errors.append(str(exc))
                continue

            stats.parsed += len(items)
            for item in items:
                if self.corpus.add(item):
                    stored.append(item)

        stats.stored = len(stored)
        self.poll_count += 1
        self.last_poll_at = now
        self.corpus.purge(now)

        if self.nlp_engine is not None:
            self._hand_off(stored)

        self.last_stats = stats
        return stored, stats

    def _hand_off(self, items: List[NewsItem]) -> None:
        """Feed stored documents to the NLP engine, if one is attached.

        Failures here are contained: ingestion must not stop because sentiment
        scoring did.
        """
        for item in items:
            try:
                self.nlp_engine.process_news(
                    headline=item.title,
                    body=item.summary or None,
                    source=item.source,
                    timestamp=item.published,
                )
            except Exception as exc:  # noqa: BLE001 - downstream must never break ingestion
                self.last_stats.errors.append(f"nlp:{item.source}: {type(exc).__name__}")

    def start(self, interval_seconds: float = 60.0) -> None:
        """Poll in the background until ``stop`` is called."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001 - the loop outlives any single sweep
                    pass
                self._stop.wait(interval_seconds)

        self._thread = threading.Thread(target=loop, name="news-feed-service", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background loop and wait for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stats(self) -> Dict[str, Any]:
        return {
            "sources": len(self.sources),
            "enabled": sum(1 for source in self.sources if source.enabled),
            "running": self._thread is not None and self._thread.is_alive(),
            "poll_count": self.poll_count,
            "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
            "last_sweep": self.last_stats.to_dict(),
            "corpus": self.corpus.stats(),
        }


# =============================================================================
# Process-wide service
# =============================================================================

_global_service: Optional["NewsFeedService"] = None
_global_lock = threading.Lock()


def get_news_service() -> "NewsFeedService":
    """Get or create the process-wide feed service.

    Mirrors ``core.alerts.get_alert_manager``. The web layer and the scheduler
    must read the same corpus, or the terminal shows one thing and the alerts
    fire on another.
    """
    global _global_service
    with _global_lock:
        if _global_service is None:
            _global_service = NewsFeedService()
        return _global_service


def set_news_service(service: Optional["NewsFeedService"]) -> None:
    """Replace the process-wide service. For tests and for explicit wiring."""
    global _global_service
    with _global_lock:
        _global_service = service
