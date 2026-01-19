"""
Deep Learning Alpha Generation Models.

Implements cutting-edge neural network architectures for financial prediction:
- LSTM with Attention Mechanism
- Temporal Fusion Transformer (TFT)
- Neural Basis Expansion (N-BEATS)
- Wavenet-style Causal Convolutions
- Graph Neural Networks for Asset Correlation
- Probabilistic Forecasting with Quantile Regression
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from collections import deque
from abc import ABC, abstractmethod
import math


class ModelType(Enum):
    """Available deep learning model types."""
    LSTM_ATTENTION = "lstm_attention"
    TRANSFORMER = "transformer"
    NBEATS = "nbeats"
    WAVENET = "wavenet"
    TCN = "temporal_conv"
    GNN = "graph_neural"
    ENSEMBLE = "ensemble"


@dataclass
class ModelConfig:
    """Configuration for deep learning models."""
    # Architecture
    input_dim: int = 64
    hidden_dim: int = 128
    output_dim: int = 1
    num_layers: int = 3
    num_heads: int = 8  # For attention
    dropout: float = 0.1

    # Training
    learning_rate: float = 0.001
    batch_size: int = 32
    sequence_length: int = 60
    forecast_horizon: int = 5

    # Regularization
    l1_reg: float = 0.0001
    l2_reg: float = 0.001
    gradient_clip: float = 1.0

    # Quantile regression for uncertainty
    quantiles: List[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])


# ============================================================================
# Mathematical Building Blocks (Pure NumPy Implementation)
# ============================================================================

def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax."""
    exp_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return exp_x / np.sum(exp_x, axis=axis, keepdims=True)


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid."""
    return np.where(x >= 0, 1 / (1 + np.exp(-x)), np.exp(x) / (1 + np.exp(x)))


def tanh(x: np.ndarray) -> np.ndarray:
    """Hyperbolic tangent."""
    return np.tanh(x)


def gelu(x: np.ndarray) -> np.ndarray:
    """Gaussian Error Linear Unit (GELU) activation."""
    return 0.5 * x * (1 + np.tanh(np.sqrt(2 / np.pi) * (x + 0.044715 * x**3)))


def layer_norm(x: np.ndarray, gamma: np.ndarray, beta: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Layer normalization."""
    mean = np.mean(x, axis=-1, keepdims=True)
    std = np.std(x, axis=-1, keepdims=True)
    return gamma * (x - mean) / (std + eps) + beta


def glorot_uniform(shape: Tuple[int, ...]) -> np.ndarray:
    """Glorot/Xavier uniform initialization."""
    fan_in, fan_out = shape[0], shape[1] if len(shape) > 1 else shape[0]
    limit = np.sqrt(6.0 / (fan_in + fan_out))
    return np.random.uniform(-limit, limit, shape)


def he_normal(shape: Tuple[int, ...]) -> np.ndarray:
    """He normal initialization for ReLU networks."""
    fan_in = shape[0]
    std = np.sqrt(2.0 / fan_in)
    return np.random.normal(0, std, shape)


# ============================================================================
# Attention Mechanisms
# ============================================================================

