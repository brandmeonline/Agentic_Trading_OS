"""ATOS-P3-EXEC-001 — the venue's rules, and the claims we stopped making.

A fill model that charges a spread is not an execution model that knows what
a venue will accept. The gap is where a strategy that backtests beautifully
meets its first rejection: an order at 15:59:59 the market never saw, 3.7
shares of a whole-share symbol, a $4 crypto order against a $10 minimum, a
short with no borrow. None of them lose money in a backtest, and all of them
are a silent divergence in the flattering direction.

The second half is the withdrawal. The README claimed execution via futures,
options and spreads; the code behind it returns a hardcoded option spread and
the system has no multiplier, expiry, margin, assignment or Greeks. Section 26
says do not claim support until the semantics exist, so the claim is gone and
the refusals are tested.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.precision_trade_planner import (  # noqa: E402
    map_signal_to_trade,
    plan_for_execution,
)
from core.safety_config import SafetyConfig  # noqa: E402
from core.venue_rules import (  # noqa: E402
    MISSING_SEMANTICS,
    SUPPORTED_FOR_LIVE,
    AssetClass,
    CryptoCalendar,
    EquityCalendar,
    ExecutionContext,
    InstrumentRules,
    OrderRefused,
    SessionState,
    UnsupportedAssetClass,
    crypto_pair,
    describe_support,
    live_support_problems,
    require_live_support,
    us_equity,
)

pytestmark = pytest.mark.adversarial

REPO = Path(__file__).resolve().parents[2]


def _et(year, month, day, hour, minute=0):
    """A UTC moment corresponding to the given Eastern wall clock (UTC-5)."""
    return datetime(year, month, day, hour + 5, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_the_regular_session_is_tradeable():
    """The baseline; every refusal below breaks one thing about it."""
    calendar = EquityCalendar()
    moment = _et(2026, 4, 1, 10, 30)      # Wednesday, mid-morning
    assert calendar.state(moment) is SessionState.REGULAR
    assert calendar.may_trade(moment) == (True, "")


def test_one_second_after_the_close_is_not_tradeable():
    """The 15:59:59 order that the venue never saw."""
    calendar = EquityCalendar()
    assert calendar.state(_et(2026, 4, 1, 15, 59)) is SessionState.REGULAR
    assert calendar.state(_et(2026, 4, 1, 16, 0)) is SessionState.AFTER_HOURS
    assert calendar.may_trade(_et(2026, 4, 1, 16, 0))[0] is False


def test_the_open_is_inclusive_and_the_close_is_not():
    calendar = EquityCalendar()
    assert calendar.state(_et(2026, 4, 1, 9, 30)) is SessionState.REGULAR
    assert calendar.state(_et(2026, 4, 1, 9, 29)) is SessionState.PRE_MARKET


def test_a_weekend_is_closed():
    calendar = EquityCalendar()
    assert calendar.state(_et(2026, 4, 4, 11)) is SessionState.CLOSED   # Sat
    assert calendar.state(_et(2026, 4, 5, 11)) is SessionState.CLOSED   # Sun


def test_a_holiday_is_closed():
    calendar = EquityCalendar(holidays=frozenset({date(2026, 4, 3)}))
    assert calendar.state(_et(2026, 4, 3, 11)) is SessionState.CLOSED
    assert calendar.state(_et(2026, 4, 2, 11)) is SessionState.REGULAR


def test_extended_hours_are_refused_unless_enabled():
    moment = _et(2026, 4, 1, 7)
    closed = EquityCalendar()
    assert closed.state(moment) is SessionState.PRE_MARKET
    allowed, reason = closed.may_trade(moment)
    assert allowed is False
    assert "not enabled" in reason
    assert "calibrated" in reason

    opened = EquityCalendar(allow_extended_hours=True)
    assert opened.may_trade(moment)[0] is True


def test_a_halt_outranks_an_open_session():
    calendar = EquityCalendar(halted_symbols=frozenset({"AAPL"}))
    moment = _et(2026, 4, 1, 10, 30)
    assert calendar.state(moment, "AAPL") is SessionState.HALTED
    assert calendar.may_trade(moment, "AAPL") == (False, "AAPL is halted")
    assert calendar.may_trade(moment, "MSFT")[0] is True


def test_only_the_regular_session_counts_as_tradeable():
    tradeable = {s for s in SessionState if s.tradeable}
    assert tradeable == {SessionState.REGULAR}


def test_crypto_trades_at_the_weekend():
    calendar = CryptoCalendar()
    assert calendar.may_trade(_et(2026, 4, 4, 3))[0] is True


def test_a_crypto_venue_outage_is_the_equivalent_of_a_halt():
    """24/7 means there is no session to hide behind when one happens."""
    calendar = CryptoCalendar(venue_down=True)
    allowed, reason = calendar.may_trade(_et(2026, 4, 1, 10))
    assert allowed is False
    assert "venue is down" in reason
    assert calendar.state(_et(2026, 4, 1, 10)) is SessionState.HALTED


def test_a_single_crypto_pair_can_be_halted():
    calendar = CryptoCalendar(halted_symbols=frozenset({"LUNA/USD"}))
    assert calendar.may_trade(_et(2026, 4, 1, 10), "LUNA/USD")[0] is False
    assert calendar.may_trade(_et(2026, 4, 1, 10), "BTC/USD")[0] is True


def test_the_eastern_offset_is_configurable():
    """An hour at the open is the whole day's liquidity."""
    standard = EquityCalendar(et_offset_hours=-5)
    daylight = EquityCalendar(et_offset_hours=-4)
    moment = datetime(2026, 7, 1, 13, 45, tzinfo=timezone.utc)
    assert standard.state(moment) is SessionState.PRE_MARKET
    assert daylight.state(moment) is SessionState.REGULAR


