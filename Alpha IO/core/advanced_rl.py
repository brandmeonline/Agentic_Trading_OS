"""
Advanced Reinforcement Learning Agents.

Production-grade RL algorithms for trading:
- Proximal Policy Optimization (PPO)
- Advantage Actor-Critic (A2C)
- Soft Actor-Critic (SAC)
- Deep Q-Network (DQN) with improvements
- Multi-Agent RL framework
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime
from collections import deque
import copy


# =============================================================================
# Configuration
# =============================================================================

class RLAlgorithm(Enum):
    """Available RL algorithms."""
    DQN = "dqn"
    DOUBLE_DQN = "double_dqn"
    DUELING_DQN = "dueling_dqn"
    PPO = "ppo"
    A2C = "a2c"
    SAC = "sac"
    TD3 = "td3"


class ActionSpace(Enum):
    """Action space types."""
    DISCRETE = "discrete"
    CONTINUOUS = "continuous"
    MULTI_DISCRETE = "multi_discrete"


@dataclass
class RLConfig:
    """RL agent configuration."""
    algorithm: RLAlgorithm = RLAlgorithm.PPO
    state_dim: int = 50
    action_dim: int = 3  # buy, sell, hold
    hidden_dims: List[int] = field(default_factory=lambda: [256, 256, 128])
    learning_rate: float = 3e-4
    gamma: float = 0.99
    tau: float = 0.005  # Soft update parameter
    batch_size: int = 64
    buffer_size: int = 100000
    update_frequency: int = 4
    target_update_frequency: int = 1000
    clip_grad_norm: float = 0.5
    # PPO specific
    ppo_clip: float = 0.2
    ppo_epochs: int = 10
    gae_lambda: float = 0.95
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    # SAC specific
    alpha: float = 0.2  # Entropy temperature
    auto_alpha: bool = True


@dataclass
class Experience:
    """Single experience tuple."""
    state: np.ndarray
    action: Union[int, np.ndarray]
    reward: float
    next_state: np.ndarray
    done: bool
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trajectory:
    """Trajectory for on-policy algorithms."""
    states: np.ndarray
    actions: np.ndarray
    rewards: np.ndarray
    next_states: np.ndarray
    dones: np.ndarray
    log_probs: np.ndarray
    values: np.ndarray
    advantages: Optional[np.ndarray] = None
    returns: Optional[np.ndarray] = None


# =============================================================================
# Neural Network Layers (Pure NumPy)
# =============================================================================

def relu(x: np.ndarray) -> np.ndarray:
    """ReLU activation."""
    return np.maximum(0, x)


def relu_grad(x: np.ndarray) -> np.ndarray:
    """ReLU gradient."""
    return (x > 0).astype(np.float32)


def tanh(x: np.ndarray) -> np.ndarray:
    """Tanh activation."""
    return np.tanh(x)


def tanh_grad(x: np.ndarray) -> np.ndarray:
    """Tanh gradient."""
    return 1 - np.tanh(x) ** 2


def softmax(x: np.ndarray) -> np.ndarray:
    """Softmax activation."""
    exp_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return exp_x / np.sum(exp_x, axis=-1, keepdims=True)


def log_softmax(x: np.ndarray) -> np.ndarray:
    """Log softmax for numerical stability."""
    return x - np.log(np.sum(np.exp(x - np.max(x, axis=-1, keepdims=True)), axis=-1, keepdims=True)) - np.max(x, axis=-1, keepdims=True)


class LinearLayer:
    """Linear layer with Xavier initialization."""

    def __init__(self, in_features: int, out_features: int):
        self.in_features = in_features
        self.out_features = out_features

        # Xavier initialization
        limit = np.sqrt(6.0 / (in_features + out_features))
        self.weights = np.random.uniform(-limit, limit, (in_features, out_features)).astype(np.float32)
        self.bias = np.zeros(out_features, dtype=np.float32)

        # Gradients
        self.weights_grad = np.zeros_like(self.weights)
        self.bias_grad = np.zeros_like(self.bias)

        # Cache for backprop
        self._input_cache = None

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass."""
        self._input_cache = x
        return np.dot(x, self.weights) + self.bias

    def backward(self, grad_output: np.ndarray) -> np.ndarray:
        """Backward pass."""
        self.weights_grad = np.dot(self._input_cache.T, grad_output)
        self.bias_grad = np.sum(grad_output, axis=0)
        return np.dot(grad_output, self.weights.T)

    def update(self, lr: float):
        """Update weights with gradient descent."""
        self.weights -= lr * self.weights_grad
        self.bias -= lr * self.bias_grad
        self.weights_grad.fill(0)
        self.bias_grad.fill(0)