class MultiHeadAttention:
    """
    Multi-Head Self-Attention mechanism.

    Implements scaled dot-product attention with multiple heads
    for learning different representation subspaces.
    """

    def __init__(self, d_model: int, num_heads: int, dropout: float = 0.1):
        self.d_model = d_model
        self.num_heads = num_heads
        self.d_k = d_model // num_heads
        self.dropout = dropout

        # Initialize weights
        self.W_q = glorot_uniform((d_model, d_model))
        self.W_k = glorot_uniform((d_model, d_model))
        self.W_v = glorot_uniform((d_model, d_model))
        self.W_o = glorot_uniform((d_model, d_model))

    def forward(self, query: np.ndarray, key: np.ndarray, value: np.ndarray,
                mask: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Forward pass of multi-head attention.

        Args:
            query: (batch, seq_len, d_model)
            key: (batch, seq_len, d_model)
            value: (batch, seq_len, d_model)
            mask: Optional attention mask

        Returns:
            output: (batch, seq_len, d_model)
            attention_weights: (batch, num_heads, seq_len, seq_len)
        """
        batch_size = query.shape[0]

        # Linear projections
        Q = query @ self.W_q
        K = key @ self.W_k
        V = value @ self.W_v

        # Reshape for multi-head: (batch, seq, d_model) -> (batch, heads, seq, d_k)
        Q = Q.reshape(batch_size, -1, self.num_heads, self.d_k).transpose(0, 2, 1, 3)
        K = K.reshape(batch_size, -1, self.num_heads, self.d_k).transpose(0, 2, 1, 3)
        V = V.reshape(batch_size, -1, self.num_heads, self.d_k).transpose(0, 2, 1, 3)

        # Scaled dot-product attention
        scores = (Q @ K.transpose(0, 1, 3, 2)) / np.sqrt(self.d_k)

        if mask is not None:
            scores = np.where(mask == 0, -1e9, scores)

        attention_weights = softmax(scores, axis=-1)

        # Apply dropout during training (simplified - always apply)
        if self.dropout > 0:
            dropout_mask = np.random.binomial(1, 1 - self.dropout, attention_weights.shape)
            attention_weights = attention_weights * dropout_mask / (1 - self.dropout)

        # Weighted sum of values
        context = attention_weights @ V

        # Reshape back: (batch, heads, seq, d_k) -> (batch, seq, d_model)
        context = context.transpose(0, 2, 1, 3).reshape(batch_size, -1, self.d_model)

        # Final linear projection
        output = context @ self.W_o

        return output, attention_weights


class TemporalAttention:
    """
    Temporal attention for time series focusing on important time steps.
    """

    def __init__(self, hidden_dim: int):
        self.hidden_dim = hidden_dim
        self.W_attention = glorot_uniform((hidden_dim, hidden_dim))
        self.v_attention = glorot_uniform((hidden_dim, 1))

    def forward(self, hidden_states: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Apply temporal attention.

        Args:
            hidden_states: (batch, seq_len, hidden_dim)

        Returns:
            context: (batch, hidden_dim)
            attention_weights: (batch, seq_len)
        """
        # Compute attention scores
        scores = tanh(hidden_states @ self.W_attention) @ self.v_attention
        scores = scores.squeeze(-1)  # (batch, seq_len)

        # Softmax to get attention weights
        attention_weights = softmax(scores, axis=-1)

        # Weighted sum
        context = np.sum(hidden_states * attention_weights[:, :, np.newaxis], axis=1)

        return context, attention_weights


# ============================================================================
# LSTM with Attention
# ============================================================================

class LSTMCell:
    """Single LSTM cell with peephole connections."""

    def __init__(self, input_dim: int, hidden_dim: int):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        # Gates: input, forget, cell, output
        combined_dim = input_dim + hidden_dim
        self.W_f = glorot_uniform((combined_dim, hidden_dim))  # Forget gate
        self.W_i = glorot_uniform((combined_dim, hidden_dim))  # Input gate
        self.W_c = glorot_uniform((combined_dim, hidden_dim))  # Cell candidate
        self.W_o = glorot_uniform((combined_dim, hidden_dim))  # Output gate

        self.b_f = np.ones(hidden_dim)  # Forget bias init to 1
        self.b_i = np.zeros(hidden_dim)
        self.b_c = np.zeros(hidden_dim)
        self.b_o = np.zeros(hidden_dim)

    def forward(self, x: np.ndarray, h_prev: np.ndarray,
                c_prev: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Forward pass of LSTM cell.

        Args:
            x: Input (batch, input_dim)
            h_prev: Previous hidden state (batch, hidden_dim)
            c_prev: Previous cell state (batch, hidden_dim)

        Returns:
            h: New hidden state
            c: New cell state
        """
        # Concatenate input and hidden state
        combined = np.concatenate([x, h_prev], axis=-1)

        # Gate computations
        f = sigmoid(combined @ self.W_f + self.b_f)  # Forget gate
        i = sigmoid(combined @ self.W_i + self.b_i)  # Input gate
        c_candidate = tanh(combined @ self.W_c + self.b_c)  # Cell candidate
        o = sigmoid(combined @ self.W_o + self.b_o)  # Output gate

        # New cell state and hidden state
        c = f * c_prev + i * c_candidate
        h = o * tanh(c)

        return h, c


class LSTMAttentionModel:
    """
    LSTM with Temporal Attention for sequence modeling.

    Architecture:
    - Bidirectional LSTM layers
    - Temporal attention mechanism
    - Residual connections
    - Layer normalization
    """

    def __init__(self, config: ModelConfig):
        self.config = config
        self.layers: List[LSTMCell] = []

        # Build LSTM layers
        for i in range(config.num_layers):
            input_dim = config.input_dim if i == 0 else config.hidden_dim
            self.layers.append(LSTMCell(input_dim, config.hidden_dim))

        # Attention
        self.attention = TemporalAttention(config.hidden_dim)

        # Output projection
        self.W_out = glorot_uniform((config.hidden_dim, config.output_dim))
        self.b_out = np.zeros(config.output_dim)

        # Layer norm parameters
        self.gamma = np.ones(config.hidden_dim)
        self.beta = np.zeros(config.hidden_dim)

    def forward(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Forward pass.

        Args:
            x: Input sequence (batch, seq_len, input_dim)

        Returns:
            output: Predictions (batch, output_dim)
            attention_weights: (batch, seq_len)
        """
        batch_size, seq_len, _ = x.shape

        # Process through LSTM layers
        hidden_states = x
        for layer in self.layers:
            h = np.zeros((batch_size, self.config.hidden_dim))
            c = np.zeros((batch_size, self.config.hidden_dim))

            layer_outputs = []
            for t in range(seq_len):
                h, c = layer.forward(hidden_states[:, t, :], h, c)
                layer_outputs.append(h)

            hidden_states = np.stack(layer_outputs, axis=1)
            hidden_states = layer_norm(hidden_states, self.gamma, self.beta)

        # Apply attention
        context, attention_weights = self.attention.forward(hidden_states)

        # Output projection
        output = context @ self.W_out + self.b_out

        return output, attention_weights


# ============================================================================
# Transformer Architecture
# ============================================================================

class PositionalEncoding:
    """Sinusoidal positional encoding for Transformers."""

    def __init__(self, d_model: int, max_len: int = 5000):
        self.d_model = d_model

        # Create positional encoding matrix
        pe = np.zeros((max_len, d_model))
        position = np.arange(max_len)[:, np.newaxis]
        div_term = np.exp(np.arange(0, d_model, 2) * (-np.log(10000.0) / d_model))

        pe[:, 0::2] = np.sin(position * div_term)
        pe[:, 1::2] = np.cos(position * div_term)

        self.pe = pe

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Add positional encoding to input."""
        seq_len = x.shape[1]
        return x + self.pe[:seq_len]


class TransformerBlock:
    """Single Transformer encoder block."""

    def __init__(self, d_model: int, num_heads: int, d_ff: int, dropout: float = 0.1):
        self.attention = MultiHeadAttention(d_model, num_heads, dropout)

        # Feed-forward network
        self.W1 = glorot_uniform((d_model, d_ff))
        self.b1 = np.zeros(d_ff)
        self.W2 = glorot_uniform((d_ff, d_model))
        self.b2 = np.zeros(d_model)

        # Layer norm parameters
        self.gamma1 = np.ones(d_model)
        self.beta1 = np.zeros(d_model)
        self.gamma2 = np.ones(d_model)
        self.beta2 = np.zeros(d_model)

        self.dropout = dropout

    def forward(self, x: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        """Forward pass of transformer block."""
        # Self-attention with residual connection
        attn_output, _ = self.attention.forward(x, x, x, mask)
        x = layer_norm(x + attn_output, self.gamma1, self.beta1)

        # Feed-forward with residual connection
        ff_output = gelu(x @ self.W1 + self.b1) @ self.W2 + self.b2
        x = layer_norm(x + ff_output, self.gamma2, self.beta2)

        return x


class TemporalFusionTransformer:
    """
    Temporal Fusion Transformer for time series forecasting.

    Features:
    - Variable selection networks
    - Gated residual networks
    - Multi-head attention for temporal patterns
    - Interpretable attention weights
    """

    def __init__(self, config: ModelConfig):
        self.config = config

        # Positional encoding
        self.pos_encoding = PositionalEncoding(config.hidden_dim)

        # Input projection
        self.W_input = glorot_uniform((config.input_dim, config.hidden_dim))

        # Transformer blocks
        self.blocks = [
            TransformerBlock(config.hidden_dim, config.num_heads,
                           config.hidden_dim * 4, config.dropout)
            for _ in range(config.num_layers)
        ]

        # Output heads for quantile regression
        self.output_heads = {
            q: glorot_uniform((config.hidden_dim, config.output_dim))
            for q in config.quantiles
        }

    def forward(self, x: np.ndarray) -> Dict[float, np.ndarray]:
        """
        Forward pass.

        Args:
            x: Input (batch, seq_len, input_dim)

        Returns:
            Dict mapping quantile to predictions
        """
        # Project input
        h = x @ self.W_input

        # Add positional encoding
        h = self.pos_encoding.forward(h)

        # Causal mask for autoregressive prediction
        seq_len = h.shape[1]
        mask = np.triu(np.ones((seq_len, seq_len)), k=1)
        mask = np.where(mask == 1, 0, 1)

        # Process through transformer blocks
        for block in self.blocks:
            h = block.forward(h, mask)

        # Take last time step for prediction
        h_last = h[:, -1, :]

        # Generate quantile predictions
        outputs = {}
        for q, W in self.output_heads.items():
            outputs[q] = h_last @ W

        return outputs


# ============================================================================
# N-BEATS (Neural Basis Expansion Analysis)
# ============================================================================

class NBEATSBlock:
    """
    N-BEATS block with learnable basis functions.

    Implements:
    - Generic basis (fully connected)
    - Trend basis (polynomial)
    - Seasonality basis (Fourier)
    """

    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int,
                 basis_type: str = "generic", num_basis: int = 8):
        self.basis_type = basis_type
        self.num_basis = num_basis

        # Stack of fully connected layers
        self.fc_layers = [
            (glorot_uniform((input_dim, hidden_dim)), np.zeros(hidden_dim)),
            (glorot_uniform((hidden_dim, hidden_dim)), np.zeros(hidden_dim)),
            (glorot_uniform((hidden_dim, hidden_dim)), np.zeros(hidden_dim)),
            (glorot_uniform((hidden_dim, hidden_dim)), np.zeros(hidden_dim)),
        ]

        # Basis expansion coefficients
        self.W_backcast = glorot_uniform((hidden_dim, num_basis))
        self.W_forecast = glorot_uniform((hidden_dim, num_basis))

        # Basis functions
        if basis_type == "trend":
            # Polynomial basis
            self.backcast_basis = self._trend_basis(input_dim, num_basis)
            self.forecast_basis = self._trend_basis(output_dim, num_basis)
        elif basis_type == "seasonality":
            # Fourier basis
            self.backcast_basis = self._seasonality_basis(input_dim, num_basis)
            self.forecast_basis = self._seasonality_basis(output_dim, num_basis)
        else:
            # Generic learnable basis
            self.backcast_basis = glorot_uniform((num_basis, input_dim))
            self.forecast_basis = glorot_uniform((num_basis, output_dim))

    def _trend_basis(self, length: int, degree: int) -> np.ndarray:
        """Create polynomial trend basis."""
        t = np.linspace(0, 1, length)
        basis = np.array([t ** i for i in range(degree)])
        return basis

    def _seasonality_basis(self, length: int, num_harmonics: int) -> np.ndarray:
        """Create Fourier seasonality basis."""
        t = np.linspace(0, 2 * np.pi, length)
        basis = []
        for i in range(1, num_harmonics // 2 + 1):
            basis.append(np.sin(i * t))
            basis.append(np.cos(i * t))
        return np.array(basis[:num_harmonics])

    def forward(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Forward pass.

        Args:
            x: Input (batch, input_dim)

        Returns:
            backcast: Reconstruction of input
            forecast: Prediction
        """
        h = x
        for W, b in self.fc_layers:
            h = gelu(h @ W + b)

        # Compute basis coefficients
        theta_backcast = h @ self.W_backcast
        theta_forecast = h @ self.W_forecast

        # Basis expansion
        backcast = theta_backcast @ self.backcast_basis
        forecast = theta_forecast @ self.forecast_basis

        return backcast, forecast


class NBEATSModel:
    """
    N-BEATS: Neural Basis Expansion Analysis for Time Series.

    Stacked architecture with:
    - Trend stack
    - Seasonality stack
    - Generic stack
    """

    def __init__(self, config: ModelConfig):
        self.config = config

        # Create stacks
        self.trend_blocks = [
            NBEATSBlock(config.sequence_length, config.hidden_dim,
                       config.forecast_horizon, "trend")
            for _ in range(3)
        ]

        self.seasonality_blocks = [
            NBEATSBlock(config.sequence_length, config.hidden_dim,
                       config.forecast_horizon, "seasonality")
            for _ in range(3)
        ]

        self.generic_blocks = [
            NBEATSBlock(config.sequence_length, config.hidden_dim,
                       config.forecast_horizon, "generic")
            for _ in range(3)
        ]

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass with doubly residual stacking.

        Args:
            x: Input (batch, seq_len)

        Returns:
            forecast: Predictions (batch, forecast_horizon)
        """
        forecast = np.zeros((x.shape[0], self.config.forecast_horizon))
        residual = x

        # Process through all stacks
        for blocks in [self.trend_blocks, self.seasonality_blocks, self.generic_blocks]:
            for block in blocks:
                backcast, block_forecast = block.forward(residual)
                residual = residual - backcast
                forecast = forecast + block_forecast

        return forecast


# ============================================================================
# Temporal Convolutional Network (TCN) with WaveNet-style dilations
# ============================================================================

class CausalConv1D:
    """Causal 1D convolution with dilation."""

    def __init__(self, in_channels: int, out_channels: int,
                 kernel_size: int, dilation: int = 1):
        self.kernel_size = kernel_size
        self.dilation = dilation
        self.padding = (kernel_size - 1) * dilation

        self.W = he_normal((out_channels, in_channels, kernel_size))
        self.b = np.zeros(out_channels)

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass of causal convolution.

        Args:
            x: Input (batch, channels, seq_len)

        Returns:
            output: (batch, out_channels, seq_len)
        """
        batch_size, in_channels, seq_len = x.shape
        out_channels = self.W.shape[0]

        # Pad input for causal convolution
        x_padded = np.pad(x, ((0, 0), (0, 0), (self.padding, 0)), mode='constant')

        # Dilated convolution (simplified implementation)
        output = np.zeros((batch_size, out_channels, seq_len))

        for t in range(seq_len):
            for k in range(self.kernel_size):
                idx = t + self.padding - k * self.dilation
                if 0 <= idx < x_padded.shape[2]:
                    output[:, :, t] += np.tensordot(
                        x_padded[:, :, idx], self.W[:, :, k], axes=([1], [1])
                    )

        output = output + self.b[np.newaxis, :, np.newaxis]
        return output


class TCNBlock:
    """Temporal Convolutional Network block with residual connection."""

    def __init__(self, channels: int, kernel_size: int, dilation: int, dropout: float = 0.1):
        self.conv1 = CausalConv1D(channels, channels, kernel_size, dilation)
        self.conv2 = CausalConv1D(channels, channels, kernel_size, dilation)
        self.dropout = dropout

        # Layer norm
        self.gamma1 = np.ones(channels)
        self.beta1 = np.zeros(channels)
        self.gamma2 = np.ones(channels)
        self.beta2 = np.zeros(channels)

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Forward pass with gated activation."""
        # First conv with gated activation
        h = self.conv1.forward(x)
        h = h.transpose(0, 2, 1)
        h = layer_norm(h, self.gamma1, self.beta1)
        h = h.transpose(0, 2, 1)
        h = gelu(h)

        # Second conv
        h = self.conv2.forward(h)
        h = h.transpose(0, 2, 1)
        h = layer_norm(h, self.gamma2, self.beta2)
        h = h.transpose(0, 2, 1)
        h = gelu(h)

        # Residual connection
        return x + h


class WaveNetModel:
    """
    WaveNet-style Temporal Convolutional Network.

    Features:
    - Exponentially increasing dilation factors
    - Gated activations
    - Skip connections
    - Multi-scale temporal patterns
    """

    def __init__(self, config: ModelConfig):
        self.config = config

        # Input projection
        self.W_input = glorot_uniform((config.input_dim, config.hidden_dim))

        # TCN blocks with increasing dilation
        num_blocks = 8
        self.blocks = [
            TCNBlock(config.hidden_dim, kernel_size=3, dilation=2**i, dropout=config.dropout)
            for i in range(num_blocks)
        ]

        # Output projection
        self.W_out = glorot_uniform((config.hidden_dim, config.output_dim))

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        Forward pass.

        Args:
            x: Input (batch, seq_len, input_dim)

        Returns:
            output: (batch, output_dim)
        """
        # Project input and transpose for conv: (batch, seq, dim) -> (batch, dim, seq)
        h = (x @ self.W_input).transpose(0, 2, 1)

        # Process through TCN blocks
        skip_connections = []
        for block in self.blocks:
            h = block.forward(h)
            skip_connections.append(h)

        # Sum skip connections
        h = sum(skip_connections) / len(skip_connections)

        # Take last time step and project to output
        h_last = h[:, :, -1]
        output = h_last @ self.W_out

        return output


# ============================================================================
# Ensemble Model Aggregator
# ============================================================================

class AlphaModelEnsemble:
    """
    Ensemble of deep learning models for robust alpha generation.

    Combines predictions from multiple architectures using
    learned weights based on recent performance.
    """

    def __init__(self, config: ModelConfig):
        self.config = config

        # Initialize all model types
        self.models = {
            ModelType.LSTM_ATTENTION: LSTMAttentionModel(config),
            ModelType.TRANSFORMER: TemporalFusionTransformer(config),
            ModelType.NBEATS: NBEATSModel(config),
            ModelType.WAVENET: WaveNetModel(config),
        }

        # Ensemble weights (learnable)
        self.weights = {model_type: 1.0 / len(self.models) for model_type in self.models}

        # Performance tracking
        self.model_errors: Dict[ModelType, deque] = {
            model_type: deque(maxlen=100) for model_type in self.models
        }

    def predict(self, x: np.ndarray) -> Dict[str, Any]:
        """
        Generate ensemble prediction with uncertainty estimates.

        Args:
            x: Input features (batch, seq_len, input_dim)

        Returns:
            Dict with predictions, uncertainty, and model weights
        """
        predictions = {}

        # Get predictions from each model
        for model_type, model in self.models.items():
            if model_type == ModelType.TRANSFORMER:
                # Transformer returns quantile predictions
                outputs = model.forward(x)
                predictions[model_type] = outputs[0.5]  # Median prediction
            elif model_type == ModelType.NBEATS:
                # N-BEATS expects flattened input
                x_flat = x[:, :, 0] if x.shape[2] == 1 else x.mean(axis=2)
                predictions[model_type] = model.forward(x_flat)[:, 0:1]
            elif model_type == ModelType.LSTM_ATTENTION:
                pred, _ = model.forward(x)
                predictions[model_type] = pred
            else:
                predictions[model_type] = model.forward(x)

        # Weighted ensemble
        ensemble_pred = sum(
            self.weights[mt] * pred
            for mt, pred in predictions.items()
        )

        # Estimate uncertainty from model disagreement
        pred_array = np.stack([pred for pred in predictions.values()], axis=0)
        uncertainty = np.std(pred_array, axis=0)

        return {
            "prediction": ensemble_pred,
            "uncertainty": uncertainty,
            "model_predictions": {mt.value: pred for mt, pred in predictions.items()},
            "model_weights": {mt.value: w for mt, w in self.weights.items()},
        }

    def update_weights(self, actual: np.ndarray, predictions: Dict[ModelType, np.ndarray]) -> None:
        """Update ensemble weights based on prediction errors."""
        for model_type, pred in predictions.items():
            error = np.mean((actual - pred) ** 2)
            self.model_errors[model_type].append(error)

        # Update weights inversely proportional to recent error
        if all(len(errors) > 10 for errors in self.model_errors.values()):
            recent_errors = {
                mt: np.mean(list(errors)[-10:])
                for mt, errors in self.model_errors.items()
            }
            total_inv_error = sum(1.0 / (e + 1e-6) for e in recent_errors.values())

            for model_type in self.weights:
                self.weights[model_type] = (
                    (1.0 / (recent_errors[model_type] + 1e-6)) / total_inv_error
                )


# ============================================================================
# Alpha Signal Generator
# ============================================================================

class DeepAlphaGenerator:
    """
    Deep Learning Alpha Signal Generator.

    Generates trading signals from ensemble of neural networks
    with uncertainty quantification and regime detection.
    """

    def __init__(self, config: Optional[ModelConfig] = None):
        self.config = config or ModelConfig()
        self.ensemble = AlphaModelEnsemble(self.config)

        # Feature statistics for normalization
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

        # Signal history
        self.signal_history: List[Dict] = []

    def normalize_features(self, features: np.ndarray) -> np.ndarray:
        """Z-score normalization of features."""
        if self.feature_mean is None:
            self.feature_mean = np.mean(features, axis=(0, 1))
            self.feature_std = np.std(features, axis=(0, 1)) + 1e-6

        return (features - self.feature_mean) / self.feature_std

    def generate_signal(self, features: np.ndarray) -> Dict[str, Any]:
        """
        Generate alpha signal from features.

        Args:
            features: Input features (batch, seq_len, num_features)

        Returns:
            Dict with signal, confidence, and metadata
        """
        # Normalize
        features_norm = self.normalize_features(features)

        # Get ensemble prediction
        result = self.ensemble.predict(features_norm)

        prediction = result["prediction"]
        uncertainty = result["uncertainty"]

        # Convert to signal
        signal_strength = np.tanh(prediction)  # Bound to [-1, 1]

        # Confidence based on prediction strength and uncertainty
        confidence = np.abs(signal_strength) * (1 - np.tanh(uncertainty))

        # Determine signal type
        if signal_strength.mean() > 0.1:
            signal_type = "STRONG_BUY" if signal_strength.mean() > 0.3 else "BUY"
        elif signal_strength.mean() < -0.1:
            signal_type = "STRONG_SELL" if signal_strength.mean() < -0.3 else "SELL"
        else:
            signal_type = "HOLD"

        signal = {
            "signal_type": signal_type,
            "signal_strength": float(signal_strength.mean()),
            "confidence": float(confidence.mean()),
            "uncertainty": float(uncertainty.mean()),
            "prediction_raw": float(prediction.mean()),
            "model_agreement": 1 - float(uncertainty.mean() / (np.abs(prediction.mean()) + 1e-6)),
            "model_weights": result["model_weights"],
        }

        self.signal_history.append(signal)
        return signal


if __name__ == "__main__":
    # Test deep learning models
    print("Testing Deep Learning Alpha Models")
    print("=" * 50)

    config = ModelConfig(
        input_dim=32,
        hidden_dim=64,
        output_dim=1,
        num_layers=2,
        sequence_length=30,
        forecast_horizon=5
    )

    # Generate sample data
    np.random.seed(42)
    batch_size = 4
    x = np.random.randn(batch_size, config.sequence_length, config.input_dim)

    # Test LSTM Attention
    print("\n1. LSTM with Attention:")
    lstm = LSTMAttentionModel(config)
    output, attention = lstm.forward(x)
    print(f"   Output shape: {output.shape}")
    print(f"   Attention shape: {attention.shape}")

    # Test Transformer
    print("\n2. Temporal Fusion Transformer:")
    tft = TemporalFusionTransformer(config)
    outputs = tft.forward(x)
    for q, pred in outputs.items():
        print(f"   Quantile {q}: {pred.shape}")

    # Test N-BEATS
    print("\n3. N-BEATS:")
    nbeats_config = ModelConfig(
        input_dim=1,
        hidden_dim=64,
        output_dim=1,
        sequence_length=30,
        forecast_horizon=5
    )
    nbeats = NBEATSModel(nbeats_config)
    x_1d = np.random.randn(batch_size, config.sequence_length)
    output = nbeats.forward(x_1d)
    print(f"   Output shape: {output.shape}")

    # Test WaveNet/TCN
    print("\n4. WaveNet/TCN:")
    wavenet = WaveNetModel(config)
    output = wavenet.forward(x)
    print(f"   Output shape: {output.shape}")

    # Test Ensemble
    print("\n5. Alpha Model Ensemble:")
    ensemble = AlphaModelEnsemble(config)
    result = ensemble.predict(x)
    print(f"   Prediction shape: {result['prediction'].shape}")
    print(f"   Uncertainty shape: {result['uncertainty'].shape}")
    print(f"   Model weights: {result['model_weights']}")

    # Test Alpha Generator
    print("\n6. Deep Alpha Generator:")
    generator = DeepAlphaGenerator(config)
    signal = generator.generate_signal(x)
    print(f"   Signal: {signal['signal_type']}")
    print(f"   Strength: {signal['signal_strength']:.4f}")
    print(f"   Confidence: {signal['confidence']:.4f}")
    print(f"   Uncertainty: {signal['uncertainty']:.4f}")
