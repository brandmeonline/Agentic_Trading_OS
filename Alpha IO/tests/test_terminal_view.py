"""Tests for the terminal market view.

The properties that matter: every route is behind auth like the rest of the
dashboard, and no panel fabricates data when its backing source is missing —
it says the source is unavailable instead.
"""

import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.news_feed import (
    NewsCorpus,
    NewsFeedService,
    NewsItem,
    SourceCategory,
    content_id,
    extract_tickers,
    get_news_service,
    set_news_service,
)
from web.app import WebConfig, create_app, make_admin_password_hash

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
PASSWORD = "terminal-test-password"


def item(title, source_name="wire", published=None):
    published = published or NOW
    url = f"https://example.com/{abs(hash((title, source_name))) % 1000000}"
    return NewsItem(
        item_id=content_id(title, url),
        title=title,
        summary="",
        url=url,
        source=source_name,
        category=SourceCategory.WIRE,
        credibility=0.85,
        published=published,
        fetched=published,
        entities=extract_tickers(title),
    )


class TerminalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.app = create_app(WebConfig(
            secret_key="terminal-test-secret",
            admin_password_hash=make_admin_password_hash(PASSWORD),
            user_settings_path=os.path.join(self.tmp, "settings.json"),
        ))
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        set_news_service(None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        set_news_service(None)
        os.environ.pop("ALPHAIO_REPORT_DIR", None)

    def login(self):
        """Log in the way a browser does, CSRF token included."""
        page = self.client.get("/login")
        match = re.search(rb'name="_csrf_token"\s+value="([^"]+)"', page.data)
        self.assertIsNotNone(match, "login page did not render a CSRF token")

        response = self.client.post(
            "/login",
            data={
                "username": "admin",
                "password": PASSWORD,
                "_csrf_token": match.group(1).decode(),
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (301, 302), response.get_data(as_text=True)[:400])

    def attach_corpus(self, corpus):
        set_news_service(NewsFeedService(sources=[], corpus=corpus, clock=lambda: NOW))


class TestAuthentication(TerminalCase):
    def test_every_terminal_route_requires_login(self):
        for route in ("/terminal", "/api/terminal/news", "/api/terminal/candidates",
                      "/api/terminal/watchlist", "/api/terminal/brief"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 302)

    def test_page_renders_once_authenticated(self):
        self.login()
        response = self.client.get("/terminal")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("News tape", body)
        self.assertIn("Uncrowded candidates", body)


class TestNewsTape(TerminalCase):
    def test_reports_unavailable_rather_than_faking_a_tape(self):
        self.login()
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        self.attach_corpus(corpus)
        payload = self.client.get("/api/terminal/news").get_json()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["items"], [])

    def test_never_polled_is_distinguished_from_polled_and_empty(self):
        # These look identical on screen unless the API says which one it is.
        self.login()
        self.attach_corpus(NewsCorpus(window_hours=24, clock=lambda: NOW))

        payload = self.client.get("/api/terminal/news").get_json()
        self.assertFalse(payload["polled"])
        self.assertIn("ALPHAIO_INGEST", payload["reason"])

        get_news_service().poll_count = 1
        payload = self.client.get("/api/terminal/news").get_json()
        self.assertTrue(payload["polled"])
        self.assertIsNone(payload["reason"])

    def test_returns_documents_newest_first(self):
        self.login()
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        corpus.add(item("NVDA older", published=NOW - timedelta(hours=3)))
        corpus.add(item("NVDA newer", published=NOW - timedelta(minutes=5)))
        self.attach_corpus(corpus)

        items = self.client.get("/api/terminal/news").get_json()["items"]
        self.assertEqual([i["title"] for i in items], ["NVDA newer", "NVDA older"])

    def test_limit_is_clamped(self):
        self.login()
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(30):
            corpus.add(item(f"NVDA story {n}"))
        self.attach_corpus(corpus)

        self.assertEqual(len(self.client.get("/api/terminal/news?limit=5").get_json()["items"]), 5)
        # Junk and out-of-range values fall back to the default rather than erroring.
        self.assertEqual(self.client.get("/api/terminal/news?limit=abc").status_code, 200)
        self.assertLessEqual(
            len(self.client.get("/api/terminal/news?limit=99999").get_json()["items"]), 200
        )


class TestCandidates(TerminalCase):
    def test_ranks_the_uncrowded_term_first(self):
        self.login()
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(12):
            corpus.add(item(f"NVDA surges to a record on strong growth {n}"))
        corpus.add(item("AMD quietly wins a design slot"))
        self.attach_corpus(corpus)

        payload = self.client.get("/api/terminal/candidates").get_json()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["candidates"][0]["term"], "AMD")

    def test_empty_corpus_yields_no_candidates(self):
        self.login()
        self.attach_corpus(NewsCorpus(window_hours=24, clock=lambda: NOW))
        payload = self.client.get("/api/terminal/candidates").get_json()
        self.assertEqual(payload["candidates"], [])