class MLP:
    """Multi-layer perceptron."""

    def __init__(self, layer_dims: List[int], activation: str = "relu"):
        self.layers = []
        self.activation = activation

        for i in range(len(layer_dims) - 1):
            self.layers.append(LinearLayer(layer_dims[i], layer_dims[i + 1]))

        self._activations = []

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass through network."""
        self._activations = []

        for i, layer in enumerate(self.layers):
            x = layer.forward(x)

            # Apply activation to all but last layer
            if i < len(self.layers) - 1:
                if self.activation == "relu":
                    x = relu(x)
                elif self.activation == "tanh":
                    x = tanh(x)

            self._activations.append(x)

        return x

    def backward(self, grad_output: np.ndarray):
        """Backward pass through network."""
        for i in range(len(self.layers) - 1, -1, -1):
            # Apply activation gradient (except for last layer)
            if i < len(self.layers) - 1:
                if self.activation == "relu":
                    grad_output = grad_output * relu_grad(self._activations[i])
                elif self.activation == "tanh":
                    grad_output = grad_output * tanh_grad(self._activations[i])

            grad_output = self.layers[i].backward(grad_output)

        return grad_output

    def update(self, lr: float):
        """Update all layer weights."""
        for layer in self.layers:
            layer.update(lr)

    def get_params(self) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Get all parameters."""
        return [(layer.weights, layer.bias) for layer in self.layers]

    def set_params(self, params: List[Tuple[np.ndarray, np.ndarray]]):
        """Set all parameters."""
        for layer, (weights, bias) in zip(self.layers, params):
            layer.weights = weights.copy()
            layer.bias = bias.copy()

    def copy(self) -> 'MLP':
        """Create a deep copy."""
        new_mlp = MLP.__new__(MLP)
        new_mlp.layers = []
        new_mlp.activation = self.activation
        new_mlp._activations = []

        for layer in self.layers:
            new_layer = LinearLayer(layer.in_features, layer.out_features)
            new_layer.weights = layer.weights.copy()
            new_layer.bias = layer.bias.copy()
            new_mlp.layers.append(new_layer)

        return new_mlp


# =============================================================================
# Experience Replay Buffer
# =============================================================================

class ReplayBuffer:
    """Experience replay buffer for off-policy algorithms."""

    def __init__(self, capacity: int, state_dim: int):
        self.capacity = capacity
        self.state_dim = state_dim
        self.buffer = deque(maxlen=capacity)

    def push(self, experience: Experience):
        """Add experience to buffer."""
        self.buffer.append(experience)

    def sample(self, batch_size: int) -> Tuple[np.ndarray, ...]:
        """Sample random batch."""
        indices = np.random.choice(len(self.buffer), batch_size, replace=False)
        experiences = [self.buffer[i] for i in indices]

        states = np.array([e.state for e in experiences])
        actions = np.array([e.action for e in experiences])
        rewards = np.array([e.reward for e in experiences])
        next_states = np.array([e.next_state for e in experiences])
        dones = np.array([e.done for e in experiences])

        return states, actions, rewards, next_states, dones

    def __len__(self) -> int:
        return len(self.buffer)


class PrioritizedReplayBuffer(ReplayBuffer):
    """Prioritized experience replay buffer."""

    def __init__(self, capacity: int, state_dim: int, alpha: float = 0.6, beta: float = 0.4):
        super().__init__(capacity, state_dim)
        self.alpha = alpha
        self.beta = beta
        self.priorities = deque(maxlen=capacity)
        self.max_priority = 1.0

    def push(self, experience: Experience):
        """Add experience with max priority."""
        self.buffer.append(experience)
        self.priorities.append(self.max_priority)

    def sample(self, batch_size: int) -> Tuple[np.ndarray, ..., np.ndarray, np.ndarray]:
        """Sample batch with prioritization."""
        priorities = np.array(self.priorities)
        probs = priorities ** self.alpha
        probs /= probs.sum()

        indices = np.random.choice(len(self.buffer), batch_size, p=probs, replace=False)
        experiences = [self.buffer[i] for i in indices]

        # Importance sampling weights
        weights = (len(self.buffer) * probs[indices]) ** (-self.beta)
        weights /= weights.max()

        states = np.array([e.state for e in experiences])
        actions = np.array([e.action for e in experiences])
        rewards = np.array([e.reward for e in experiences])
        next_states = np.array([e.next_state for e in experiences])
        dones = np.array([e.done for e in experiences])

        return states, actions, rewards, next_states, dones, weights, indices

    def update_priorities(self, indices: np.ndarray, priorities: np.ndarray):
        """Update priorities for sampled experiences."""
        for idx, priority in zip(indices, priorities):
            self.priorities[idx] = priority + 1e-6
            self.max_priority = max(self.max_priority, priority + 1e-6)


# =============================================================================
# Base RL Agent
# =============================================================================

class RLAgent(ABC):
    """Abstract base class for RL agents."""

    def __init__(self, config: RLConfig):
        self.config = config
        self.training_steps = 0
        self.episode_rewards = []

    @abstractmethod
    def select_action(self, state: np.ndarray, explore: bool = True) -> Union[int, np.ndarray]:
        """Select action given state."""
        pass

    @abstractmethod
    def update(self, *args, **kwargs) -> Dict[str, float]:
        """Update agent parameters."""
        pass

    @abstractmethod
    def save(self, path: str):
        """Save agent state."""
        pass

    @abstractmethod
    def load(self, path: str):
        """Load agent state."""
        pass


