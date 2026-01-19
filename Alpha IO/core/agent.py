"""
Advanced Reinforcement Learning Trading Agent.

Implements multiple RL strategies with adaptive learning and sophisticated
state representation for optimal trading decisions.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import random
from collections import deque


class LearningAlgorithm(Enum):
    """Available learning algorithms."""
    Q_LEARNING = "q_learning"
    SARSA = "sarsa"
    DOUBLE_Q = "double_q"
    EXPECTED_SARSA = "expected_sarsa"


@dataclass
class AgentConfig:
    """Configuration for the trading agent."""
    learning_rate: float = 0.1
    discount_factor: float = 0.95
    epsilon: float = 0.1  # Exploration rate
    epsilon_decay: float = 0.995
    epsilon_min: float = 0.01
    momentum_window: int = 10
    confidence_bins: List[float] = field(default_factory=lambda: [0.5, 0.6, 0.7, 0.8, 0.9])
    algorithm: LearningAlgorithm = LearningAlgorithm.Q_LEARNING


@dataclass
class MarketState:
    """Represents the current market state for the agent."""
    confidence_bin: float
    momentum_trend: int  # -1, 0, 1
    volatility_regime: int  # 0=low, 1=medium, 2=high
    streak_type: int  # -1=loss, 0=neutral, 1=win

    def to_tuple(self) -> Tuple:
        """Convert to hashable tuple for Q-table lookup."""
        return (self.confidence_bin, self.momentum_trend, self.volatility_regime, self.streak_type)


class TradingAgent:
    """
    Advanced RL-based trading agent with multiple learning algorithms.

    Features:
    - Q-learning, SARSA, Double Q-learning, Expected SARSA
    - Adaptive exploration with epsilon decay
    - Rich state representation including market regime
    - Experience replay for stability
    - Momentum and streak tracking
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        self.q_table: Dict[Tuple, Dict[int, float]] = {}  # state -> {action -> value}
        self.q_table_2: Dict[Tuple, Dict[int, float]] = {}  # For Double Q-learning

        # Actions: 0 = hold, 1 = trade
        self.actions = [0, 1]

        # State tracking
        self.momentum: deque = deque(maxlen=self.config.momentum_window)
        self.volatility_history: deque = deque(maxlen=20)
        self.win_streak = 0
        self.loss_streak = 0

        # Learning metrics
        self.epsilon = self.config.epsilon
        self.total_reward = 0.0
        self.episode_rewards: List[float] = []
        self.trade_count = 0

        # Experience replay buffer
        self.replay_buffer: deque = deque(maxlen=1000)

        # Backward compatibility
        self.confidence_bins = self.config.confidence_bins

    def _bin_confidence(self, confidence: float) -> float:
        """Discretize confidence into bins."""
        for b in reversed(self.config.confidence_bins):
            if confidence >= b:
                return b
        return self.config.confidence_bins[0]

    def _get_momentum_trend(self) -> int:
        """Calculate momentum trend from recent rewards."""
        if len(self.momentum) < 3:
            return 0
        recent = list(self.momentum)[-3:]
        avg = sum(recent) / len(recent)
        if avg > 0.5:
            return 1
        elif avg < -0.5:
            return -1
        return 0

    def _get_volatility_regime(self) -> int:
        """Estimate current volatility regime."""
        if len(self.volatility_history) < 5:
            return 1  # Medium by default
        std = np.std(list(self.volatility_history))
        if std < 0.3:
            return 0  # Low
        elif std > 0.7:
            return 2  # High
        return 1  # Medium

    def _get_streak_type(self) -> int:
        """Get current streak type."""
        if self.win_streak >= 2:
            return 1
        elif self.loss_streak >= 2:
            return -1
        return 0

    def _build_state(self, confidence: float) -> MarketState:
        """Build comprehensive state representation."""
        return MarketState(
            confidence_bin=self._bin_confidence(confidence),
            momentum_trend=self._get_momentum_trend(),
            volatility_regime=self._get_volatility_regime(),
            streak_type=self._get_streak_type()
        )

    def _get_q_value(self, state: Tuple, action: int, use_table_2: bool = False) -> float:
        """Get Q-value for state-action pair."""
        table = self.q_table_2 if use_table_2 else self.q_table
        if state not in table:
            table[state] = {a: 0.0 for a in self.actions}
        return table[state].get(action, 0.0)

    def _set_q_value(self, state: Tuple, action: int, value: float, use_table_2: bool = False) -> None:
        """Set Q-value for state-action pair."""
        table = self.q_table_2 if use_table_2 else self.q_table
        if state not in table:
            table[state] = {a: 0.0 for a in self.actions}
        table[state][action] = value

    def _select_action(self, state: Tuple, training: bool = True) -> int:
        """Select action using epsilon-greedy policy."""
        if training and random.random() < self.epsilon:
            return random.choice(self.actions)

        # Greedy action selection
        q_values = {a: self._get_q_value(state, a) for a in self.actions}
        return max(q_values, key=q_values.get)

    def _update_q_learning(self, state: Tuple, action: int, reward: float, next_state: Tuple) -> None:
        """Standard Q-learning update."""
        current_q = self._get_q_value(state, action)
        max_next_q = max(self._get_q_value(next_state, a) for a in self.actions)
        new_q = current_q + self.config.learning_rate * (
            reward + self.config.discount_factor * max_next_q - current_q
        )
        self._set_q_value(state, action, new_q)

    def _update_sarsa(self, state: Tuple, action: int, reward: float,
                      next_state: Tuple, next_action: int) -> None:
        """SARSA update (on-policy)."""
        current_q = self._get_q_value(state, action)
        next_q = self._get_q_value(next_state, next_action)
        new_q = current_q + self.config.learning_rate * (
            reward + self.config.discount_factor * next_q - current_q
        )
        self._set_q_value(state, action, new_q)

    def _update_double_q(self, state: Tuple, action: int, reward: float, next_state: Tuple) -> None:
        """Double Q-learning to reduce overestimation bias."""
        if random.random() < 0.5:
            # Update Q1
            best_action = max(self.actions, key=lambda a: self._get_q_value(next_state, a, use_table_2=False))
            next_q = self._get_q_value(next_state, best_action, use_table_2=True)
            current_q = self._get_q_value(state, action, use_table_2=False)
            new_q = current_q + self.config.learning_rate * (
                reward + self.config.discount_factor * next_q - current_q
            )
            self._set_q_value(state, action, new_q, use_table_2=False)
        else:
            # Update Q2
            best_action = max(self.actions, key=lambda a: self._get_q_value(next_state, a, use_table_2=True))
            next_q = self._get_q_value(next_state, best_action, use_table_2=False)
            current_q = self._get_q_value(state, action, use_table_2=True)
            new_q = current_q + self.config.learning_rate * (
                reward + self.config.discount_factor * next_q - current_q
            )
            self._set_q_value(state, action, new_q, use_table_2=True)

    def _update_expected_sarsa(self, state: Tuple, action: int, reward: float, next_state: Tuple) -> None:
        """Expected SARSA update."""
        current_q = self._get_q_value(state, action)

        # Calculate expected value of next state
        q_values = [self._get_q_value(next_state, a) for a in self.actions]
        max_q = max(q_values)
        n_actions = len(self.actions)

        # Expected value under epsilon-greedy policy
        expected_q = (1 - self.epsilon) * max_q + (self.epsilon / n_actions) * sum(q_values)

        new_q = current_q + self.config.learning_rate * (
            reward + self.config.discount_factor * expected_q - current_q
        )
        self._set_q_value(state, action, new_q)

    def update(self, confidence: float, reward: float, next_confidence: Optional[float] = None) -> None:
        """
        Update agent after receiving reward.

        Args:
            confidence: Confidence level when trade was made
            reward: Reward received (P&L normalized)
            next_confidence: Optional next state confidence
        """
        state = self._build_state(confidence)
        state_tuple = state.to_tuple()

        # Determine action taken (1 if traded based on confidence threshold)
        action = 1 if confidence >= 0.7 else 0

        # Build next state
        next_confidence = next_confidence or confidence
        next_state = self._build_state(next_confidence)
        next_state_tuple = next_state.to_tuple()

        # Store experience
        self.replay_buffer.append((state_tuple, action, reward, next_state_tuple))

        # Update based on algorithm
        if self.config.algorithm == LearningAlgorithm.Q_LEARNING:
            self._update_q_learning(state_tuple, action, reward, next_state_tuple)
        elif self.config.algorithm == LearningAlgorithm.SARSA:
            next_action = self._select_action(next_state_tuple)
            self._update_sarsa(state_tuple, action, reward, next_state_tuple, next_action)
        elif self.config.algorithm == LearningAlgorithm.DOUBLE_Q:
            self._update_double_q(state_tuple, action, reward, next_state_tuple)
        elif self.config.algorithm == LearningAlgorithm.EXPECTED_SARSA:
            self._update_expected_sarsa(state_tuple, action, reward, next_state_tuple)

        # Update tracking
        self.momentum.append(reward)
        self.volatility_history.append(abs(reward))
        self.total_reward += reward
        self.trade_count += 1

        # Update streaks
        if reward > 0:
            self.win_streak += 1
            self.loss_streak = 0
        elif reward < 0:
            self.loss_streak += 1
            self.win_streak = 0

        # Decay epsilon
        self.epsilon = max(self.config.epsilon_min, self.epsilon * self.config.epsilon_decay)

    def decide(self, confidence: float, training: bool = False) -> bool:
        """
        Decide whether to trade based on current confidence.

        Args:
            confidence: Current signal confidence
            training: Whether in training mode (enables exploration)

        Returns:
            True if should trade, False otherwise
        """
        state = self._build_state(confidence)
        state_tuple = state.to_tuple()
        action = self._select_action(state_tuple, training=training)

        # Add streak and momentum bonuses for non-training decisions
        if not training:
            q_value = self._get_q_value(state_tuple, 1)  # Q-value for trading
            streak_bonus = 0.05 if self.win_streak >= 2 else 0
            momentum_bonus = 0.03 if self._get_momentum_trend() > 0 else 0
            adjusted_value = q_value + streak_bonus + momentum_bonus

            # More conservative when on loss streak
            if self.loss_streak >= 2:
                adjusted_value -= 0.1

            return adjusted_value > 0 and action == 1

        return action == 1

    def replay_experience(self, batch_size: int = 32) -> None:
        """Perform experience replay for stability."""
        if len(self.replay_buffer) < batch_size:
            return

        batch = random.sample(list(self.replay_buffer), batch_size)
        for state, action, reward, next_state in batch:
            self._update_q_learning(state, action, reward, next_state)

    def get_policy_confidence(self, confidence: float) -> float:
        """Get the agent's confidence in trading at this level."""
        state = self._build_state(confidence)
        state_tuple = state.to_tuple()
        q_trade = self._get_q_value(state_tuple, 1)
        q_hold = self._get_q_value(state_tuple, 0)

        # Softmax-style confidence
        if q_trade == q_hold == 0:
            return 0.5
        total = abs(q_trade) + abs(q_hold)
        if total == 0:
            return 0.5
        return (q_trade + abs(min(q_trade, q_hold))) / (total + 2 * abs(min(q_trade, q_hold)))

    def summary(self) -> Dict:
        """Get comprehensive agent summary."""
        return {
            "algorithm": self.config.algorithm.value,
            "Q-Table": dict(self.q_table),  # Backward compatibility
            "Momentum": list(self.momentum),  # Backward compatibility
            "q_table_size": len(self.q_table),
            "epsilon": round(self.epsilon, 4),
            "total_reward": round(self.total_reward, 2),
            "trade_count": self.trade_count,
            "win_streak": self.win_streak,
            "loss_streak": self.loss_streak,
            "momentum_trend": self._get_momentum_trend(),
            "volatility_regime": self._get_volatility_regime(),
            "replay_buffer_size": len(self.replay_buffer),
        }

    def save_state(self) -> Dict:
        """Save agent state for persistence."""
        return {
            "q_table": {str(k): v for k, v in self.q_table.items()},
            "q_table_2": {str(k): v for k, v in self.q_table_2.items()},
            "epsilon": self.epsilon,
            "total_reward": self.total_reward,
            "trade_count": self.trade_count,
            "momentum": list(self.momentum),
        }

    def load_state(self, state: Dict) -> None:
        """Load agent state from saved data."""
        self.q_table = {eval(k): v for k, v in state.get("q_table", {}).items()}
        self.q_table_2 = {eval(k): v for k, v in state.get("q_table_2", {}).items()}
        self.epsilon = state.get("epsilon", self.config.epsilon)
        self.total_reward = state.get("total_reward", 0.0)
        self.trade_count = state.get("trade_count", 0)
        self.momentum = deque(state.get("momentum", []), maxlen=self.config.momentum_window)


if __name__ == "__main__":
    import pandas as pd

    # Test the enhanced agent
    agent = TradingAgent(AgentConfig(algorithm=LearningAlgorithm.DOUBLE_Q))

    # Simulate some trades
    test_trades = [
        (0.85, 1.5), (0.72, -0.8), (0.91, 2.1), (0.68, 0.3),
        (0.78, -1.2), (0.82, 1.8), (0.75, 0.5), (0.88, 2.5),
    ]

    for conf, reward in test_trades:
        should_trade = agent.decide(conf, training=True)
        agent.update(conf, reward)
        print(f"Conf: {conf:.2f}, Reward: {reward:+.1f}, Trade: {should_trade}")

    print("\nAgent Summary:", agent.summary())

    # Backward compatibility test
    try:
        trades = pd.read_csv("data/trade_log.csv")
        legacy_agent = TradingAgent()
        for _, row in trades.iterrows():
            legacy_agent.update(row["confidence"], row["reward"])
        print("Legacy Agent Summary:", legacy_agent.summary())
    except FileNotFoundError:
        print("No trade log found. Run backtest to generate signals.")
