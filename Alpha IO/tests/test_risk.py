"""
Unit tests for the Risk Management module.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.risk import (
    RiskManager, RiskConfig, RiskLevel, RiskEvent, RiskMetrics, Position
)
from datetime import datetime


class TestRiskConfig(unittest.TestCase):
    """Tests for RiskConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RiskConfig()
        self.assertEqual(config.initial_capital, 10000.0)
        self.assertEqual(config.max_risk_per_trade, 0.015)
        self.assertEqual(config.risk_level, RiskLevel.MODERATE)

    def test_custom_config(self):
        """Test custom configuration."""
        config = RiskConfig(
            initial_capital=50000,
            risk_level=RiskLevel.AGGRESSIVE
        )
        self.assertEqual(config.initial_capital, 50000)
        self.assertEqual(config.risk_level, RiskLevel.AGGRESSIVE)


class TestPosition(unittest.TestCase):
    """Tests for Position dataclass."""

    def test_unrealized_pnl(self):
        """Test unrealized P&L calculation."""
        pos = Position(
            asset="BTC",
            size=1.0,
            entry_price=100.0,
            current_price=110.0,
            entry_time=datetime.now()
        )
        self.assertEqual(pos.unrealized_pnl, 10.0)

    def test_unrealized_pnl_pct(self):
        """Test unrealized P&L percentage calculation."""
        pos = Position(
            asset="BTC",
            size=1.0,
            entry_price=100.0,
            current_price=110.0,
            entry_time=datetime.now()
        )
        self.assertEqual(pos.unrealized_pnl_pct, 0.1)

    def test_market_value(self):
        """Test market value calculation."""
        pos = Position(
            asset="BTC",
            size=2.0,
            entry_price=100.0,
            current_price=150.0,
            entry_time=datetime.now()
        )
        self.assertEqual(pos.market_value, 300.0)


