"""Tests for the news/filing ingestion layer.

Nothing here touches the network: the fetcher is replaced by a stub in every
test that needs one, so the suite is deterministic and runs offline in CI.
"""

import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.news_feed import (
    ConditionalFetcher,
    FeedParseError,
    FeedParser,
    FeedSource,
    FetchResult,
    IngestStats,
    NewsCorpus,
    NewsFeedService,
    NewsItem,
    RateLimitedScheduler,
    SourceCategory,
    content_id,
    extract_tickers,
)

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

RSS_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Wire</title>
    <item>
      <title>NVDA beats on record data center revenue</title>
      <description>&lt;p&gt;Shares surge in after-hours trading.&lt;/p&gt;</description>
      <link>https://example.com/a</link>
      <pubDate>Thu, 28 Aug 2026 11:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Fed holds rates steady</title>
      <description>Policy statement unchanged.</description>
      <link>https://example.com/b</link>
      <pubDate>Thu, 28 Aug 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>EDGAR</title>
  <entry>
    <title>8-K - TSMC capacity expansion</title>
    <link rel="alternate" href="https://example.com/edgar/1"/>
    <summary>Item 7.01 Regulation FD Disclosure.</summary>
    <updated>2026-08-28T11:45:00Z</updated>
  </entry>
</feed>
"""

# Missing link, missing date, and an empty title that must be dropped.
PARTIAL_RSS_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Untitled source with no link or date</title></item>
  <item><title></title><link>https://example.com/empty</link></item>
</channel></rss>
"""


def source(name="wire", **kwargs):
    kwargs.setdefault("url", "https://example.com/feed.xml")
    kwargs.setdefault("credibility", 0.8)
    kwargs.setdefault("min_interval_seconds", 0)
    return FeedSource(name, **kwargs)


def item(title, *, source_name="wire", published=None, summary="", credibility=0.8,
         url=None, entities=None):
    published = published or NOW
    url = url or f"https://example.com/{abs(hash(title)) % 10000}"
    return NewsItem(
        item_id=content_id(title, url),
        title=title,
        summary=summary,
        url=url,
        source=source_name,
        category=SourceCategory.WIRE,
        credibility=credibility,
        published=published,
        fetched=published,
        entities=entities if entities is not None else extract_tickers(f"{title} {summary}"),
    )


class TestFeedSource(unittest.TestCase):
    def test_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            FeedSource("bad", "file:///etc/passwd")

    def test_rejects_out_of_range_credibility(self):
        with self.assertRaises(ValueError):
            FeedSource("bad", "https://example.com/f", credibility=1.5)

    def test_host_is_exposed(self):
        self.assertEqual(source(url="https://feeds.example.com/x").host, "feeds.example.com")


class TestFeedParser(unittest.TestCase):
    def setUp(self):
        self.parser = FeedParser()

    def test_parses_rss_and_strips_markup(self):
        items = self.parser.parse(RSS_DOC, source(), now=NOW)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "NVDA beats on record data center revenue")
        self.assertEqual(items[0].summary, "Shares surge in after-hours trading.")
        self.assertEqual(items[0].published, datetime(2026, 8, 28, 11, 30, tzinfo=timezone.utc))
        self.assertIn("NVDA", items[0].entities)

    def test_parses_atom_with_alternate_link(self):
        items = self.parser.parse(ATOM_DOC, source(), now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://example.com/edgar/1")
        self.assertEqual(items[0].published, datetime(2026, 8, 28, 11, 45, tzinfo=timezone.utc))

    def test_tolerates_missing_optional_fields(self):
        items = self.parser.parse(PARTIAL_RSS_DOC, source(), now=NOW)
        # The untitled entry is dropped; the one with no link or date survives.
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "")
        self.assertEqual(items[0].published, NOW)

    def test_malformed_xml_raises(self):
        with self.assertRaises(FeedParseError):
            self.parser.parse("<rss><channel><item>", source(), now=NOW)

    def test_non_feed_document_raises_rather_than_returning_empty(self):
        # An HTML error page must not be indistinguishable from an empty feed.
        with self.assertRaises(FeedParseError):
            self.parser.parse("<html><body>503</body></html>", source(), now=NOW)


class TestExtractTickers(unittest.TestCase):
    def test_extracts_uppercase_symbols(self):
        self.assertIn("NVDA", extract_tickers("NVDA beats estimates"))

    def test_skips_common_financial_acronyms(self):
        found = extract_tickers("CEO says GDP and CPI support the FED view")
        self.assertEqual(found, [])

    def test_deduplicates_and_limits(self):
        found = extract_tickers("AAPL AAPL MSFT GOOG AMZN META TSLA NFLX ORCL CRM", limit=3)
        self.assertEqual(len(found), 3)
        self.assertEqual(len(set(found)), 3)


