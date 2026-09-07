"""Maps signal confidence to a trade *shape* — ATOS-P3-EXEC-001.

Research output only. Nothing here is executable, and the module refuses to
pretend otherwise.

Every structure below is a derivative: option spreads, perpetual futures,
calendar spreads. This system has no contract multipliers, no expiry or roll
handling, no margin model, no assignment or exercise, no Greeks and no open-
interest limits - see MISSING_SEMANTICS in core/venue_rules.py for the full
list. A recommendation of "ADA futures (3x leverage)" from a module that
cannot compute a maintenance margin is a sentence, not a trade.

So the output is labelled. Every result carries executable=False and the
reason, ``plan_for_execution`` raises rather than returning anything, and the
README no longer claims execution via futures and options. When those
semantics exist, this module becomes useful; until then, saying so is the
useful thing it does.
"""

from typing import Any, Dict

from core.venue_rules import AssetClass, MISSING_SEMANTICS, UnsupportedAssetClass

#: Stamped onto every result. A caller that ignores it has been told.
NOT_EXECUTABLE = {
    "executable": False,
    "reason": (
        "derivative structures are research output only: this system has no "
        "contract multiplier, expiry, margin, assignment or Greeks handling "
        "(ATOS-P3-EXEC-001)"
    ),
}


def plan_for_execution(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """The function a caller would reach for to trade one of these.

    It raises. There is no correct implementation until the semantics listed
    in core/venue_rules.MISSING_SEMANTICS exist, and returning something
    plausible in the meantime is how a research note becomes an order.
    """
    missing = sorted(set(
        item for asset in (AssetClass.OPTION, AssetClass.FUTURE)
        for item in MISSING_SEMANTICS[asset]
    ))
    raise UnsupportedAssetClass(
        "these structures cannot be executed by this system; missing: "
        + ", ".join(missing)
    )


def map_signal_to_trade(signal_text, confidence, timing="short_term", volatility="medium"):
    """
    This function receives a signal (text + metadata) and determines the best type of trade to execute.
    Parameters:
        signal_text (str): The raw alpha signal (e.g. "Bullish on ADA after CPI report")
        confidence (float): Agent-derived confidence score (0.0 - 1.0)
        timing (str): "short_term", "mid_term", "long_term"
        volatility (str): "low", "medium", "high"
    Returns:
        dict: Recommended trade structure
    """

    # High confidence, short-term = leverage or spread
    if confidence > 0.8 and timing == "short_term":
        if volatility == "high":
            return {
                "strategy": "bull call spread",
                "structure": "Buy ADA 0.42C / Sell 0.48C",
                "leverage": "defined risk",
                "note": "Captures upside with capped risk due to high implied vol",
                **NOT_EXECUTABLE,
            }
        else:
            return {
                "strategy": "ADA futures (3x leverage)",
                "structure": "Long ADAUSDT-PERP",
                "leverage": "aggressive",
                "note": "Confidence warrants directional exposure",
                **NOT_EXECUTABLE,
            }

    # Medium confidence, mid/long term = futures or calendar spreads
    if 0.6 < confidence <= 0.8:
        return {
            "strategy": "futures calendar spread",
            "structure": "Long front-month, short back-month",
            "leverage": "neutral",
            "note": "Expresses a relative value view over time",
            **NOT_EXECUTABLE,
        }

    # Low confidence or choppy signal
    if confidence <= 0.6:
        return {
            "strategy": "do nothing",
            "structure": "n/a",
            "leverage": "n/a",
            "note": "Signal too weak or unclear - wait for clarity",
            **NOT_EXECUTABLE,
        }

    return {
        "strategy": "discretionary",
        "structure": "Manual override required",
        "note": "Edge case",
        **NOT_EXECUTABLE,
    }

# Test signal mapping
if __name__ == "__main__":
    signal = "ADA bullish after Fed hold"
    result = map_signal_to_trade(signal, 0.84, timing="short_term", volatility="high")
    print("[TRADE PLANNER] Recommended Strategy:", result)