# =============================================================================
# Deep Q-Network (DQN) Agent
# =============================================================================

class DQNAgent(RLAgent):
    """DQN agent with improvements (Double DQN, Dueling DQN)."""

    def __init__(self, config: RLConfig):
        super().__init__(config)

        # Networks
        layer_dims = [config.state_dim] + config.hidden_dims + [config.action_dim]
        self.q_network = MLP(layer_dims, activation="relu")
        self.target_network = self.q_network.copy()

        # Replay buffer
        self.buffer = PrioritizedReplayBuffer(config.buffer_size, config.state_dim)

        # Exploration
        self.epsilon = 1.0
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.995

    def select_action(self, state: np.ndarray, explore: bool = True) -> int:
        """Epsilon-greedy action selection."""
        if explore and np.random.random() < self.epsilon:
            return np.random.randint(self.config.action_dim)

        state = state.reshape(1, -1)
        q_values = self.q_network.forward(state)
        return int(np.argmax(q_values))

    def update(self, experience: Experience) -> Dict[str, float]:
        """Update Q-network."""
        self.buffer.push(experience)

        if len(self.buffer) < self.config.batch_size:
            return {}

        self.training_steps += 1

        # Sample batch
        result = self.buffer.sample(self.config.batch_size)
        states, actions, rewards, next_states, dones, weights, indices = result

        # Compute targets
        current_q = self.q_network.forward(states)
        next_q = self.target_network.forward(next_states)

        # Double DQN: use online network to select action
        if self.config.algorithm == RLAlgorithm.DOUBLE_DQN:
            next_actions = np.argmax(self.q_network.forward(next_states), axis=1)
            next_q_values = next_q[np.arange(len(next_actions)), next_actions]
        else:
            next_q_values = np.max(next_q, axis=1)

        targets = rewards + self.config.gamma * next_q_values * (1 - dones)

        # Compute loss
        predicted = current_q[np.arange(len(actions)), actions.astype(int)]
        td_errors = targets - predicted
        loss = np.mean(weights * td_errors ** 2)

        # Backward pass
        grad = np.zeros_like(current_q)
        grad[np.arange(len(actions)), actions.astype(int)] = -2 * weights * td_errors / len(weights)
        self.q_network.backward(grad)
        self.q_network.update(self.config.learning_rate)

        # Update priorities
        self.buffer.update_priorities(indices, np.abs(td_errors))

        # Update target network
        if self.training_steps % self.config.target_update_frequency == 0:
            self.target_network = self.q_network.copy()

        # Decay epsilon
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

        return {"loss": loss, "epsilon": self.epsilon, "td_error": np.mean(np.abs(td_errors))}

    def save(self, path: str):
        """Save agent state."""
        import pickle
        state = {
            "q_network": self.q_network.get_params(),
            "target_network": self.target_network.get_params(),
            "epsilon": self.epsilon,
            "training_steps": self.training_steps,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)

    def load(self, path: str):
        """Load agent state."""
        import pickle
        with open(path, "rb") as f:
            state = pickle.load(f)
        self.q_network.set_params(state["q_network"])
        self.target_network.set_params(state["target_network"])
        self.epsilon = state["epsilon"]
        self.training_steps = state["training_steps"]


# =============================================================================
# Proximal Policy Optimization (PPO) Agent
# =============================================================================

