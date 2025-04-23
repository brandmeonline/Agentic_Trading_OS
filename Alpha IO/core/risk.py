class RiskManager:
    def __init__(self, capital=10000, max_risk_per_trade=0.015, max_drawdown=0.06):
        self.capital = capital
        self.max_risk_per_trade = max_risk_per_trade
        self.max_drawdown = max_drawdown
        self.daily_loss = 0
        self.pnl_history = []
        self.win_streak = 0
        self.loss_streak = 0
        self.asset_exposure = {}

    def get_position_size(self, asset, confidence):
        base = self.capital * self.max_risk_per_trade
        scale = 1.0 + (confidence - 0.7) * 2.5
        size = round(base * scale, 2)
        self.asset_exposure[asset] = self.asset_exposure.get(asset, 0) + size
        return size

    def update_after_trade(self, asset, pnl):
        self.pnl_history.append(pnl)
        self.daily_loss += min(pnl, 0)
        if pnl > 0:
            self.win_streak += 1
            self.loss_streak = 0
        elif pnl < 0:
            self.loss_streak += 1
            self.win_streak = 0

        if asset in self.asset_exposure:
            self.asset_exposure[asset] = max(0, self.asset_exposure[asset] - abs(pnl))

    def check_risk_limits(self, asset):
        if abs(self.daily_loss) >= self.capital * self.max_drawdown:
            print(f"[RISK] Max daily drawdown reached. Pausing trading.")
            return False
        if self.loss_streak >= 3:
            print(f"[RISK] Loss streak of {self.loss_streak} triggered. Pausing trading.")
            return False
        if self.asset_exposure.get(asset, 0) > self.capital * 0.25:
            print(f"[RISK] Exposure limit exceeded for {asset}.")
            return False
        return True

    def get_summary(self):
        return {
            "Daily Loss": round(self.daily_loss, 2),
            "Cumulative P&L": round(sum(self.pnl_history), 2),
            "Win Streak": self.win_streak,
            "Loss Streak": self.loss_streak,
            "Trades": len(self.pnl_history),
            "Exposure": self.asset_exposure
        }