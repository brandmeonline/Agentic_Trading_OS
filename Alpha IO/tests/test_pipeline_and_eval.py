"""Tests for corpus persistence, signal evaluation, and the trading pipeline.

The load-bearing properties:

1. The corpus survives a restart, and a corrupt cache degrades rather than
   blocking startup.
2. No edge is ever declared on a small sample, and none is declared when a
   baseline explains the result.
3. A live order is impossible without an established edge — enforced in code,
   asserted here.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.news_feed import NewsCorpus, NewsItem, SourceCategory, content_id, extract_tickers
from core.pipeline import Blocked, PipelineMode, TradingPipeline
from core.signal_eval import (
    MIN_SAMPLES,
    EdgeReport,
    SignalEvaluator,
    SignalObservation,
    Verdict,
    observation_from_measurement,
    wilson_interval,
)

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


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


# =============================================================================
# Persistence
# =============================================================================

class TestCorpusPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "corpus.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def corpus(self, **kwargs):
        kwargs.setdefault("window_hours", 24)
        kwargs.setdefault("clock", lambda: NOW)
        kwargs.setdefault("sqlite_path", self.db)
        return NewsCorpus(**kwargs)

    def test_documents_survive_a_restart(self):
        first = self.corpus()
        first.add(item("NVDA beats"))
        first.extend([item("NVDA rallies"), item("TSMC expands")])

        second = self.corpus()
        self.assertEqual(len(second), 3)
        self.assertEqual(second.mention_count("NVDA", NOW), 2)

    def test_dedupe_holds_across_a_restart(self):
        self.corpus().add(item("NVDA beats"))
        restarted = self.corpus()
        self.assertFalse(restarted.add(item("NVDA beats")))
        self.assertEqual(len(restarted), 1)

    def test_out_of_window_documents_are_not_restored(self):
        first = self.corpus()
        first.add(item("stale", published=NOW - timedelta(hours=48)))
        first.add(item("fresh", published=NOW - timedelta(hours=1)))
        self.assertEqual([i.title for i in self.corpus().recent(NOW)], ["fresh"])

    def test_purge_removes_rows_from_disk(self):
        first = self.corpus()
        first.add(item("stale", published=NOW - timedelta(hours=48)))
        first.add(item("fresh", published=NOW - timedelta(hours=1)))
        first.purge(NOW)
        # A later clock must not resurrect what purge deleted.
        later = NewsCorpus(window_hours=999, clock=lambda: NOW, sqlite_path=self.db)
        self.assertEqual([i.title for i in later.recent(NOW)], ["fresh"])

    def test_entities_and_metadata_round_trip(self):
        self.corpus().add(item("NVDA beats on strong growth", source_name="reuters"))
        restored = self.corpus().recent(NOW)[0]
        self.assertIn("NVDA", restored.entities)
        self.assertEqual(restored.source, "reuters")
        self.assertEqual(restored.category, SourceCategory.WIRE)
        self.assertAlmostEqual(restored.credibility, 0.85)

    def test_a_corrupt_cache_degrades_rather_than_blocking_startup(self):
        # Deliberately unlike the ledger: the corpus is reconstructible by
        # polling, so refusing to start would trade a recoverable problem for
        # an outage.
        with open(self.db, "w", encoding="utf-8") as handle:
            handle.write("this is not a database")

        corpus = self.corpus()
        self.assertEqual(len(corpus), 0)
        self.assertIsNotNone(corpus.load_error)
        self.assertTrue(corpus.stats(NOW)["persisted"])

    def test_in_memory_corpus_still_works_without_a_path(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        corpus.add(item("NVDA beats"))
        self.assertEqual(len(corpus), 1)
        self.assertFalse(corpus.stats(NOW)["persisted"])


# =============================================================================
# Evaluation
# =============================================================================

def observation(direction=1, forward=None, crowd=0.5, at=None):
    obs = SignalObservation(
        timestamp=at or NOW,
        term="NVDA",
        score=0.5,
        direction=direction,
        crowd_sentiment=crowd,
        sources=3,
    )
    obs.forward_return = forward
    return obs


class TestWilsonInterval(unittest.TestCase):
    def test_zero_trials_is_degenerate_not_an_error(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))

    def test_interval_brackets_the_estimate(self):
        low, high = wilson_interval(60, 100)
        self.assertLess(low, 0.6)
        self.assertGreater(high, 0.6)

    def test_interval_tightens_with_sample_size(self):
        narrow = wilson_interval(600, 1000)
        wide = wilson_interval(6, 10)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_bounds_stay_inside_zero_one(self):
        low, high = wilson_interval(100, 100)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)


class TestSignalEvaluator(unittest.TestCase):
    def evaluator(self, prices=None):
        prices = prices or {}
        return SignalEvaluator(price_lookup=lambda term, at: prices.get((term, at)))

    def test_attach_outcomes_computes_forward_return(self):
        entry, exit_at = NOW, NOW + timedelta(days=1)
        evaluator = self.evaluator({("NVDA", entry): 100.0, ("NVDA", exit_at): 110.0})
        [obs] = evaluator.attach_outcomes([observation()])
        self.assertAlmostEqual(obs.forward_return, 0.10)

    def test_missing_prices_leave_the_signal_unevaluated_not_flat(self):
        # Treating an unknown outcome as a zero return biases the hit rate.
        [obs] = self.evaluator().attach_outcomes([observation()])
        self.assertIsNone(obs.forward_return)
        self.assertFalse(obs.evaluated)

    def test_small_samples_establish_nothing_however_good(self):
        # 30 perfect signals. Still not evidence.
        perfect = [observation(direction=1, forward=0.05) for _ in range(30)]
        report = self.evaluator().evaluate(perfect)
        self.assertIs(report.verdict, Verdict.INSUFFICIENT)
        self.assertFalse(report.established)
        self.assertIn("noise", report.reason)

    def test_an_edge_that_only_matches_the_baseline_is_no_edge(self):
        # Every signal long, every move up: 100% hit rate, and always-long
        # scores exactly the same. The signal added nothing.
        observations = [observation(direction=1, forward=0.02) for _ in range(MIN_SAMPLES + 20)]
        report = self.evaluator().evaluate(observations)
        self.assertIs(report.verdict, Verdict.NO_EDGE)
        self.assertEqual(report.hit_rate, 1.0)
        self.assertEqual(report.best_baseline().hit_rate, 1.0)

    def test_a_genuine_edge_is_established(self):
        # Signal is right 85% of the time and points against the crowd, so no
        # baseline reproduces it.
        observations = []
        for n in range(200):
            right = n % 100 < 85
            # Crowd is bullish; signal is short; the move goes down when right.
            observations.append(observation(
                direction=-1,
                forward=-0.02 if right else 0.02,
                crowd=0.8,
            ))
        report = self.evaluator().evaluate(observations)
        self.assertIs(report.verdict, Verdict.ESTABLISHED)
        self.assertGreater(report.hit_rate_low, report.best_baseline().hit_rate)
        self.assertGreater(report.mean_return, 0)

    def test_no_view_signals_are_excluded(self):
        observations = [observation(direction=0, forward=0.02) for _ in range(MIN_SAMPLES + 10)]
        report = self.evaluator().evaluate(observations)
        self.assertIs(report.verdict, Verdict.INSUFFICIENT)
        self.assertEqual(report.samples, 0)

    def test_report_names_all_three_baselines(self):
        observations = [observation(direction=1, forward=0.01 if n % 2 else -0.01)
                        for n in range(MIN_SAMPLES + 10)]
        names = {b.name for b in self.evaluator().evaluate(observations).baselines}
        self.assertEqual(names, {"always-long", "follow-the-crowd", "coin-flip"})

    def test_summary_is_readable(self):
        report = self.evaluator().evaluate([observation(forward=0.01) for _ in range(5)])
        self.assertIn("INSUFFICIENT", report.summary())


class TestObservationFromMeasurement(unittest.TestCase):
    def test_direction_defaults_against_the_crowd(self):
        class Measurement:
            term, score, crowd_sentiment, sources = "NVDA", 0.5, 0.9, 3

        obs = observation_from_measurement(Measurement(), NOW)
        self.assertEqual(obs.direction, -1, "a bullish tape should imply a short view")

        class Bearish(Measurement):
            crowd_sentiment = 0.1

        self.assertEqual(observation_from_measurement(Bearish(), NOW).direction, 1)

    def test_explicit_direction_wins(self):
        class Measurement:
            term, score, crowd_sentiment, sources = "NVDA", 0.5, 0.9, 3

        self.assertEqual(observation_from_measurement(Measurement(), NOW, direction=1).direction, 1)


# =============================================================================
# Pipeline
# =============================================================================

class FakeRouter:
    def __init__(self, decision="trade", score=0.7, sources=3, measured=True):
        self.payload = {
            "decision": decision,
            "asymmetry_score": score,
            "asymmetry": {"measured": measured, "sources": sources, "term": None},
            "note": "fixture",
        }

    def route(self, text, confidence, term=None):
        payload = dict(self.payload)
        payload["asymmetry"] = dict(payload["asymmetry"], term=term)
        return payload


class FakeRisk:
    def __init__(self, allowed=True, notional=1000.0):
        self.allowed = allowed
        self.notional = notional
        self.sized = []

    def check_risk_limits(self, asset):
        return self.allowed

    def calculate_position_size(self, asset, confidence, entry_price=None, stop_loss_pct=None):
        self.sized.append((asset, confidence, entry_price))
        return self.notional


class FakeEngine:
    def __init__(self, fail=False):
        self.fail = fail
        self.prices = {}
        self.submitted = []

    def set_price(self, asset, price):
        self.prices[asset] = price

    def create_order(self, asset, side, quantity, **kwargs):
        if self.fail:
            raise RuntimeError("broker down")

        class Order:
            order_id = f"ord-{asset}"

        Order.asset, Order.side, Order.quantity = asset, side, quantity
        return Order()

    def submit_order(self, order, algo=None):
        self.submitted.append(order)
        return {"status": "filled"}


def established_report():
    return EdgeReport(
        verdict=Verdict.ESTABLISHED, samples=500, hit_rate=0.62,
        hit_rate_low=0.58, hit_rate_high=0.66, mean_return=0.004,
        reason="fixture",
    )


def no_edge_report():
    return EdgeReport(
        verdict=Verdict.NO_EDGE, samples=500, hit_rate=0.51,
        hit_rate_low=0.47, hit_rate_high=0.55, mean_return=0.0,
        reason="fixture",
    )


class PipelineCase(unittest.TestCase):
    def build(self, **kwargs):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        corpus.add(item("NVDA beats"))
        kwargs.setdefault("corpus", corpus)
        kwargs.setdefault("router", FakeRouter())
        kwargs.setdefault("risk_manager", FakeRisk())
        kwargs.setdefault("execution_engine", FakeEngine())
        kwargs.setdefault("price_lookup", lambda term: 100.0)
        kwargs.setdefault("clock", lambda: NOW)
        return TradingPipeline(**kwargs)


class TestEvidenceGate(PipelineCase):
    """The gate that matters: no measured edge, no live order."""

    def test_live_without_evidence_downgrades_to_paper(self):
        pipeline = self.build(mode=PipelineMode.LIVE, edge_report=None)
        self.assertEqual(pipeline.effective_mode(), PipelineMode.PAPER)
        self.assertFalse(pipeline.edge_established)

        status = pipeline.gate_status()
        self.assertTrue(status["downgraded"])
        self.assertEqual(status["requested_mode"], "live")
        self.assertEqual(status["effective_mode"], "paper")

    def test_live_with_a_no_edge_report_still_downgrades(self):
        pipeline = self.build(mode=PipelineMode.LIVE, edge_report=no_edge_report())
        self.assertEqual(pipeline.effective_mode(), PipelineMode.PAPER)

    def test_live_with_an_established_edge_stays_live(self):
        pipeline = self.build(mode=PipelineMode.LIVE, edge_report=established_report())
        self.assertTrue(pipeline.edge_established)
        self.assertEqual(pipeline.effective_mode(), PipelineMode.LIVE)
        self.assertFalse(pipeline.gate_status()["downgraded"])

    def test_the_downgrade_reason_is_recorded_on_the_decision(self):
        pipeline = self.build(mode=PipelineMode.LIVE, edge_report=None)
        result = pipeline.run_cycle(terms=["NVDA"])
        [decision] = result.decisions
        self.assertEqual(decision.blocked_by, Blocked.NO_EDGE_EVIDENCE)
        self.assertIn("established edge", decision.detail)
        # It still executed on paper — the gate blocks live, not the system.
        self.assertTrue(decision.ordered)

    def test_paper_mode_needs_no_evidence(self):
        pipeline = self.build(mode=PipelineMode.PAPER)
        self.assertEqual(pipeline.effective_mode(), PipelineMode.PAPER)


class TestPipelineGates(PipelineCase):
    def test_observe_mode_orders_nothing(self):
        engine = FakeEngine()
        pipeline = self.build(mode=PipelineMode.OBSERVE, execution_engine=engine)
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.ordered, 0)
        self.assertEqual(result.decisions[0].blocked_by, Blocked.OBSERVE_MODE)
        self.assertEqual(engine.submitted, [])

    def test_a_non_trade_routing_is_not_ordered(self):
        pipeline = self.build(router=FakeRouter(decision="watchlist"))
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.decisions[0].blocked_by, Blocked.NOT_ROUTED)
        self.assertEqual(result.ordered, 0)

    def test_risk_limits_block_the_order(self):
        pipeline = self.build(risk_manager=FakeRisk(allowed=False))
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.decisions[0].blocked_by, Blocked.RISK_LIMITS)

    def test_zero_size_blocks_the_order(self):
        pipeline = self.build(risk_manager=FakeRisk(notional=0.0))
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.decisions[0].blocked_by, Blocked.ZERO_SIZE)

    def test_missing_price_blocks_the_order(self):
        pipeline = self.build(price_lookup=lambda term: None)
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.decisions[0].blocked_by, Blocked.NO_PRICE)

    def test_execution_failure_is_recorded_not_raised(self):
        pipeline = self.build(execution_engine=FakeEngine(fail=True))
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.decisions[0].blocked_by, Blocked.EXECUTION_FAILED)
        self.assertFalse(result.decisions[0].ordered)


class TestPipelineSizing(PipelineCase):
    def test_notional_is_converted_to_a_share_count(self):
        # calculate_position_size returns capital units, not shares. Getting
        # this backwards would size every order by a factor of the price.
        risk = FakeRisk(notional=1000.0)
        pipeline = self.build(risk_manager=risk, price_lookup=lambda term: 250.0)
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertAlmostEqual(result.decisions[0].quantity, 4.0)

    def test_risk_manager_receives_the_entry_price(self):
        risk = FakeRisk()
        pipeline = self.build(risk_manager=risk, price_lookup=lambda term: 123.0)
        pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(risk.sized[0][2], 123.0)

    def test_a_sizing_failure_means_no_order(self):
        class BrokenRisk(FakeRisk):
            def calculate_position_size(self, *args, **kwargs):
                raise RuntimeError("sizing exploded")

        pipeline = self.build(risk_manager=BrokenRisk())
        result = pipeline.run_cycle(terms=["NVDA"])
        self.assertEqual(result.decisions[0].blocked_by, Blocked.ZERO_SIZE)


class TestPipelineCycle(PipelineCase):
    def test_candidates_come_from_the_corpus_when_none_are_given(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(3):
            corpus.add(item(f"NVDA story {n}"))
        corpus.add(item("TSMC expands"))
        pipeline = self.build(corpus=corpus, mode=PipelineMode.OBSERVE)
        terms = [d.term for d in pipeline.run_cycle().decisions]
        self.assertEqual(terms[0], "NVDA", "most-mentioned term should come first")
        self.assertIn("TSMC", terms)

    def test_orders_per_cycle_are_capped(self):
        pipeline = self.build(max_orders_per_cycle=2)
        result = pipeline.run_cycle(terms=["AAA", "BBB", "CCC", "DDD"])
        self.assertEqual(result.ordered, 2)

    def test_blocked_counts_summarise_the_cycle(self):
        pipeline = self.build(router=FakeRouter(decision="ignore"))
        pipeline.run_cycle(terms=["AAA", "BBB"])
        self.assertEqual(pipeline.last_result.blocked_counts(), {"not_routed": 2})

    def test_stats_expose_the_gates(self):
        pipeline = self.build(mode=PipelineMode.LIVE, edge_report=established_report())
        pipeline.run_cycle(terms=["NVDA"])
        stats = pipeline.stats()
        self.assertTrue(stats["gates"]["edge_established"])
        self.assertEqual(stats["last_cycle"]["mode"], "live")


if __name__ == "__main__":
    unittest.main()