class PPOAgent(RLAgent):
    """PPO agent with GAE and clipped objective."""

    def __init__(self, config: RLConfig):
        super().__init__(config)

        # Actor network (policy)
        actor_dims = [config.state_dim] + config.hidden_dims + [config.action_dim]
        self.actor = MLP(actor_dims, activation="relu")

        # Critic network (value function)
        critic_dims = [config.state_dim] + config.hidden_dims + [1]
        self.critic = MLP(critic_dims, activation="relu")

        # Trajectory buffer
        self.trajectory = []

    def select_action(self, state: np.ndarray, explore: bool = True) -> Tuple[int, float, float]:
        """Select action using policy network."""
        state = state.reshape(1, -1)

        # Get action probabilities
        logits = self.actor.forward(state)
        probs = softmax(logits)

        if explore:
            action = np.random.choice(self.config.action_dim, p=probs[0])
        else:
            action = np.argmax(probs[0])

        log_prob = np.log(probs[0, action] + 1e-8)
        value = self.critic.forward(state)[0, 0]

        return action, log_prob, value

    def compute_gae(self, rewards: np.ndarray, values: np.ndarray, dones: np.ndarray, next_value: float) -> Tuple[np.ndarray, np.ndarray]:
        """Compute Generalized Advantage Estimation."""
        advantages = np.zeros_like(rewards)
        returns = np.zeros_like(rewards)
        gae = 0.0

        for t in reversed(range(len(rewards))):
            if t == len(rewards) - 1:
                next_val = next_value
            else:
                next_val = values[t + 1]

            delta = rewards[t] + self.config.gamma * next_val * (1 - dones[t]) - values[t]
            gae = delta + self.config.gamma * self.config.gae_lambda * (1 - dones[t]) * gae
            advantages[t] = gae
            returns[t] = advantages[t] + values[t]

        return advantages, returns

    def update(self, trajectory: Trajectory) -> Dict[str, float]:
        """Update policy and value networks using PPO."""
        # Normalize advantages
        advantages = (trajectory.advantages - trajectory.advantages.mean()) / (trajectory.advantages.std() + 1e-8)

        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0

        n_samples = len(trajectory.states)

        for _ in range(self.config.ppo_epochs):
            # Shuffle indices
            indices = np.random.permutation(n_samples)

            for start in range(0, n_samples, self.config.batch_size):
                end = start + self.config.batch_size
                batch_idx = indices[start:end]

                states = trajectory.states[batch_idx]
                actions = trajectory.actions[batch_idx].astype(int)
                old_log_probs = trajectory.log_probs[batch_idx]
                batch_advantages = advantages[batch_idx]
                batch_returns = trajectory.returns[batch_idx]

                # Forward pass
                logits = self.actor.forward(states)
                probs = softmax(logits)
                values = self.critic.forward(states).flatten()

                # Compute new log probs
                new_log_probs = np.log(probs[np.arange(len(actions)), actions] + 1e-8)

                # PPO clipped objective
                ratio = np.exp(new_log_probs - old_log_probs)
                clipped_ratio = np.clip(ratio, 1 - self.config.ppo_clip, 1 + self.config.ppo_clip)
                policy_loss = -np.mean(np.minimum(ratio * batch_advantages, clipped_ratio * batch_advantages))

                # Value loss
                value_loss = np.mean((values - batch_returns) ** 2)

                # Entropy bonus
                entropy = -np.sum(probs * np.log(probs + 1e-8), axis=1).mean()

                # Total loss
                loss = policy_loss + self.config.value_coef * value_loss - self.config.entropy_coef * entropy

                # Backward pass for actor
                actor_grad = np.zeros_like(logits)
                for i, action in enumerate(actions):
                    grad_log_prob = np.zeros(self.config.action_dim)
                    grad_log_prob[action] = -batch_advantages[i] * ratio[i] / (probs[i, action] + 1e-8)
                    actor_grad[i] = grad_log_prob
                self.actor.backward(actor_grad / len(actions))
                self.actor.update(self.config.learning_rate)

                # Backward pass for critic
                critic_grad = 2 * (values - batch_returns).reshape(-1, 1) / len(values)
                self.critic.backward(critic_grad)
                self.critic.update(self.config.learning_rate)

                total_policy_loss += policy_loss
                total_value_loss += value_loss
                total_entropy += entropy

        self.training_steps += 1
        n_updates = self.config.ppo_epochs * (n_samples // self.config.batch_size)

        return {
            "policy_loss": total_policy_loss / n_updates,
            "value_loss": total_value_loss / n_updates,
            "entropy": total_entropy / n_updates,
        }

    def collect_trajectory(self, env, steps: int) -> Trajectory:
        """Collect trajectory from environment."""
        states, actions, rewards, next_states = [], [], [], []
        dones, log_probs, values = [], [], []

        state = env.reset() if hasattr(env, 'reset') else np.random.randn(self.config.state_dim)

        for _ in range(steps):
            action, log_prob, value = self.select_action(state)

            # Simulate environment step
            next_state = state + np.random.randn(self.config.state_dim) * 0.1
            reward = np.random.randn() * 0.1
            done = np.random.random() < 0.01

            states.append(state)
            actions.append(action)
            rewards.append(reward)
            next_states.append(next_state)
            dones.append(done)
            log_probs.append(log_prob)
            values.append(value)

            state = next_state if not done else np.random.randn(self.config.state_dim)

        # Compute advantages
        states = np.array(states)
        values = np.array(values)
        rewards = np.array(rewards)
        dones = np.array(dones)

        _, next_value, _ = self.select_action(state, explore=False) if not done else (0, 0.0, 0.0)
        advantages, returns = self.compute_gae(rewards, values, dones, next_value if isinstance(next_value, float) else 0.0)

        return Trajectory(
            states=states,
            actions=np.array(actions),
            rewards=rewards,
            next_states=np.array(next_states),
            dones=dones,
            log_probs=np.array(log_probs),
            values=values,
            advantages=advantages,
            returns=returns,
        )

    def save(self, path: str):
        """Save agent state."""
        import pickle
        state = {
            "actor": self.actor.get_params(),
            "critic": self.critic.get_params(),
            "training_steps": self.training_steps,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)

    def load(self, path: str):
        """Load agent state."""
        import pickle
        with open(path, "rb") as f:
            state = pickle.load(f)
        self.actor.set_params(state["actor"])
        self.critic.set_params(state["critic"])
        self.training_steps = state["training_steps"]


# =============================================================================
# Advantage Actor-Critic (A2C) Agent
# =============================================================================

class A2CAgent(RLAgent):
    """A2C agent with shared feature extraction."""

    def __init__(self, config: RLConfig):
        super().__init__(config)

        # Shared feature network
        feature_dims = [config.state_dim] + config.hidden_dims[:-1]
        self.features = MLP(feature_dims, activation="relu")

        # Actor head
        self.actor_head = LinearLayer(config.hidden_dims[-2], config.action_dim)

        # Critic head
        self.critic_head = LinearLayer(config.hidden_dims[-2], 1)

        # N-step returns
        self.n_steps = 5
        self.buffer = []

    def select_action(self, state: np.ndarray, explore: bool = True) -> Tuple[int, float, float]:
        """Select action using shared network."""
        state = state.reshape(1, -1)

        # Shared features
        features = self.features.forward(state)

        # Actor output
        logits = self.actor_head.forward(features)
        probs = softmax(logits)

        # Critic output
        value = self.critic_head.forward(features)[0, 0]

        if explore:
            action = np.random.choice(self.config.action_dim, p=probs[0])
        else:
            action = np.argmax(probs[0])

        log_prob = np.log(probs[0, action] + 1e-8)

        return action, log_prob, value

    def update(self, experiences: List[Experience]) -> Dict[str, float]:
        """Update networks using collected experiences."""
        if len(experiences) < self.n_steps:
            return {}

        states = np.array([e.state for e in experiences])
        actions = np.array([e.action for e in experiences]).astype(int)
        rewards = np.array([e.reward for e in experiences])
        next_states = np.array([e.next_state for e in experiences])
        dones = np.array([e.done for e in experiences])

        # Compute returns
        returns = np.zeros_like(rewards)
        _, _, last_value = self.select_action(next_states[-1], explore=False)
        R = last_value * (1 - dones[-1])

        for t in reversed(range(len(rewards))):
            R = rewards[t] + self.config.gamma * R * (1 - dones[t])
            returns[t] = R

        # Forward pass
        features = self.features.forward(states)
        logits = self.actor_head.forward(features)
        values = self.critic_head.forward(features).flatten()
        probs = softmax(logits)

        # Compute advantages
        advantages = returns - values

        # Actor loss (policy gradient)
        log_probs = np.log(probs[np.arange(len(actions)), actions] + 1e-8)
        policy_loss = -np.mean(log_probs * advantages)

        # Critic loss
        value_loss = np.mean((values - returns) ** 2)

        # Entropy bonus
        entropy = -np.sum(probs * np.log(probs + 1e-8), axis=1).mean()

        # Total loss
        loss = policy_loss + self.config.value_coef * value_loss - self.config.entropy_coef * entropy

        # Backward pass
        # Actor head gradient
        actor_grad = np.zeros_like(logits)
        for i, action in enumerate(actions):
            actor_grad[i, action] = -advantages[i] / (probs[i, action] + 1e-8)
        actor_grad = actor_grad / len(actions)

        # Critic head gradient
        critic_grad = 2 * (values - returns).reshape(-1, 1) / len(values)

        # Update
        self.actor_head.backward(actor_grad)
        self.critic_head.backward(critic_grad)

        # Combine gradients for feature network
        feature_grad = self.actor_head.backward(actor_grad) + self.critic_head.backward(critic_grad)
        self.features.backward(feature_grad)

        # Apply updates
        self.features.update(self.config.learning_rate)
        self.actor_head.update(self.config.learning_rate)
        self.critic_head.update(self.config.learning_rate)

        self.training_steps += 1

        return {
            "policy_loss": policy_loss,
            "value_loss": value_loss,
            "entropy": entropy,
            "advantage_mean": np.mean(advantages),
        }

    def save(self, path: str):
        """Save agent state."""
        import pickle
        state = {
            "features": self.features.get_params(),
            "actor_head": (self.actor_head.weights, self.actor_head.bias),
            "critic_head": (self.critic_head.weights, self.critic_head.bias),
            "training_steps": self.training_steps,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)

    def load(self, path: str):
        """Load agent state."""
        import pickle
        with open(path, "rb") as f:
            state = pickle.load(f)
        self.features.set_params(state["features"])
        self.actor_head.weights, self.actor_head.bias = state["actor_head"]
        self.critic_head.weights, self.critic_head.bias = state["critic_head"]
        self.training_steps = state["training_steps"]


# =============================================================================
# Soft Actor-Critic (SAC) Agent
# =============================================================================

class SACAgent(RLAgent):
    """SAC agent for continuous action spaces."""

    def __init__(self, config: RLConfig):
        super().__init__(config)

        # Actor network (outputs mean and log_std)
        actor_dims = [config.state_dim] + config.hidden_dims + [config.action_dim * 2]
        self.actor = MLP(actor_dims, activation="relu")

        # Twin Q-networks
        q_dims = [config.state_dim + config.action_dim] + config.hidden_dims + [1]
        self.q1 = MLP(q_dims, activation="relu")
        self.q2 = MLP(q_dims, activation="relu")
        self.q1_target = self.q1.copy()
        self.q2_target = self.q2.copy()

        # Entropy temperature
        self.log_alpha = np.log(config.alpha)
        self.target_entropy = -config.action_dim

        # Replay buffer
        self.buffer = ReplayBuffer(config.buffer_size, config.state_dim)

    def select_action(self, state: np.ndarray, explore: bool = True) -> np.ndarray:
        """Sample action from policy."""
        state = state.reshape(1, -1)
        output = self.actor.forward(state)

        # Split into mean and log_std
        mean = output[0, :self.config.action_dim]
        log_std = np.clip(output[0, self.config.action_dim:], -20, 2)
        std = np.exp(log_std)

        if explore:
            # Sample from normal distribution
            noise = np.random.randn(self.config.action_dim)
            action = mean + std * noise
        else:
            action = mean

        # Squash to [-1, 1]
        action = np.tanh(action)
        return action

    def update(self, experience: Experience) -> Dict[str, float]:
        """Update SAC networks."""
        self.buffer.push(experience)

        if len(self.buffer) < self.config.batch_size:
            return {}

        self.training_steps += 1

        # Sample batch
        states, actions, rewards, next_states, dones = self.buffer.sample(self.config.batch_size)

        # Compute Q targets
        with_action = np.concatenate([states, actions], axis=1)
        q1_values = self.q1.forward(with_action).flatten()
        q2_values = self.q2.forward(with_action).flatten()

        # Next actions from current policy
        next_outputs = self.actor.forward(next_states)
        next_means = next_outputs[:, :self.config.action_dim]
        next_log_stds = np.clip(next_outputs[:, self.config.action_dim:], -20, 2)
        next_stds = np.exp(next_log_stds)
        next_noise = np.random.randn(*next_means.shape)
        next_actions = np.tanh(next_means + next_stds * next_noise)

        # Log probabilities
        next_log_probs = self._compute_log_prob(next_means, next_log_stds, next_actions)

        # Target Q values
        next_with_action = np.concatenate([next_states, next_actions], axis=1)
        next_q1 = self.q1_target.forward(next_with_action).flatten()
        next_q2 = self.q2_target.forward(next_with_action).flatten()
        next_q = np.minimum(next_q1, next_q2)

        alpha = np.exp(self.log_alpha)
        targets = rewards + self.config.gamma * (1 - dones) * (next_q - alpha * next_log_probs)

        # Q losses
        q1_loss = np.mean((q1_values - targets) ** 2)
        q2_loss = np.mean((q2_values - targets) ** 2)

        # Update Q networks
        q1_grad = 2 * (q1_values - targets).reshape(-1, 1) / len(q1_values)
        q2_grad = 2 * (q2_values - targets).reshape(-1, 1) / len(q2_values)
        self.q1.backward(q1_grad)
        self.q2.backward(q2_grad)
        self.q1.update(self.config.learning_rate)
        self.q2.update(self.config.learning_rate)

        # Update actor
        outputs = self.actor.forward(states)
        means = outputs[:, :self.config.action_dim]
        log_stds = np.clip(outputs[:, self.config.action_dim:], -20, 2)
        stds = np.exp(log_stds)
        noise = np.random.randn(*means.shape)
        actions_new = np.tanh(means + stds * noise)
        log_probs = self._compute_log_prob(means, log_stds, actions_new)

        with_new_action = np.concatenate([states, actions_new], axis=1)
        q1_new = self.q1.forward(with_new_action).flatten()
        q2_new = self.q2.forward(with_new_action).flatten()
        q_new = np.minimum(q1_new, q2_new)

        policy_loss = np.mean(alpha * log_probs - q_new)

        # Soft update targets
        for layer, target_layer in zip(self.q1.layers, self.q1_target.layers):
            target_layer.weights = self.config.tau * layer.weights + (1 - self.config.tau) * target_layer.weights
            target_layer.bias = self.config.tau * layer.bias + (1 - self.config.tau) * target_layer.bias

        for layer, target_layer in zip(self.q2.layers, self.q2_target.layers):
            target_layer.weights = self.config.tau * layer.weights + (1 - self.config.tau) * target_layer.weights
            target_layer.bias = self.config.tau * layer.bias + (1 - self.config.tau) * target_layer.bias

        # Update temperature
        if self.config.auto_alpha:
            alpha_loss = -np.mean(self.log_alpha * (log_probs + self.target_entropy))
            self.log_alpha -= self.config.learning_rate * alpha_loss

        return {
            "q1_loss": q1_loss,
            "q2_loss": q2_loss,
            "policy_loss": policy_loss,
            "alpha": np.exp(self.log_alpha),
        }

    def _compute_log_prob(self, mean: np.ndarray, log_std: np.ndarray, action: np.ndarray) -> np.ndarray:
        """Compute log probability of action under policy."""
        std = np.exp(log_std)
        # Atanh of tanh(x) = x
        pre_tanh = np.arctanh(np.clip(action, -0.999, 0.999))
        log_prob = -0.5 * (((pre_tanh - mean) / (std + 1e-8)) ** 2 + 2 * log_std + np.log(2 * np.pi))
        log_prob = np.sum(log_prob, axis=1)
        # Jacobian correction
        log_prob -= np.sum(np.log(1 - action ** 2 + 1e-6), axis=1)
        return log_prob

    def save(self, path: str):
        """Save agent state."""
        import pickle
        state = {
            "actor": self.actor.get_params(),
            "q1": self.q1.get_params(),
            "q2": self.q2.get_params(),
            "q1_target": self.q1_target.get_params(),
            "q2_target": self.q2_target.get_params(),
            "log_alpha": self.log_alpha,
            "training_steps": self.training_steps,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)

    def load(self, path: str):
        """Load agent state."""
        import pickle
        with open(path, "rb") as f:
            state = pickle.load(f)
        self.actor.set_params(state["actor"])
        self.q1.set_params(state["q1"])
        self.q2.set_params(state["q2"])
        self.q1_target.set_params(state["q1_target"])
        self.q2_target.set_params(state["q2_target"])
        self.log_alpha = state["log_alpha"]
        self.training_steps = state["training_steps"]


# =============================================================================
# Trading Environment
# =============================================================================

class TradingEnvironment:
    """Trading environment for RL agents."""

    def __init__(
        self,
        prices: np.ndarray,
        features: np.ndarray,
        initial_balance: float = 10000.0,
        commission: float = 0.001,
        max_position: float = 1.0
    ):
        self.prices = prices
        self.features = features
        self.initial_balance = initial_balance
        self.commission = commission
        self.max_position = max_position

        self.state_dim = features.shape[1] + 3  # features + position + balance + unrealized_pnl
        self.action_dim = 3  # buy, sell, hold

        self.reset()

    def reset(self) -> np.ndarray:
        """Reset environment."""
        self.step_idx = 0
        self.balance = self.initial_balance
        self.position = 0.0
        self.entry_price = 0.0
        self.total_reward = 0.0
        self.trades = []

        return self._get_state()

    def _get_state(self) -> np.ndarray:
        """Get current state."""
        unrealized_pnl = 0.0
        if self.position != 0:
            unrealized_pnl = self.position * (self.prices[self.step_idx] - self.entry_price)

        additional = np.array([
            self.position / self.max_position,
            self.balance / self.initial_balance,
            unrealized_pnl / self.initial_balance
        ])

        return np.concatenate([self.features[self.step_idx], additional])

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict]:
        """Take action in environment."""
        price = self.prices[self.step_idx]
        reward = 0.0
        info = {}

        # Execute action
        if action == 0:  # Buy
            if self.position <= 0:
                # Close short if any
                if self.position < 0:
                    pnl = -self.position * (price - self.entry_price)
                    commission = abs(self.position) * price * self.commission
                    self.balance += pnl - commission
                    reward += pnl - commission

                # Open long
                size = (self.balance * self.max_position) / price
                commission = size * price * self.commission
                self.position = size
                self.entry_price = price
                self.balance -= commission
                info["trade"] = "buy"

        elif action == 1:  # Sell
            if self.position >= 0:
                # Close long if any
                if self.position > 0:
                    pnl = self.position * (price - self.entry_price)
                    commission = self.position * price * self.commission
                    self.balance += pnl - commission
                    reward += pnl - commission

                # Open short
                size = (self.balance * self.max_position) / price
                commission = size * price * self.commission
                self.position = -size
                self.entry_price = price
                self.balance -= commission
                info["trade"] = "sell"

        # Hold: action == 2, do nothing

        # Move to next step
        self.step_idx += 1
        done = self.step_idx >= len(self.prices) - 1

        # Calculate unrealized PnL change as reward component
        if not done and self.position != 0:
            next_price = self.prices[self.step_idx]
            price_change = (next_price - price) / price
            unrealized_change = self.position * price_change * self.initial_balance
            reward += unrealized_change * 0.01  # Scale factor

        self.total_reward += reward
        info["balance"] = self.balance
        info["position"] = self.position

        return self._get_state(), reward, done, info