def test_a_naive_timestamp_is_read_as_utc():
    calendar = EquityCalendar()
    naive = datetime(2026, 4, 1, 15, 30)      # 10:30 Eastern
    assert calendar.state(naive) is SessionState.REGULAR


# ---------------------------------------------------------------------------
# Instrument rules
# ---------------------------------------------------------------------------


def test_a_valid_equity_order_is_accepted():
    assert us_equity("AAPL").problems(quantity=10, price=190.25) == []


def test_a_fractional_quantity_is_refused_for_a_whole_share_symbol():
    """3.7 shares is a strategy the venue would not have executed."""
    problems = us_equity("AAPL").problems(quantity=3.7, price=190.0)
    assert any("fractional" in p for p in problems)


def test_a_fractional_quantity_is_accepted_when_the_symbol_allows_it():
    assert us_equity("AAPL", fractional=True).problems(3.7, 190.0) == []


def test_quantizing_rounds_a_whole_share_symbol_down():
    assert us_equity("AAPL").quantize_quantity(3.7) == 3.0
    assert us_equity("AAPL", fractional=True).quantize_quantity(3.7) == 3.7


def test_a_price_off_the_tick_is_refused():
    problems = us_equity("AAPL").problems(quantity=1, price=190.2537)
    assert any("tick" in p for p in problems)
    assert us_equity("AAPL").quantize_price(190.2537) == 190.25


def test_a_crypto_order_below_the_minimum_notional_is_refused():
    rules = crypto_pair("BTC/USD", min_notional=10.0)
    problems = rules.problems(quantity=0.00001, price=40_000.0)
    assert any("below the 10.00 minimum" in p for p in problems)
    assert rules.problems(quantity=0.001, price=40_000.0) == []


def test_a_quantity_below_the_minimum_is_refused():
    rules = InstrumentRules("X", AssetClass.CRYPTO, fractional=True,
                            min_quantity=0.01)
    assert rules.problems(0.001, 100.0)
    assert rules.problems(0.02, 100.0) == []


def test_venue_precision_is_applied_to_quantity():
    rules = crypto_pair("BTC/USD", quantity_precision=3, lot_size=0.001)
    assert rules.quantize_quantity(0.123456) == 0.123


def test_a_short_in_a_non_shortable_name_is_refused():
    problems = us_equity("MEME", shortable=False).problems(
        10, 5.0, side="sell_short"
    )
    assert any("not shortable" in p for p in problems)


def test_a_short_with_no_borrow_is_refused():
    rules = InstrumentRules("HTB", AssetClass.EQUITY, shortable=True,
                            borrow_available=False)
    problems = rules.problems(10, 5.0, side="sell_short")
    assert any("no borrow" in p for p in problems)


def test_a_short_with_borrow_is_accepted():
    rules = InstrumentRules("SPY", AssetClass.EQUITY, shortable=True,
                            borrow_available=True)
    assert rules.problems(10, 500.0, side="sell_short") == []


def test_a_non_positive_quantity_is_refused_before_anything_else():
    problems = us_equity("AAPL").problems(0, 190.0)
    assert problems == ["quantity is not positive"]


def test_require_raises_with_every_reason():
    with pytest.raises(OrderRefused) as caught:
        us_equity("AAPL").problems  # keep the reference honest
        us_equity("AAPL").require(3.7, 190.2537)
    message = str(caught.value)
    assert "fractional" in message and "tick" in message


# ---------------------------------------------------------------------------
# The check both paths run
# ---------------------------------------------------------------------------


def test_a_good_order_in_session_passes():
    context = ExecutionContext(rules=us_equity("AAPL"))
    assert context.may_place(10, 190.25, _et(2026, 4, 1, 10, 30)) == (True, "")


def test_the_session_and_the_instrument_are_both_checked():
    context = ExecutionContext(rules=us_equity("AAPL"))
    allowed, reason = context.may_place(3.7, 190.2537, _et(2026, 4, 4, 10))
    assert allowed is False
    assert "closed" in reason
    assert "fractional" in reason
    assert "tick" in reason