class TestRiskManager(unittest.TestCase):
    """Tests for RiskManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.risk = RiskManager(RiskConfig(initial_capital=10000))

    def test_initialization(self):
        """Test risk manager initializes correctly."""
        self.assertEqual(self.risk.capital, 10000)
        self.assertEqual(self.risk.peak_capital, 10000)
        self.assertEqual(self.risk.trade_count, 0)

    def test_get_position_size(self):
        """Test position sizing."""
        size = self.risk.get_position_size("BTC", 0.8)
        self.assertGreater(size, 0)
        self.assertLess(size, self.risk.capital)

    def test_position_size_confidence_scaling(self):
        """Test that higher confidence leads to larger positions."""
        size_low = self.risk.get_position_size("BTC", 0.6)
        # Reset exposure for fair comparison
        self.risk.asset_exposure = {}
        size_high = self.risk.get_position_size("BTC", 0.9)

        self.assertGreater(size_high, size_low)

    def test_exposure_limits(self):
        """Test that exposure limits are enforced."""
        # Try to get very large position
        total_size = 0
        for _ in range(20):
            size = self.risk.get_position_size("BTC", 0.95)
            total_size += size

        # Should be capped at concentration limit
        max_exposure = self.risk.capital * self.risk.config.max_position_concentration
        self.assertLessEqual(self.risk.asset_exposure.get("BTC", 0), max_exposure)

    def test_update_after_trade_win(self):
        """Test update after winning trade."""
        self.risk.update_after_trade("BTC", 100)
        self.assertEqual(self.risk.total_pnl, 100)
        self.assertEqual(self.risk.capital, 10100)
        self.assertEqual(self.risk.win_streak, 1)
        self.assertEqual(self.risk.win_count, 1)

    def test_update_after_trade_loss(self):
        """Test update after losing trade."""
        self.risk.update_after_trade("BTC", -50)
        self.assertEqual(self.risk.total_pnl, -50)
        self.assertEqual(self.risk.capital, 9950)
        self.assertEqual(self.risk.loss_streak, 1)
        self.assertEqual(self.risk.loss_count, 1)

    def test_check_risk_limits_normal(self):
        """Test risk limits under normal conditions."""
        self.assertTrue(self.risk.check_risk_limits("BTC"))

    def test_check_risk_limits_loss_streak(self):
        """Test risk limits after loss streak."""
        for _ in range(3):
            self.risk.update_after_trade("BTC", -100)

        self.assertFalse(self.risk.check_risk_limits("BTC"))

    def test_get_current_drawdown(self):
        """Test drawdown calculation."""
        self.risk.update_after_trade("BTC", 1000)  # Peak at 11000
        self.risk.update_after_trade("BTC", -500)  # Now at 10500

        dd = self.risk.get_current_drawdown()
        expected_dd = (11000 - 10500) / 11000
        self.assertAlmostEqual(dd, expected_dd, places=4)

    def test_get_max_drawdown(self):
        """Test max drawdown calculation."""
        trades = [100, -200, 50, -100, 300, -150]
        for pnl in trades:
            self.risk.update_after_trade("BTC", pnl)

        max_dd = self.risk.get_max_drawdown()
        self.assertGreater(max_dd, 0)

    def test_calculate_metrics(self):
        """Test metrics calculation."""
        trades = [100, -50, 75, -25, 150, -60, 80]
        for pnl in trades:
            self.risk.update_after_trade("BTC", pnl)

        metrics = self.risk.calculate_metrics()
        self.assertIsInstance(metrics, RiskMetrics)
        self.assertGreater(metrics.win_rate, 0)

    def test_risk_levels(self):
        """Test different risk levels affect sizing."""
        configs = [
            RiskConfig(risk_level=RiskLevel.CONSERVATIVE),
            RiskConfig(risk_level=RiskLevel.MODERATE),
            RiskConfig(risk_level=RiskLevel.AGGRESSIVE),
        ]

        sizes = []
        for config in configs:
            rm = RiskManager(config)
            size = rm.get_position_size("BTC", 0.8)
            sizes.append(size)

        # Aggressive should be larger than conservative
        self.assertGreater(sizes[2], sizes[0])

    def test_drawdown_scalar(self):
        """Test that position size reduces during drawdown."""
        # Create drawdown
        self.risk.peak_capital = 12000
        self.risk.capital = 10000  # 16.7% drawdown

        scalar = self.risk._get_drawdown_scalar()
        self.assertLess(scalar, 1.0)

    def test_streak_scalar(self):
        """Test streak-based position adjustment."""
        # Create win streak
        for _ in range(3):
            self.risk.update_after_trade("BTC", 100)

        scalar = self.risk._get_streak_scalar()
        self.assertEqual(scalar, 1.1)  # Hot streak bonus

        # Create loss streak
        risk2 = RiskManager()
        for _ in range(2):
            risk2.update_after_trade("BTC", -100)

        scalar2 = risk2._get_streak_scalar()
        self.assertEqual(scalar2, 0.7)  # Cold streak reduction

    def test_summary(self):
        """Test summary generation."""
        self.risk.update_after_trade("BTC", 100)
        summary = self.risk.get_summary()

        # Check backward compatibility keys
        self.assertIn("Daily Loss", summary)
        self.assertIn("Cumulative P&L", summary)
        self.assertIn("Win Streak", summary)
        self.assertIn("Exposure", summary)

        # Check new keys
        self.assertIn("sharpe_ratio", summary)
        self.assertIn("win_rate", summary)

    def test_position_tracking(self):
        """Test position tracking."""
        pos = Position(
            asset="ETH",
            size=10,
            entry_price=2000,
            current_price=2100,
            entry_time=datetime.now()
        )

        self.risk.set_position("ETH", pos)
        self.assertIn("ETH", self.risk.positions)

        closed = self.risk.close_position("ETH")
        self.assertEqual(closed, pos)
        self.assertNotIn("ETH", self.risk.positions)


class TestBackwardCompatibility(unittest.TestCase):
    """Tests to ensure backward compatibility."""

    def test_legacy_interface(self):
        """Test that legacy interface still works."""
        risk = RiskManager()

        # Legacy position size call
        size = risk.get_position_size("BTC", 0.8)
        self.assertGreater(size, 0)

        # Legacy update call
        risk.update_after_trade("BTC", 100)

        # Legacy check limits call
        result = risk.check_risk_limits("BTC")
        self.assertIsInstance(result, bool)

        # Legacy summary
        summary = risk.get_summary()
        self.assertIn("Daily Loss", summary)
        self.assertIn("Cumulative P&L", summary)

    def test_legacy_attributes(self):
        """Test legacy attributes exist."""
        risk = RiskManager()
        self.assertTrue(hasattr(risk, "max_risk_per_trade"))
        self.assertTrue(hasattr(risk, "max_drawdown"))
        self.assertTrue(hasattr(risk, "daily_loss"))
        self.assertTrue(hasattr(risk, "pnl_history"))
        self.assertTrue(hasattr(risk, "asset_exposure"))


if __name__ == "__main__":
    unittest.main()