class TestNewsCorpus(unittest.TestCase):
    def setUp(self):
        self.corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)

    def test_deduplicates_by_content(self):
        first = item("NVDA beats", url="https://example.com/x")
        duplicate = item("NVDA beats", url="https://example.com/x")
        self.assertTrue(self.corpus.add(first))
        self.assertFalse(self.corpus.add(duplicate))
        self.assertEqual(len(self.corpus), 1)

    def test_repoll_of_unchanged_feed_stores_nothing_new(self):
        parser = FeedParser()
        src = source()
        first = parser.parse(RSS_DOC, src, now=NOW)
        second = parser.parse(RSS_DOC, src, now=NOW + timedelta(minutes=5))
        self.assertEqual(self.corpus.extend(first), 2)
        self.assertEqual(self.corpus.extend(second), 0)

    def test_purge_drops_items_outside_window(self):
        self.corpus.add(item("stale", published=NOW - timedelta(hours=48)))
        self.corpus.add(item("fresh", published=NOW - timedelta(hours=1)))
        self.assertEqual(self.corpus.purge(NOW), 1)
        self.assertEqual(len(self.corpus), 1)

    def test_recent_excludes_items_outside_window(self):
        self.corpus.add(item("stale", published=NOW - timedelta(hours=48)))
        self.corpus.add(item("fresh", published=NOW - timedelta(hours=1)))
        self.assertEqual([i.title for i in self.corpus.recent(NOW)], ["fresh"])

    def test_max_items_evicts_oldest(self):
        corpus = NewsCorpus(window_hours=24, max_items=2, clock=lambda: NOW)
        for n in range(4):
            corpus.add(item(f"headline {n}"))
        self.assertEqual(len(corpus), 2)

    def test_mention_count_matches_whole_words_and_entities(self):
        self.corpus.add(item("NVDA beats on data center revenue"))
        self.corpus.add(item("Analysts raise NVDA targets"))
        self.corpus.add(item("Fed holds rates steady"))
        self.assertEqual(self.corpus.mention_count("NVDA", NOW), 2)
        self.assertEqual(self.corpus.mention_count("AMD", NOW), 0)

    def test_mention_count_does_not_match_substrings(self):
        self.corpus.add(item("BABA earnings preview", entities=[]))
        self.assertEqual(self.corpus.mention_count("BA", NOW), 0)

    def test_crowd_sentiment_is_neutral_when_silent(self):
        self.assertEqual(self.corpus.crowd_sentiment("NOBODY", NOW), 0.5)

    def test_crowd_sentiment_leans_with_the_crowd(self):
        for n in range(4):
            self.corpus.add(item(f"NVDA surges to record high on strong growth {n}"))
        self.assertGreater(self.corpus.crowd_sentiment("NVDA", NOW), 0.75)

        bear = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(4):
            bear.add(item(f"NVDA plunges as analysts downgrade on weak guidance {n}"))
        self.assertLess(bear.crowd_sentiment("NVDA", NOW), 0.25)

    def test_crowd_sentiment_respects_a_supplied_analyzer(self):
        self.corpus.add(item("NVDA does a thing"))
        self.assertAlmostEqual(
            self.corpus.crowd_sentiment("NVDA", NOW, analyzer=lambda _: 1.0), 1.0
        )

    def test_crowding_reports_source_breadth(self):
        self.corpus.add(item("NVDA beats", source_name="wire-a"))
        self.corpus.add(item("NVDA rallies", source_name="wire-b"))
        self.corpus.add(item("NVDA gains", source_name="wire-b"))
        stats = self.corpus.crowding("NVDA", NOW)
        self.assertEqual(stats.mentions, 3)
        self.assertEqual(stats.sources, 2)

    def test_concurrent_writes_do_not_lose_items(self):
        def writer(offset):
            for n in range(50):
                self.corpus.add(item(f"headline {offset}-{n}"))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.corpus), 200)


class TestRateLimitedScheduler(unittest.TestCase):
    def test_rejects_zero_workers(self):
        with self.assertRaises(ValueError):
            RateLimitedScheduler(max_workers=0)

    def test_respects_minimum_interval(self):
        clock = [1000.0]
        scheduler = RateLimitedScheduler(clock=lambda: clock[0])
        src = source(min_interval_seconds=300)

        self.assertEqual(scheduler.due([src]), [src])
        scheduler.mark_polled(src)
        self.assertEqual(scheduler.due([src]), [])

        clock[0] += 299
        self.assertEqual(scheduler.due([src]), [])
        clock[0] += 2
        self.assertEqual(scheduler.due([src]), [src])

    def test_skips_disabled_sources(self):
        scheduler = RateLimitedScheduler()
        self.assertEqual(scheduler.due([source(enabled=False)]), [])

    def test_never_exceeds_worker_bound(self):
        scheduler = RateLimitedScheduler(max_workers=2)
        sources = [source(f"s{n}") for n in range(8)]
        live, peak, lock = [0], [0], threading.Lock()

        def work(src):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            try:
                return FetchResult(src, RSS_DOC)
            finally:
                with lock:
                    live[0] -= 1

        results = scheduler.run(sources, work)
        self.assertEqual(len(results), 8)
        self.assertLessEqual(peak[0], 2)

    def test_one_raising_source_does_not_stop_the_sweep(self):
        scheduler = RateLimitedScheduler()
        sources = [source("good"), source("bad")]

        def work(src):
            if src.name == "bad":
                raise RuntimeError("boom")
            return FetchResult(src, RSS_DOC)

        results = scheduler.run(sources, work)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for r in results if r.ok), 1)
        self.assertEqual(sum(1 for r in results if not r.ok), 1)