class TestWatchlist(TerminalCase):
    def test_uses_priced_symbols_when_no_watchlist_configured(self):
        self.login()
        from web.app import trading_state
        trading_state.prices.update({"BTC/USD": 65000.0, "ETH/USD": 3200.0})
        try:
            self.attach_corpus(NewsCorpus(window_hours=24, clock=lambda: NOW))
            rows = self.client.get("/api/terminal/watchlist").get_json()["symbols"]
            self.assertEqual({row["symbol"] for row in rows}, {"BTC/USD", "ETH/USD"})
            self.assertEqual(rows[0]["price"], 65000.0)
        finally:
            trading_state.prices.clear()

    def test_reports_coverage_for_the_base_symbol(self):
        self.login()
        from web.app import trading_state
        trading_state.prices.update({"NVDA": 900.0})
        try:
            corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
            for n in range(3):
                corpus.add(item(f"NVDA story {n}", source_name=f"wire-{n}"))
            self.attach_corpus(corpus)

            rows = self.client.get("/api/terminal/watchlist").get_json()["symbols"]
            self.assertEqual(rows[0]["mentions"], 3)
            self.assertEqual(rows[0]["sources"], 3)
        finally:
            trading_state.prices.clear()

    def test_empty_when_nothing_is_priced(self):
        self.login()
        self.attach_corpus(NewsCorpus(window_hours=24, clock=lambda: NOW))
        self.assertEqual(self.client.get("/api/terminal/watchlist").get_json()["symbols"], [])


class TestBrief(TerminalCase):
    def test_reports_absence_when_no_brief_has_been_generated(self):
        self.login()
        os.environ["ALPHAIO_REPORT_DIR"] = os.path.join(self.tmp, "reports")
        payload = self.client.get("/api/terminal/brief").get_json()
        self.assertFalse(payload["available"])
        self.assertIn("no briefs", payload["reason"])

    def test_serves_the_most_recent_brief(self):
        self.login()
        directory = os.path.join(self.tmp, "reports")
        os.makedirs(directory)
        for name, body in (("macro-20260827.md", "# older"), ("macro-20260828.md", "# newest")):
            with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
                handle.write(body)
        os.environ["ALPHAIO_REPORT_DIR"] = directory

        payload = self.client.get("/api/terminal/brief").get_json()
        self.assertEqual(payload["name"], "macro-20260828.md")
        self.assertEqual(payload["markdown"], "# newest")


class TestServiceSingleton(unittest.TestCase):
    def tearDown(self):
        set_news_service(None)

    def test_get_news_service_is_stable(self):
        set_news_service(None)
        self.assertIs(get_news_service(), get_news_service())

    def test_set_news_service_replaces_it(self):
        replacement = NewsFeedService(sources=[])
        set_news_service(replacement)
        self.assertIs(get_news_service(), replacement)


if __name__ == "__main__":
    unittest.main()