def test_a_crypto_order_uses_the_crypto_calendar():
    context = ExecutionContext(
        rules=crypto_pair("BTC/USD"),
        crypto_calendar=CryptoCalendar(venue_down=True),
    )
    allowed, reason = context.may_place(0.01, 40_000.0, _et(2026, 4, 4, 3))
    assert allowed is False
    assert "venue is down" in reason


def test_the_same_context_is_usable_for_a_backtest_and_a_live_path():
    """A backtest that fills an order the venue would reject has tested a
    different system, so both call the same function."""
    rules = us_equity("AAPL")
    moment = _et(2026, 4, 1, 10, 30)

    backtest = ExecutionContext(rules=rules, live=False)
    live = ExecutionContext(rules=rules, live=True)

    assert backtest.problems(10, 190.25, moment) == \
        live.problems(10, 190.25, moment)


# ---------------------------------------------------------------------------
# The claims we stopped making
# ---------------------------------------------------------------------------


def test_equities_and_crypto_are_the_supported_classes():
    assert SUPPORTED_FOR_LIVE == {AssetClass.EQUITY, AssetClass.CRYPTO}
    assert live_support_problems(AssetClass.EQUITY) == []
    assert live_support_problems(AssetClass.CRYPTO) == []
    require_live_support(AssetClass.EQUITY)


@pytest.mark.parametrize("asset_class", [
    AssetClass.FUTURE, AssetClass.OPTION, AssetClass.FX,
])
def test_an_unsupported_class_is_refused_by_name(asset_class):
    problems = live_support_problems(asset_class)
    assert problems
    with pytest.raises(UnsupportedAssetClass):
        require_live_support(asset_class)


def test_the_refusal_says_what_is_missing_rather_than_just_no():
    """"Unsupported" invites someone to flip a flag; "no margin model" does
    not."""
    reason = live_support_problems(AssetClass.OPTION)[0]
    for requirement in ("contract multipliers", "expiries",
                        "assignment and exercise", "Greeks"):
        assert requirement in reason


def test_futures_name_the_margin_and_liquidation_gaps():
    missing = MISSING_SEMANTICS[AssetClass.FUTURE]
    assert any("margin" in m for m in missing)
    assert any("liquidation" in m for m in missing)
    assert any("reduce-only" in m for m in missing)


def test_a_live_context_refuses_an_unsupported_class():
    rules = InstrumentRules("ES", AssetClass.FUTURE)
    live = ExecutionContext(rules=rules, live=True)
    allowed, reason = live.may_place(1, 5000.0, _et(2026, 4, 1, 10, 30))
    assert allowed is False
    assert "not supported for live execution" in reason


def test_the_same_class_is_allowed_in_a_research_context():
    """Refused for live, not erased: research on a class we cannot trade is
    still research."""
    rules = InstrumentRules("ES", AssetClass.FUTURE, min_quantity=1.0)
    research = ExecutionContext(rules=rules, live=False)
    problems = research.problems(1, 5000.0, _et(2026, 4, 1, 10, 30))
    assert all("not supported for live" not in p for p in problems)


def test_the_safety_config_refuses_derivatives_in_live():
    problems = SafetyConfig(mode="live", allow_options=True).problems()
    assert any("not supported for live execution" in p for p in problems)

    problems = SafetyConfig(mode="live", allow_futures=True).problems()
    assert any("not supported for live execution" in p for p in problems)


def test_the_planner_stamps_every_result_as_not_executable():
    for confidence, volatility in ((0.9, "high"), (0.9, "low"), (0.7, "medium"),
                                   (0.5, "medium"), (0.62, "medium")):
        result = map_signal_to_trade("signal", confidence, volatility=volatility)
        assert result["executable"] is False
        assert "research output only" in result["reason"]


def test_the_planner_has_no_working_execution_path():
    """Returning something plausible is how a research note becomes an order."""
    with pytest.raises(UnsupportedAssetClass) as caught:
        plan_for_execution()
    assert "contract multipliers" in str(caught.value)


def test_the_support_description_is_generated_not_written_down():
    """So the claim and the code cannot drift apart the way they had."""
    described = describe_support()
    assert described["supported_for_live"] == ["crypto", "equity"]
    assert set(described["unsupported"]) == {"fx", "future", "option"}


def test_the_readme_no_longer_claims_execution_via_futures_and_options():
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    # The claim was a bullet in the "It supports:" list. It may still appear
    # inside the paragraph that explains its removal, which is the opposite
    # of a claim - so the check is on the line, not the substring.
    lines = [line.strip() for line in readme.splitlines()]
    assert "Execution via futures, options, spreads" not in lines

    assert "research output, not executable" in readme
    assert "equities and crypto" in readme


def test_the_readme_line_about_the_planner_says_research_only():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    planner_lines = [line for line in readme.splitlines()
                     if line.startswith("core/precision_trade_planner.py:")]
    assert planner_lines
    assert "RESEARCH OUTPUT ONLY" in planner_lines[0]