class TestConditionalFetcher(unittest.TestCase):
    def test_records_validators_for_conditional_requests(self):
        fetcher = ConditionalFetcher(sleep_fn=lambda _: None)

        class Headers(dict):
            def get_content_charset(self):
                return "utf-8"

        headers = Headers({"ETag": 'W/"abc"', "Last-Modified": "Thu, 28 Aug 2026 11:00:00 GMT"})
        fetcher._remember("https://example.com/feed.xml", headers)

        validators = fetcher.validators_for("https://example.com/feed.xml")
        self.assertEqual(validators["If-None-Match"], 'W/"abc"')
        self.assertIn("If-Modified-Since", validators)


class StubFetcher:
    """Returns canned bodies per source name, counting calls."""

    def __init__(self, bodies, error_for=None):
        self.bodies = bodies
        self.error_for = error_for or set()
        self.calls = 0

    def fetch(self, src):
        self.calls += 1
        if src.name in self.error_for:
            return FetchResult(src, None, error="HTTP 503")
        body = self.bodies.get(src.name)
        if body is None:
            return FetchResult(src, None, not_modified=True)
        return FetchResult(src, body)


class TestNewsFeedService(unittest.TestCase):
    def build(self, bodies, error_for=None, sources=None, nlp_engine=None):
        sources = sources or [source("wire", min_interval_seconds=0)]
        return NewsFeedService(
            sources=sources,
            corpus=NewsCorpus(window_hours=24, clock=lambda: NOW),
            fetcher=StubFetcher(bodies, error_for),
            scheduler=RateLimitedScheduler(max_workers=4),
            nlp_engine=nlp_engine,
            clock=lambda: NOW,
        )

    def test_poll_stores_parsed_items(self):
        service = self.build({"wire": RSS_DOC})
        stored, stats = service.poll_once()
        self.assertEqual(len(stored), 2)
        self.assertEqual(stats.fetched, 1)
        self.assertEqual(stats.parsed, 2)
        self.assertEqual(stats.stored, 2)
        self.assertEqual(stats.failed, 0)

    def test_second_poll_of_same_content_stores_nothing(self):
        service = self.build({"wire": RSS_DOC})
        service.poll_once()
        stored, stats = service.poll_once()
        self.assertEqual(stored, [])
        self.assertEqual(stats.stored, 0)

    def test_not_modified_is_counted_not_failed(self):
        service = self.build({})
        _, stats = service.poll_once()
        self.assertEqual(stats.not_modified, 1)
        self.assertEqual(stats.failed, 0)

    def test_failing_source_is_recorded_and_others_still_ingest(self):
        service = self.build(
            {"wire": RSS_DOC},
            error_for={"broken"},
            sources=[source("wire", min_interval_seconds=0), source("broken", min_interval_seconds=0)],
        )
        stored, stats = service.poll_once()
        self.assertEqual(len(stored), 2)
        self.assertEqual(stats.failed, 1)
        self.assertTrue(any("broken" in error for error in stats.errors))

    def test_unparseable_body_is_a_failure_not_a_crash(self):
        service = self.build({"wire": "<html>503</html>"})
        stored, stats = service.poll_once()
        self.assertEqual(stored, [])
        self.assertEqual(stats.failed, 1)

    def test_duplicate_source_names_are_rejected(self):
        service = self.build({"wire": RSS_DOC})
        with self.assertRaises(ValueError):
            service.add_source(source("wire"))

    def test_nlp_hand_off_receives_each_item(self):
        received = []

        class Engine:
            def process_news(self, headline, body, source, timestamp):
                received.append(headline)

        service = self.build({"wire": RSS_DOC}, nlp_engine=Engine())
        service.poll_once()
        self.assertEqual(len(received), 2)

    def test_failing_nlp_engine_does_not_break_ingestion(self):
        class Engine:
            def process_news(self, **kwargs):
                raise RuntimeError("model down")

        service = self.build({"wire": RSS_DOC}, nlp_engine=Engine())
        stored, _ = service.poll_once()
        self.assertEqual(len(stored), 2)
        self.assertEqual(len(service.corpus), 2)

    def test_stats_shape(self):
        service = self.build({"wire": RSS_DOC})
        service.poll_once()
        stats = service.stats()
        self.assertEqual(stats["sources"], 1)
        self.assertEqual(stats["corpus"]["items_in_window"], 2)


class TestIngestStats(unittest.TestCase):
    def test_errors_are_truncated_in_serialization(self):
        stats = IngestStats(errors=[f"e{n}" for n in range(50)])
        self.assertEqual(len(stats.to_dict()["errors"]), 10)


if __name__ == "__main__":
    unittest.main()