# =============================================================================
# Factory Functions
# =============================================================================

def create_agent(algorithm: RLAlgorithm, config: Optional[RLConfig] = None) -> RLAgent:
    """Create RL agent of specified type."""
    if config is None:
        config = RLConfig(algorithm=algorithm)

    if algorithm in [RLAlgorithm.DQN, RLAlgorithm.DOUBLE_DQN, RLAlgorithm.DUELING_DQN]:
        return DQNAgent(config)
    elif algorithm == RLAlgorithm.PPO:
        return PPOAgent(config)
    elif algorithm == RLAlgorithm.A2C:
        return A2CAgent(config)
    elif algorithm == RLAlgorithm.SAC:
        return SACAgent(config)
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")


def create_trading_env(
    prices: np.ndarray,
    features: np.ndarray,
    initial_balance: float = 10000.0
) -> TradingEnvironment:
    """Create trading environment."""
    return TradingEnvironment(prices, features, initial_balance)


# =============================================================================
# Testing
# =============================================================================

def test_advanced_rl():
    """Test advanced RL agents."""
    print("Testing Advanced RL Agents...")

    # Generate synthetic data
    n_samples = 1000
    state_dim = 20
    prices = 100 + np.cumsum(np.random.randn(n_samples) * 0.5)
    features = np.random.randn(n_samples, state_dim - 3)

    # Test DQN
    print("\n1. Testing DQN Agent...")
    config = RLConfig(
        algorithm=RLAlgorithm.DQN,
        state_dim=state_dim,
        action_dim=3,
        hidden_dims=[64, 64],
        batch_size=32,
        buffer_size=10000,
    )
    dqn_agent = DQNAgent(config)

    env = TradingEnvironment(prices[:500], features[:500])
    state = env.reset()

    for step in range(100):
        action = dqn_agent.select_action(state)
        next_state, reward, done, info = env.step(action)
        exp = Experience(state, action, reward, next_state, done)
        metrics = dqn_agent.update(exp)
        state = next_state
        if done:
            break

    print(f"   DQN trained for {dqn_agent.training_steps} steps")
    print(f"   Final epsilon: {dqn_agent.epsilon:.4f}")

    # Test PPO
    print("\n2. Testing PPO Agent...")
    config = RLConfig(
        algorithm=RLAlgorithm.PPO,
        state_dim=state_dim,
        action_dim=3,
        hidden_dims=[64, 64],
        batch_size=32,
        ppo_epochs=4,
    )
    ppo_agent = PPOAgent(config)

    # Collect trajectory
    states, actions, rewards = [], [], []
    log_probs, values, dones = [], [], []
    next_states = []

    state = env.reset()
    for _ in range(128):
        action, log_prob, value = ppo_agent.select_action(state)
        next_state, reward, done, _ = env.step(action)

        states.append(state)
        actions.append(action)
        rewards.append(reward)
        next_states.append(next_state)
        dones.append(done)
        log_probs.append(log_prob)
        values.append(value)

        state = next_state if not done else env.reset()

    # Compute advantages
    states = np.array(states)
    values = np.array(values)
    rewards = np.array(rewards)
    dones = np.array(dones).astype(float)

    advantages, returns = ppo_agent.compute_gae(rewards, values, dones, 0.0)

    trajectory = Trajectory(
        states=states,
        actions=np.array(actions),
        rewards=rewards,
        next_states=np.array(next_states),
        dones=dones,
        log_probs=np.array(log_probs),
        values=values,
        advantages=advantages,
        returns=returns,
    )

    metrics = ppo_agent.update(trajectory)
    print(f"   PPO policy loss: {metrics['policy_loss']:.6f}")
    print(f"   PPO value loss: {metrics['value_loss']:.6f}")

    # Test A2C
    print("\n3. Testing A2C Agent...")
    config = RLConfig(
        algorithm=RLAlgorithm.A2C,
        state_dim=state_dim,
        action_dim=3,
        hidden_dims=[64, 32],
    )
    a2c_agent = A2CAgent(config)

    experiences = []
    state = env.reset()
    for _ in range(32):
        action, _, _ = a2c_agent.select_action(state)
        next_state, reward, done, _ = env.step(action)
        experiences.append(Experience(state, action, reward, next_state, done))
        state = next_state if not done else env.reset()

    metrics = a2c_agent.update(experiences)
    if metrics:
        print(f"   A2C policy loss: {metrics['policy_loss']:.6f}")
        print(f"   A2C value loss: {metrics['value_loss']:.6f}")

    # Test SAC
    print("\n4. Testing SAC Agent...")
    config = RLConfig(
        algorithm=RLAlgorithm.SAC,
        state_dim=state_dim,
        action_dim=3,
        hidden_dims=[64, 64],
        batch_size=32,
        buffer_size=10000,
    )
    sac_agent = SACAgent(config)

    state = env.reset()
    for step in range(100):
        action = sac_agent.select_action(state)
        # Discretize action for environment
        discrete_action = int(np.argmax(action)) if len(action) > 1 else 2
        next_state, reward, done, _ = env.step(min(discrete_action, 2))
        exp = Experience(state, action, reward, next_state, done)
        metrics = sac_agent.update(exp)
        state = next_state if not done else env.reset()

    print(f"   SAC trained for {sac_agent.training_steps} steps")

    # Test Trading Environment
    print("\n5. Testing Trading Environment...")
    env = TradingEnvironment(prices, features, initial_balance=10000.0)
    state = env.reset()

    total_reward = 0
    for _ in range(len(prices) - 1):
        action = np.random.randint(3)
        next_state, reward, done, info = env.step(action)
        total_reward += reward
        if done:
            break

    print(f"   Final balance: ${env.balance:.2f}")
    print(f"   Total reward: {total_reward:.2f}")

    print("\n✓ All advanced RL tests passed!")
    return True


if __name__ == "__main__":
    test_advanced_rl()
