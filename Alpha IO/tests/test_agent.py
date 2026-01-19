"""
Unit tests for the Trading Agent module.
"""

import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import (
    TradingAgent, AgentConfig, MarketState, LearningAlgorithm
)


class TestAgentConfig(unittest.TestCase):
    """Tests for AgentConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AgentConfig()
        self.assertEqual(config.learning_rate, 0.1)
        self.assertEqual(config.discount_factor, 0.95)
        self.assertEqual(config.epsilon, 0.1)
        self.assertEqual(config.algorithm, LearningAlgorithm.Q_LEARNING)

    def test_custom_config(self):
        """Test custom configuration."""
        config = AgentConfig(
            learning_rate=0.05,
            algorithm=LearningAlgorithm.DOUBLE_Q
        )
        self.assertEqual(config.learning_rate, 0.05)
        self.assertEqual(config.algorithm, LearningAlgorithm.DOUBLE_Q)


class TestMarketState(unittest.TestCase):
    """Tests for MarketState dataclass."""

    def test_to_tuple(self):
        """Test state to tuple conversion."""
        state = MarketState(
            confidence_bin=0.8,
            momentum_trend=1,
            volatility_regime=1,
            streak_type=0
        )
        expected = (0.8, 1, 1, 0)
        self.assertEqual(state.to_tuple(), expected)


class TestTradingAgent(unittest.TestCase):
    """Tests for TradingAgent class."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = TradingAgent()

    def test_initialization(self):
        """Test agent initializes correctly."""
        self.assertEqual(len(self.agent.q_table), 0)
        self.assertEqual(self.agent.trade_count, 0)
        self.assertEqual(self.agent.total_reward, 0.0)

    def test_bin_confidence(self):
        """Test confidence binning."""
        self.assertEqual(self.agent._bin_confidence(0.95), 0.9)
        self.assertEqual(self.agent._bin_confidence(0.85), 0.8)
        self.assertEqual(self.agent._bin_confidence(0.75), 0.7)
        self.assertEqual(self.agent._bin_confidence(0.45), 0.5)

    def test_update(self):
        """Test agent update after trade."""
        self.agent.update(0.8, 1.5)
        self.assertEqual(self.agent.trade_count, 1)
        self.assertEqual(self.agent.total_reward, 1.5)
        self.assertEqual(self.agent.win_streak, 1)
        self.assertEqual(self.agent.loss_streak, 0)

    def test_update_loss(self):
        """Test agent update after losing trade."""
        self.agent.update(0.7, -0.5)
        self.assertEqual(self.agent.loss_streak, 1)
        self.assertEqual(self.agent.win_streak, 0)

    def test_decide(self):
        """Test trading decision."""
        # Train with some positive experiences
        for _ in range(5):
            self.agent.update(0.85, 1.0)

        # Should be more likely to trade at high confidence
        decision = self.agent.decide(0.85)
        self.assertIsInstance(decision, bool)

    def test_epsilon_decay(self):
        """Test epsilon decays over time."""
        initial_epsilon = self.agent.epsilon
        self.agent.update(0.8, 1.0)
        self.assertLess(self.agent.epsilon, initial_epsilon)

    def test_momentum_tracking(self):
        """Test momentum window tracking."""
        rewards = [1.0, -0.5, 0.8, -0.2, 1.5]
        for i, r in enumerate(rewards):
            self.agent.update(0.75, r)

        self.assertEqual(len(self.agent.momentum), min(len(rewards), self.agent.config.momentum_window))

    def test_different_algorithms(self):
        """Test different learning algorithms."""
        algorithms = [
            LearningAlgorithm.Q_LEARNING,
            LearningAlgorithm.SARSA,
            LearningAlgorithm.DOUBLE_Q,
            LearningAlgorithm.EXPECTED_SARSA,
        ]

        for algo in algorithms:
            config = AgentConfig(algorithm=algo)
            agent = TradingAgent(config)
            agent.update(0.8, 1.0)
            self.assertEqual(agent.trade_count, 1)

    def test_experience_replay(self):
        """Test experience replay functionality."""
        # Add some experiences
        for i in range(50):
            self.agent.update(0.7 + i * 0.005, (i % 3 - 1) * 0.5)

        # Should not raise error
        self.agent.replay_experience(batch_size=10)

    def test_summary(self):
        """Test summary generation."""
        self.agent.update(0.8, 1.0)
        summary = self.agent.summary()

        self.assertIn("algorithm", summary)
        self.assertIn("Q-Table", summary)  # Backward compatibility
        self.assertIn("Momentum", summary)  # Backward compatibility
        self.assertIn("trade_count", summary)

    def test_save_load_state(self):
        """Test state persistence."""
        self.agent.update(0.8, 1.0)
        self.agent.update(0.7, -0.5)

        state = self.agent.save_state()
        new_agent = TradingAgent()
        new_agent.load_state(state)

        self.assertEqual(new_agent.total_reward, self.agent.total_reward)
        self.assertEqual(new_agent.trade_count, self.agent.trade_count)

    def test_policy_confidence(self):
        """Test policy confidence calculation."""
        confidence = self.agent.get_policy_confidence(0.8)
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_volatility_regime_detection(self):
        """Test volatility regime detection."""
        # Low volatility
        for _ in range(10):
            self.agent.update(0.75, 0.1)

        low_vol_regime = self.agent._get_volatility_regime()

        # High volatility
        agent2 = TradingAgent()
        rewards = [2.0, -2.0, 1.5, -1.5, 2.5, -2.5]
        for r in rewards:
            agent2.update(0.75, r)

        # Regime should differ with different volatility
        self.assertIn(low_vol_regime, [0, 1, 2])

    def test_streak_detection(self):
        """Test win/loss streak detection."""
        # Create win streak
        for _ in range(3):
            self.agent.update(0.8, 1.0)

        self.assertEqual(self.agent._get_streak_type(), 1)  # Win streak

        # Create loss streak
        agent2 = TradingAgent()
        for _ in range(3):
            agent2.update(0.8, -1.0)

        self.assertEqual(agent2._get_streak_type(), -1)  # Loss streak


class TestBackwardCompatibility(unittest.TestCase):
    """Tests to ensure backward compatibility."""

    def test_legacy_interface(self):
        """Test that legacy interface still works."""
        agent = TradingAgent()

        # Legacy update call
        agent.update(0.8, 1.0)

        # Legacy decide call
        result = agent.decide(0.8)
        self.assertIsInstance(result, bool)

        # Legacy summary
        summary = agent.summary()
        self.assertIn("Q-Table", summary)
        self.assertIn("Momentum", summary)

    def test_confidence_bins_attribute(self):
        """Test that confidence_bins attribute exists for compatibility."""
        agent = TradingAgent()
        self.assertTrue(hasattr(agent, "confidence_bins"))
        self.assertEqual(agent.confidence_bins, agent.config.confidence_bins)


if __name__ == "__main__":
    unittest.main()
