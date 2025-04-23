# auto_tuner.py – dynamically adjust thresholds based on performance and market state

import pandas as pd

class AutoTuner:
    def __init__(self, base_confidence=0.7, base_risk=0.015):
        self.base_confidence = base_confidence
        self.base_risk = base_risk

    def evaluate_performance(self, trade_log_path="data/trade_log.csv"):
        try:
            df = pd.read_csv(trade_log_path)
            win_rate = (df["pnl"] > 0).mean()
            avg_pnl = df["pnl"].mean()
            loss_streak = self._calc_streak(df["pnl"].tolist(), negative=True)
            return win_rate, avg_pnl, loss_streak
        except FileNotFoundError:
            return 0.5, 0, 0

    def adjust_parameters(self, win_rate, avg_pnl, loss_streak):
        confidence = self.base_confidence
        risk = self.base_risk

        if win_rate > 0.65:
            confidence -= 0.05
            risk += 0.005
        elif win_rate < 0.45:
            confidence += 0.05
            risk -= 0.005

        if avg_pnl > 0:
            risk += 0.002
        elif avg_pnl < 0:
            confidence += 0.02

        if loss_streak >= 3:
            confidence += 0.03
            risk = max(risk - 0.005, 0.01)

        return round(confidence, 3), round(risk, 4)

    def _calc_streak(self, pnl_list, negative=True):
        streak = 0
        for pnl in reversed(pnl_list):
            if (negative and pnl < 0) or (not negative and pnl > 0):
                streak += 1
            else:
                break
        return streak

if __name__ == "__main__":
    tuner = AutoTuner()
    win_rate, avg_pnl, loss_streak = tuner.evaluate_performance()
    new_conf, new_risk = tuner.adjust_parameters(win_rate, avg_pnl, loss_streak)
    print(f"[TUNER] Adjusted Confidence: {new_conf}, Adjusted Risk per Trade: {new_risk}")