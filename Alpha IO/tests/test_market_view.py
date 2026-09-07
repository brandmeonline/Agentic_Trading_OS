"""Tests for the Tier 1 market view.

The property that matters: a panel with no data says so. Every failure mode
here — no source, a dead batch, a partial response, a missing rate — must
produce an honest "unavailable" or a labelled partial reading, never a
plausible-looking number.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.market_view import (
    CROSS_ASSET_PROXIES,
    DEFAULT_BREADTH_UNIVERSE,
    SECTOR_ETFS,
    AlpacaQuoteSource,
    Breadth,
    FredSource,
    MarketView,
    Quote,
    QuoteSource,
    build_market_view,
    parse_snapshots,
)

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


class StubSource(QuoteSource):
    """Returns a fixed change per symbol; missing symbols are simply absent."""

    staleness = "stub"

    def __init__(self, changes=None, cover=None):
        self.changes = changes or {}
        self.cover = cover
        self.requested = []

    def quotes(self, symbols):
        self.requested.append(list(symbols))
        out = {}
        for symbol in symbols:
            if self.cover is not None and symbol not in self.cover:
                continue
            change = self.changes.get(symbol, 0.0)
            out[symbol] = Quote(symbol, 100.0 * (1 + change / 100.0), prev_close=100.0)
        return out


class TestSymbolSets(unittest.TestCase):
    def test_eleven_sectors(self):
        self.assertEqual(len(SECTOR_ETFS), 11)
        self.assertEqual(len({s for s, _ in SECTOR_ETFS}), 11)

    def test_proxies_are_labelled_as_proxies(self):
        # UUP is not DXY. The note must say so, or the strip lies quietly.
        notes = {symbol: note for symbol, _, note in CROSS_ASSET_PROXIES}
        self.assertIn("proxy", notes["UUP"])
        self.assertIn("proxy", notes["VIXY"])
        self.assertEqual(notes["SPY"], "direct")

    def test_breadth_universe_includes_the_sectors(self):
        for symbol, _ in SECTOR_ETFS:
            self.assertIn(symbol, DEFAULT_BREADTH_UNIVERSE)


class TestParseSnapshots(unittest.TestCase):
    def test_parses_the_bare_shape(self):
        quotes = parse_snapshots({"XLK": {"latestTrade": {"p": 250.0},
                                          "prevDailyBar": {"c": 245.0}}})
        self.assertAlmostEqual(quotes["XLK"].change_pct, 2.0408, places=3)

    def test_parses_the_wrapped_shape(self):
        # Returning nothing for the wrong wrapper would look like a quiet market.
        quotes = parse_snapshots({"snapshots": {"XLF": {"dailyBar": {"c": 40.0},
                                                        "prevDailyBar": {"c": 41.0}}}})
        self.assertIn("XLF", quotes)
        self.assertLess(quotes["XLF"].change_pct, 0)

    def test_falls_back_through_price_fields(self):
        quotes = parse_snapshots({"XLE": {"minuteBar": {"c": 90.0}}})
        self.assertEqual(quotes["XLE"].price, 90.0)
        self.assertIsNone(quotes["XLE"].change_pct)

    def test_skips_entries_with_no_usable_price(self):
        self.assertEqual(parse_snapshots({"XLU": {"latestTrade": {"p": 0}}}), {})
        self.assertEqual(parse_snapshots({"XLU": "nonsense"}), {})

    def test_tolerates_junk(self):
        self.assertEqual(parse_snapshots(None), {})
        self.assertEqual(parse_snapshots([]), {})


class TestAlpacaQuoteSource(unittest.TestCase):
    class Client:
        def __init__(self, payloads=None, fail=False):
            self.payloads = payloads or {}
            self.fail = fail
            self.calls = []

        def _request(self, method, endpoint, params=None, base_url=None):
            self.calls.append(params["symbols"])
            if self.fail:
                raise RuntimeError("data api down")
            return self.payloads.get(params["symbols"], {})

    def test_batches_symbols_into_few_requests(self):
        # The whole reason breadth fits the free tier: one call, many symbols.
        client = self.Client()
        source = AlpacaQuoteSource(client, batch_size=200)
        source.quotes([f"S{n}" for n in range(150)])
        self.assertEqual(len(client.calls), 1)

    def test_splits_when_over_the_batch_size(self):
        client = self.Client()
        AlpacaQuoteSource(client, batch_size=100).quotes([f"S{n}" for n in range(250)])
        self.assertEqual(len(client.calls), 3)

    def test_a_failing_batch_is_recorded_not_raised(self):
        source = AlpacaQuoteSource(self.Client(fail=True))
        self.assertEqual(source.quotes(["XLK"]), {})
        self.assertIn("data api down", source.last_error)


class TestFredSource(unittest.TestCase):
    def test_unconfigured_without_a_key(self):
        source = FredSource(api_key=None)
        if "FRED_API_KEY" in os.environ:
            self.skipTest("FRED_API_KEY set in this environment")
        self.assertFalse(source.configured)
        self.assertIsNone(source.latest("DGS10"))
        self.assertIn("FRED_API_KEY", source.last_error)

    def test_reads_the_latest_usable_observation(self):
        payload = json.dumps({"observations": [
            {"value": "."},          # FRED marks missing observations this way
            {"value": "4.21"},
        ]}).encode()
        source = FredSource(api_key="k", opener=lambda url, timeout: payload)
        self.assertEqual(source.latest("DGS10"), 4.21)

    def test_all_missing_observations_yield_none(self):
        payload = json.dumps({"observations": [{"value": "."}, {"value": ""}]}).encode()
        source = FredSource(api_key="k", opener=lambda url, timeout: payload)
        self.assertIsNone(source.latest("DGS10"))
        self.assertIn("no usable observation", source.last_error)

    def test_a_network_failure_is_not_a_crash(self):
        def boom(url, timeout):
            raise OSError("network down")

        source = FredSource(api_key="k", opener=boom)
        self.assertIsNone(source.latest("DGS10"))
        self.assertIn("OSError", source.last_error)


class TestSectorHeatmap(unittest.TestCase):
    def test_unavailable_without_a_source(self):
        panel = MarketView().sector_heatmap()
        self.assertFalse(panel.available)
        self.assertIn("No market data source", panel.reason)
        self.assertEqual(panel.tiles, [])

    def test_sorted_best_to_worst(self):
        source = StubSource({"XLK": 2.0, "XLE": -1.5, "XLF": 0.5})
        tiles = MarketView(quote_source=source).sector_heatmap().tiles
        self.assertEqual(tiles[0].symbol, "XLK")
        self.assertEqual(tiles[-1].symbol, "XLE")

    def test_missing_symbols_sort_last_and_carry_no_number(self):
        source = StubSource({"XLK": 1.0}, cover={"XLK"})
        tiles = MarketView(quote_source=source).sector_heatmap().tiles
        self.assertEqual(tiles[0].symbol, "XLK")
        self.assertIsNone(tiles[-1].change_pct)

    def test_staleness_is_reported(self):
        panel = MarketView(quote_source=StubSource()).sector_heatmap()
        self.assertEqual(panel.staleness, "stub")


class TestBreadth(unittest.TestCase):
    def test_unavailable_without_a_source(self):
        self.assertFalse(MarketView().breadth().available)

    def test_counts_advancers_and_decliners(self):
        source = StubSource({"A": 1.0, "B": 1.0, "C": -1.0, "D": 0.0})
        panel = MarketView(quote_source=source).breadth(["A", "B", "C", "D"])
        reading = panel.breadth
        self.assertEqual((reading.advancing, reading.declining, reading.unchanged), (2, 1, 1))
        self.assertEqual(reading.net, 1)
        self.assertEqual(reading.ratio, 2.0)

    def test_partial_coverage_reads_as_partial_not_flat(self):
        # Uncovered names must not be counted as unchanged; that would show a
        # market where half the universe went nowhere.
        source = StubSource({"A": 1.0}, cover={"A"})
        reading = MarketView(quote_source=source).breadth(["A", "B", "C"]).breadth
        self.assertEqual(reading.covered, 1)
        self.assertEqual(reading.universe_size, 3)
        self.assertEqual(reading.unchanged, 0)

    def test_no_coverage_is_unavailable(self):
        source = StubSource(cover=set())
        panel = MarketView(quote_source=source).breadth(["A", "B"])
        self.assertFalse(panel.available)
        self.assertIn("No quotes", panel.reason)

    def test_ratio_is_none_when_nothing_declined(self):
        reading = Breadth(universe_size=2, advancing=2, declining=0, unchanged=0, covered=2)
        self.assertIsNone(reading.ratio)
        self.assertEqual(reading.pct_advancing, 100.0)


class TestCrossAsset(unittest.TestCase):
    def test_unavailable_with_neither_source(self):
        self.assertFalse(MarketView().cross_asset().available)

    def test_quotes_only(self):
        panel = MarketView(quote_source=StubSource({"SPY": 0.8})).cross_asset()
        self.assertTrue(panel.available)
        self.assertEqual(len(panel.tiles), len(CROSS_ASSET_PROXIES))

    def test_rates_come_from_fred_not_a_proxy(self):
        payload = json.dumps({"observations": [{"value": "4.35"}]}).encode()
        fred = FredSource(api_key="k", opener=lambda url, timeout: payload)
        panel = MarketView(quote_source=StubSource(), fred=fred).cross_asset()

        rate_tiles = [t for t in panel.tiles if t.symbol.startswith("DGS")]
        self.assertTrue(rate_tiles)
        self.assertEqual(rate_tiles[0].quote.price, 4.35)
        self.assertIn("FRED", rate_tiles[0].note)

    def test_a_failing_rate_does_not_remove_the_strip(self):
        def boom(url, timeout):
            raise OSError("down")

        fred = FredSource(api_key="k", opener=boom)
        panel = MarketView(quote_source=StubSource({"SPY": 0.1}), fred=fred).cross_asset()
        self.assertTrue(panel.available)
        self.assertFalse([t for t in panel.tiles if t.symbol.startswith("DGS")])


class TestAllPanels(unittest.TestCase):
    def test_shape(self):
        payload = MarketView(quote_source=StubSource({"XLK": 1.0})).all_panels()
        for key in ("sector_heatmap", "breadth", "cross_asset", "universe_name", "source"):
            self.assertIn(key, payload)
        self.assertEqual(payload["source"], "StubSource")
        self.assertIsNone(payload["rates_source"])

    def test_unconfigured_view_still_returns_every_panel(self):
        payload = MarketView().all_panels()
        for key in ("sector_heatmap", "breadth", "cross_asset"):
            self.assertFalse(payload[key]["available"])


class TestBuildMarketView(unittest.TestCase):
    def test_builds_without_credentials_rather_than_raising(self):
        saved = {k: os.environ.pop(k, None) for k in ("ALPACA_API_KEY", "ALPACA_API_SECRET")}
        try:
            view = build_market_view()
            self.assertIsNone(view.quote_source)
            self.assertFalse(view.sector_heatmap().available)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
