"""Performance-driven parameter suggestions — ATOS-P3-TUNE-001.

Invariant:

    This module can suggest trading *less*. It cannot suggest trading more,
    and it cannot change anything.

The previous version's central rule was

    if win_rate > 0.65:
        confidence -= 0.05
        risk += 0.005

which reads as: the last few trades went well, so lower the bar and risk more.
That is a martingale with a spreadsheet. It is at its most aggressive
immediately after a run of luck, which is precisely when the next trade is
least likely to continue it, and it computed the win rate from the trade log
it had just been judged on - so the evidence and the fit were the same data.
When the log was missing it returned 0.5 and 0, and tuned on nothing.

What is left is deliberately one-directional. Bad news tightens; good news
changes nothing. The asymmetry is the point: the failure mode of being too
careful is opportunity cost, and the failure mode of the other direction is
the account. Anything that wants to loosen a parameter goes through
:mod:`core.tuning_governance` - shadow, out-of-sample evidence, a named human
- and even then cannot exceed the promoted safety ceilings.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.tuning_governance import ParameterSet, TuningSuggestion

logger = logging.getLogger(__name__)

#: Below this many closed trades, a win rate is not a measurement.
MINIMUM_TRADES = 30


class AutoTuner:
    """Suggests tighter parameters after bad performance. Nothing else."""

    def __init__(self, base_confidence: float = 0.7, base_risk: float = 0.015):
        self.base_confidence = base_confidence
        self.base_risk = base_risk

    # -- evidence ---------------------------------------------------------

    def evaluate_performance(
        self, trade_log_path: str = "data/trade_log.csv"
    ) -> Optional[Dict[str, Any]]:
        """Read the trade log, or return None.

        None, not neutral values. The previous version returned (0.5, 0, 0) on
        a missing file, which the caller could not distinguish from a genuinely
        break-even record - so a system with no trade log at all would still
        "tune".
        """
        try:
            import pandas as pd
        except ImportError:  # pragma: no cover - pandas is a hard dependency
            logger.warning("pandas is unavailable; cannot evaluate performance")
            return None

        try:
            frame = pd.read_csv(trade_log_path)
        except (FileNotFoundError, OSError) as exc:
            logger.info("No trade log at %s (%s); nothing to evaluate",
                        trade_log_path, type(exc).__name__)
            return None
        except Exception as exc:
            logger.warning("Could not read %s: %s", trade_log_path, exc)
            return None

        if "pnl" not in frame.columns or frame.empty:
            logger.warning("%s has no usable pnl column", trade_log_path)
            return None

        pnl = [float(v) for v in frame["pnl"].tolist()]
        return {
            "trades": len(pnl),
            "win_rate": sum(1 for v in pnl if v > 0) / len(pnl),
            "avg_pnl": sum(pnl) / len(pnl),
            "loss_streak": self._calc_streak(pnl, negative=True),
        }

    # -- suggestion -------------------------------------------------------

    def suggest(
        self, performance: Optional[Dict[str, Any]] = None
    ) -> TuningSuggestion:
        """Propose parameters. Only ever the same or tighter.

        Returns a suggestion, not an assignment: applying it is the
        registry's job, after shadow and out-of-sample evidence.
        """
        current = ParameterSet(
            {"min_confidence": self.base_confidence,
             "risk_per_trade": self.base_risk},
            label="current",
        )

        if performance is None:
            return TuningSuggestion(
                parameters=current, unchanged=True,
                reasons=["no performance record; nothing to conclude"],
            )

        trades = int(performance.get("trades", 0))
        if trades < MINIMUM_TRADES:
            return TuningSuggestion(
                parameters=current, unchanged=True,
                reasons=[
                    f"{trades} closed trade(s); {MINIMUM_TRADES} are needed "
                    "before a win rate is a measurement rather than a mood"
                ],
            )

        confidence = self.base_confidence
        risk = self.base_risk
        reasons: List[str] = []

        win_rate = float(performance.get("win_rate", 0.0))
        avg_pnl = float(performance.get("avg_pnl", 0.0))
        loss_streak = int(performance.get("loss_streak", 0))

        if win_rate < 0.45:
            confidence = min(0.95, confidence + 0.05)
            reasons.append(
                f"win rate {win_rate:.0%} is below 45%, so the confidence "
                "threshold rises"
            )
        if avg_pnl < 0:
            confidence = min(0.95, confidence + 0.02)
            risk = max(0.001, risk - 0.002)
            reasons.append(
                f"average PnL {avg_pnl:.2f} is negative, so trade less and "
                "smaller"
            )
        if loss_streak >= 3:
            confidence = min(0.95, confidence + 0.03)
            risk = max(0.001, risk - 0.005)
            reasons.append(
                f"{loss_streak} consecutive losses, so tighten further"
            )

        # Good performance is not a reason to do anything. It is stated,
        # rather than silently ignored, so a reader can see the asymmetry is
        # deliberate rather than an unfinished branch.
        if not reasons:
            return TuningSuggestion(
                parameters=current, unchanged=True,
                reasons=[
                    f"win rate {win_rate:.0%}, average PnL {avg_pnl:.2f}: "
                    "nothing to tighten. Good performance is not a reason to "
                    "loosen a limit - that decision needs out-of-sample "
                    "evidence and a human, not a recent run"
                ],
            )

        proposed = ParameterSet(
            {"min_confidence": round(confidence, 3),
             "risk_per_trade": round(risk, 4)},
            label="tightened",
        )
        return TuningSuggestion(parameters=proposed, reasons=reasons)

    # -- kept for the previous callers -------------------------------------

    def adjust_parameters(
        self, win_rate: float, avg_pnl: float, loss_streak: int,
        trades: int = MINIMUM_TRADES,
    ) -> Tuple[float, float]:
        """The old signature, with the loosening branches removed.

        Kept so existing callers keep working, and deliberately no longer able
        to return a risk above ``base_risk`` or a confidence below
        ``base_confidence``.
        """
        suggestion = self.suggest({
            "trades": trades, "win_rate": win_rate, "avg_pnl": avg_pnl,
            "loss_streak": loss_streak,
        })
        values = suggestion.parameters.values
        confidence = float(values["min_confidence"])
        risk = float(values["risk_per_trade"])

        # Belt and braces. The suggestion path cannot loosen, and this asserts
        # it at the boundary anyway, because this is the function a caller is
        # most likely to wire straight into a live config.
        assert confidence >= self.base_confidence
        assert risk <= self.base_risk
        return confidence, risk

    @staticmethod
    def _calc_streak(pnl_list: Sequence[float], negative: bool = True) -> int:
        streak = 0
        for pnl in reversed(list(pnl_list)):
            if (negative and pnl < 0) or (not negative and pnl > 0):
                streak += 1
            else:
                break
        return streak


if __name__ == "__main__":
    tuner = AutoTuner()
    result = tuner.suggest(tuner.evaluate_performance())
    print(f"[TUNER] {'unchanged' if result.unchanged else 'proposed'}: "
          f"{result.parameters.values}")
    for reason in result.reasons:
        print(f"  - {reason}")
