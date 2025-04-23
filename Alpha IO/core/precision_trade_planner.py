# precision_trade_planner.py – maps signal confidence to optimal trade type (futures, options, spreads)

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
                "note": "Captures upside with capped risk due to high implied vol"
            }
        else:
            return {
                "strategy": "ADA futures (3x leverage)",
                "structure": "Long ADAUSDT-PERP",
                "leverage": "aggressive",
                "note": "Confidence warrants directional exposure"
            }

    # Medium confidence, mid/long term = futures or calendar spreads
    if 0.6 < confidence <= 0.8:
        return {
            "strategy": "futures calendar spread",
            "structure": "Long front-month, short back-month",
            "leverage": "neutral",
            "note": "Expresses a relative value view over time"
        }

    # Low confidence or choppy signal
    if confidence <= 0.6:
        return {
            "strategy": "do nothing",
            "structure": "n/a",
            "leverage": "n/a",
            "note": "Signal too weak or unclear – wait for clarity"
        }

    return {
        "strategy": "discretionary",
        "structure": "Manual override required",
        "note": "Edge case"
    }

# Test signal mapping
if __name__ == "__main__":
    signal = "ADA bullish after Fed hold"
    result = map_signal_to_trade(signal, 0.84, timing="short_term", volatility="high")
    print("[TRADE PLANNER] Recommended Strategy:", result)