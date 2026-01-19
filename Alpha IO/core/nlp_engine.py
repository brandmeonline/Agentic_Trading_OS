"""
NLP Sentiment Analysis Engine.

Advanced natural language processing for financial sentiment:
- Transformer-based text embeddings (pure NumPy)
- Financial sentiment classification
- Entity extraction (tickers, companies)
- News and social media processing
- Sentiment aggregation and signal generation
"""

from __future__ import annotations

import numpy as np
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Set
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime
import json


# =============================================================================
# Configuration and Data Models
# =============================================================================

class SentimentType(Enum):
    """Sentiment classification types."""
    VERY_BEARISH = -2
    BEARISH = -1
    NEUTRAL = 0
    BULLISH = 1
    VERY_BULLISH = 2


@dataclass
class SentimentSignal:
    """Sentiment analysis result."""
    text: str
    sentiment: SentimentType
    confidence: float
    entities: List[str]
    topics: List[str]
    timestamp: datetime
    source: str
    raw_score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "text": self.text[:200],
            "sentiment": self.sentiment.name,
            "confidence": self.confidence,
            "entities": self.entities,
            "topics": self.topics,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "raw_score": self.raw_score,
            "metadata": self.metadata
        }


@dataclass
class AggregatedSentiment:
    """Aggregated sentiment for an asset."""
    asset: str
    sentiment_score: float  # -1 to 1
    confidence: float
    n_signals: int
    bullish_ratio: float
    bearish_ratio: float
    volume_weighted_score: float
    momentum: float  # Change in sentiment
    sources: Dict[str, float]  # Source -> contribution
    timestamp: datetime


@dataclass
class NLPConfig:
    """NLP engine configuration."""
    embedding_dim: int = 128
    vocab_size: int = 10000
    max_sequence_length: int = 256
    sentiment_threshold: float = 0.3
    entity_confidence_threshold: float = 0.5
    aggregation_window: int = 3600  # seconds
    decay_factor: float = 0.95
    min_word_frequency: int = 2


# =============================================================================
# Text Preprocessing
# =============================================================================

class TextPreprocessor:
    """
    Financial text preprocessing.

    Handles:
    - Tokenization
    - Normalization
    - Financial entity recognition
    - Noise removal
    """

    # Common financial terms and their sentiment implications
    BULLISH_TERMS = {
        'buy', 'long', 'bullish', 'moon', 'pump', 'breakout', 'rally', 'surge',
        'gain', 'profit', 'growth', 'uptick', 'upside', 'accumulate', 'rocket',
        'soar', 'jump', 'spike', 'boom', 'explode', 'skyrocket', 'outperform',
        'beat', 'exceed', 'strong', 'positive', 'optimistic', 'confident',
        'upgrade', 'target', 'calls', 'support', 'opportunity', 'undervalued'
    }

    BEARISH_TERMS = {
        'sell', 'short', 'bearish', 'dump', 'crash', 'plunge', 'drop', 'fall',
        'loss', 'decline', 'downturn', 'downside', 'distribute', 'tank', 'sink',
        'tumble', 'collapse', 'correction', 'weak', 'negative', 'pessimistic',
        'downgrade', 'puts', 'resistance', 'risk', 'overvalued', 'bubble',
        'fear', 'panic', 'crisis', 'recession', 'bankruptcy', 'default'
    }

    INTENSITY_MODIFIERS = {
        'very': 1.5, 'extremely': 2.0, 'slightly': 0.5, 'somewhat': 0.7,
        'highly': 1.5, 'massively': 2.0, 'huge': 1.5, 'major': 1.3,
        'minor': 0.5, 'significant': 1.3, 'substantial': 1.3
    }

    NEGATION_WORDS = {'not', "n't", 'no', 'never', 'neither', 'none', 'nothing'}

    def __init__(self):
        # Common crypto/stock tickers pattern
        self.ticker_pattern = re.compile(r'\$([A-Z]{1,5})\b|\b([A-Z]{2,5})\b(?=\s|$|[,.])')
        self.cashtag_pattern = re.compile(r'\$([A-Z]{1,5})\b')
        self.url_pattern = re.compile(r'https?://\S+|www\.\S+')
        self.mention_pattern = re.compile(r'@\w+')
        self.number_pattern = re.compile(r'\b\d+\.?\d*[%KMB]?\b')

    def preprocess(self, text: str) -> Tuple[List[str], Dict[str, Any]]:
        """
        Preprocess text for sentiment analysis.

        Returns:
            Tuple of (tokens, metadata)
        """
        metadata = {
            "original_length": len(text),
            "tickers": [],
            "mentions": [],
            "urls": [],
            "numbers": []
        }

        # Extract entities before cleaning
        metadata["tickers"] = self.cashtag_pattern.findall(text)
        metadata["mentions"] = self.mention_pattern.findall(text)
        metadata["urls"] = self.url_pattern.findall(text)
        metadata["numbers"] = self.number_pattern.findall(text)

        # Clean text
        text = text.lower()
        text = self.url_pattern.sub(' ', text)
        text = self.mention_pattern.sub(' ', text)
        text = re.sub(r'[^\w\s\']', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Tokenize
        tokens = text.split()

        # Remove very short tokens
        tokens = [t for t in tokens if len(t) > 1]

        return tokens, metadata

    def extract_entities(self, text: str) -> List[str]:
        """Extract financial entities (tickers, companies)."""
        entities = []

        # Cashtags
        cashtags = self.cashtag_pattern.findall(text)
        entities.extend(cashtags)

        # Common crypto mentions
        crypto_patterns = {
            'bitcoin': 'BTC', 'btc': 'BTC',
            'ethereum': 'ETH', 'eth': 'ETH',
            'cardano': 'ADA', 'ada': 'ADA',
            'solana': 'SOL', 'sol': 'SOL',
            'ripple': 'XRP', 'xrp': 'XRP',
            'dogecoin': 'DOGE', 'doge': 'DOGE'
        }

        text_lower = text.lower()
        for pattern, ticker in crypto_patterns.items():
            if pattern in text_lower:
                entities.append(ticker)

        return list(set(entities))


# =============================================================================
# Word Embeddings (Pure NumPy)
# =============================================================================

class WordEmbeddings:
    """
    Simple word embeddings using word co-occurrence.

    For production, this would be replaced with pre-trained
    embeddings like Word2Vec or GloVe.
    """

    def __init__(self, dim: int = 128, vocab_size: int = 10000):
        self.dim = dim
        self.vocab_size = vocab_size
        self.word_to_idx: Dict[str, int] = {"<PAD>": 0, "<UNK>": 1}
        self.idx_to_word: Dict[int, str] = {0: "<PAD>", 1: "<UNK>"}
        self.embeddings: Optional[np.ndarray] = None
        self._initialized = False

    def build_vocabulary(self, texts: List[List[str]], min_freq: int = 2):
        """Build vocabulary from tokenized texts."""
        # Count word frequencies
        word_counts: Dict[str, int] = {}
        for tokens in texts:
            for token in tokens:
                word_counts[token] = word_counts.get(token, 0) + 1

        # Filter by frequency and limit vocab size
        sorted_words = sorted(
            [(w, c) for w, c in word_counts.items() if c >= min_freq],
            key=lambda x: x[1],
            reverse=True
        )[:self.vocab_size - 2]

        # Build mappings
        for i, (word, _) in enumerate(sorted_words):
            idx = i + 2  # Reserve 0 for PAD, 1 for UNK
            self.word_to_idx[word] = idx
            self.idx_to_word[idx] = word

        # Initialize random embeddings
        actual_vocab_size = len(self.word_to_idx)
        self.embeddings = np.random.randn(actual_vocab_size, self.dim) * 0.1

        # Special tokens
        self.embeddings[0] = 0  # PAD
        self.embeddings[1] = np.random.randn(self.dim) * 0.01  # UNK

        self._initialized = True

    def initialize_random(self):
        """Initialize with random embeddings."""
        self.embeddings = np.random.randn(self.vocab_size, self.dim) * 0.1
        self.embeddings[0] = 0  # PAD
        self._initialized = True

    def tokens_to_indices(self, tokens: List[str]) -> np.ndarray:
        """Convert tokens to indices."""
        return np.array([
            self.word_to_idx.get(token, 1)  # 1 is UNK
            for token in tokens
        ])

    def embed(self, tokens: List[str]) -> np.ndarray:
        """Get embeddings for tokens."""
        if not self._initialized:
            self.initialize_random()

        indices = self.tokens_to_indices(tokens)
        indices = np.clip(indices, 0, len(self.embeddings) - 1)
        return self.embeddings[indices]

    def embed_sequence(self, tokens: List[str], max_length: int) -> np.ndarray:
        """Get padded sequence embeddings."""
        embeddings = self.embed(tokens[:max_length])

        if len(embeddings) < max_length:
            padding = np.zeros((max_length - len(embeddings), self.dim))
            embeddings = np.vstack([embeddings, padding])

        return embeddings


# =============================================================================
# Attention Mechanism
# =============================================================================

class SelfAttention:
    """
    Self-attention mechanism for sequence processing.
    """

    def __init__(self, dim: int, n_heads: int = 4):
        self.dim = dim
        self.n_heads = n_heads
        self.head_dim = dim // n_heads

        # Initialize weights
        scale = np.sqrt(2.0 / dim)
        self.W_q = np.random.randn(dim, dim) * scale
        self.W_k = np.random.randn(dim, dim) * scale
        self.W_v = np.random.randn(dim, dim) * scale
        self.W_o = np.random.randn(dim, dim) * scale

    def __call__(self, x: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Apply self-attention.

        Args:
            x: Input of shape (seq_len, dim)
            mask: Optional mask of shape (seq_len,)

        Returns:
            Output of shape (seq_len, dim)
        """
        seq_len = x.shape[0]

        # Linear projections
        Q = x @ self.W_q
        K = x @ self.W_k
        V = x @ self.W_v

        # Reshape for multi-head attention
        Q = Q.reshape(seq_len, self.n_heads, self.head_dim)
        K = K.reshape(seq_len, self.n_heads, self.head_dim)
        V = V.reshape(seq_len, self.n_heads, self.head_dim)

        # Transpose for batch matrix multiplication
        Q = Q.transpose(1, 0, 2)  # (n_heads, seq_len, head_dim)
        K = K.transpose(1, 0, 2)
        V = V.transpose(1, 0, 2)

        # Attention scores
        scores = np.matmul(Q, K.transpose(0, 2, 1)) / np.sqrt(self.head_dim)

        # Apply mask
        if mask is not None:
            mask_expanded = mask[np.newaxis, np.newaxis, :]
            scores = np.where(mask_expanded == 0, -1e9, scores)

        # Softmax
        scores_exp = np.exp(scores - np.max(scores, axis=-1, keepdims=True))
        attention = scores_exp / (np.sum(scores_exp, axis=-1, keepdims=True) + 1e-10)

        # Apply attention to values
        context = np.matmul(attention, V)  # (n_heads, seq_len, head_dim)

        # Concatenate heads
        context = context.transpose(1, 0, 2).reshape(seq_len, self.dim)

        # Output projection
        output = context @ self.W_o

        return output


# =============================================================================
# Sentiment Analyzer
# =============================================================================

class SentimentAnalyzer:
    """
    Financial sentiment analyzer.

    Uses a combination of:
    - Lexicon-based analysis
    - Attention-weighted embeddings
    - Pattern matching
    """

    def __init__(self, config: NLPConfig):
        self.config = config
        self.preprocessor = TextPreprocessor()
        self.embeddings = WordEmbeddings(config.embedding_dim, config.vocab_size)
        self.attention = SelfAttention(config.embedding_dim)

        # Sentiment lexicon weights
        self.lexicon_weights: Dict[str, float] = {}
        self._build_lexicon()

    def _build_lexicon(self):
        """Build sentiment lexicon."""
        # Bullish terms
        for term in TextPreprocessor.BULLISH_TERMS:
            self.lexicon_weights[term] = 1.0

        # Bearish terms
        for term in TextPreprocessor.BEARISH_TERMS:
            self.lexicon_weights[term] = -1.0

        # Add intensity modifiers
        self.intensity_modifiers = TextPreprocessor.INTENSITY_MODIFIERS
        self.negation_words = TextPreprocessor.NEGATION_WORDS

    def analyze(self, text: str, source: str = "unknown") -> SentimentSignal:
        """
        Analyze sentiment of text.

        Args:
            text: Input text
            source: Source of the text

        Returns:
            SentimentSignal with analysis results
        """
        # Preprocess
        tokens, metadata = self.preprocessor.preprocess(text)

        if not tokens:
            return SentimentSignal(
                text=text,
                sentiment=SentimentType.NEUTRAL,
                confidence=0.0,
                entities=metadata.get("tickers", []),
                topics=[],
                timestamp=datetime.now(),
                source=source,
                raw_score=0.0
            )

        # Lexicon-based score
        lexicon_score, lexicon_confidence = self._lexicon_score(tokens)

        # Embedding-based score
        embedding_score, embedding_confidence = self._embedding_score(tokens)

        # Combine scores
        combined_score = 0.6 * lexicon_score + 0.4 * embedding_score
        combined_confidence = 0.6 * lexicon_confidence + 0.4 * embedding_confidence

        # Classify sentiment
        sentiment = self._classify_sentiment(combined_score)

        # Extract entities
        entities = self.preprocessor.extract_entities(text)

        # Extract topics
        topics = self._extract_topics(tokens)

        return SentimentSignal(
            text=text,
            sentiment=sentiment,
            confidence=combined_confidence,
            entities=entities,
            topics=topics,
            timestamp=datetime.now(),
            source=source,
            raw_score=combined_score,
            metadata=metadata
        )

    def _lexicon_score(self, tokens: List[str]) -> Tuple[float, float]:
        """Calculate lexicon-based sentiment score."""
        score = 0.0
        matched_count = 0
        intensity = 1.0
        negate = False

        for i, token in enumerate(tokens):
            # Check for negation
            if token in self.negation_words:
                negate = True
                continue

            # Check for intensity modifier
            if token in self.intensity_modifiers:
                intensity = self.intensity_modifiers[token]
                continue

            # Check for sentiment term
            if token in self.lexicon_weights:
                term_score = self.lexicon_weights[token] * intensity

                if negate:
                    term_score = -term_score
                    negate = False

                score += term_score
                matched_count += 1
                intensity = 1.0

        # Normalize
        if matched_count > 0:
            normalized_score = np.tanh(score / np.sqrt(matched_count))
            confidence = min(1.0, matched_count / 5)  # Confidence based on matches
        else:
            normalized_score = 0.0
            confidence = 0.1

        return normalized_score, confidence

    def _embedding_score(self, tokens: List[str]) -> Tuple[float, float]:
        """Calculate embedding-based sentiment score using attention."""
        if not tokens:
            return 0.0, 0.0

        # Get embeddings
        embeddings = self.embeddings.embed_sequence(
            tokens, self.config.max_sequence_length
        )

        # Create mask for non-padding tokens
        mask = np.zeros(self.config.max_sequence_length)
        mask[:min(len(tokens), self.config.max_sequence_length)] = 1

        # Apply self-attention
        attended = self.attention(embeddings, mask)

        # Pool attended representations (mean of non-padding)
        valid_length = int(np.sum(mask))
        if valid_length > 0:
            pooled = np.mean(attended[:valid_length], axis=0)
        else:
            pooled = np.zeros(self.config.embedding_dim)

        # Project to sentiment score
        # Use a learned projection (initialized randomly for now)
        np.random.seed(42)  # Deterministic for consistency
        sentiment_projection = np.random.randn(self.config.embedding_dim) * 0.1

        raw_score = np.dot(pooled, sentiment_projection)
        normalized_score = np.tanh(raw_score)

        # Confidence based on embedding norm
        confidence = min(1.0, np.linalg.norm(pooled) / 10)

        return normalized_score, confidence

    def _classify_sentiment(self, score: float) -> SentimentType:
        """Classify sentiment from score."""
        threshold = self.config.sentiment_threshold

        if score >= threshold * 2:
            return SentimentType.VERY_BULLISH
        elif score >= threshold:
            return SentimentType.BULLISH
        elif score <= -threshold * 2:
            return SentimentType.VERY_BEARISH
        elif score <= -threshold:
            return SentimentType.BEARISH
        else:
            return SentimentType.NEUTRAL

    def _extract_topics(self, tokens: List[str]) -> List[str]:
        """Extract main topics from tokens."""
        # Financial topics
        topic_keywords = {
            "earnings": ["earnings", "revenue", "profit", "income", "eps"],
            "fed": ["fed", "fomc", "rate", "interest", "powell", "monetary"],
            "regulation": ["sec", "regulation", "law", "compliance", "legal"],
            "technology": ["tech", "ai", "blockchain", "crypto", "innovation"],
            "macro": ["inflation", "gdp", "employment", "recession", "economy"],
            "trading": ["trade", "volume", "liquidity", "spread", "orderbook"]
        }

        topics = []
        token_set = set(tokens)

        for topic, keywords in topic_keywords.items():
            if any(kw in token_set for kw in keywords):
                topics.append(topic)

        return topics


# =============================================================================
# News Processor
# =============================================================================

class NewsProcessor:
    """
    Financial news processing pipeline.

    Handles:
    - News article parsing
    - Headline analysis
    - Source credibility weighting
    - Time decay for news impact
    """

    # Source credibility weights
    SOURCE_WEIGHTS = {
        "reuters": 1.0,
        "bloomberg": 1.0,
        "wsj": 0.95,
        "cnbc": 0.85,
        "marketwatch": 0.8,
        "seekingalpha": 0.7,
        "twitter": 0.5,
        "reddit": 0.4,
        "unknown": 0.3
    }

    def __init__(self, analyzer: SentimentAnalyzer, config: NLPConfig):
        self.analyzer = analyzer
        self.config = config

    def process_article(
        self,
        headline: str,
        body: Optional[str] = None,
        source: str = "unknown",
        timestamp: Optional[datetime] = None
    ) -> SentimentSignal:
        """
        Process a news article.

        Headlines are weighted more heavily than body text.
        """
        # Analyze headline
        headline_signal = self.analyzer.analyze(headline, source)

        if body:
            # Analyze body (first 500 chars for efficiency)
            body_signal = self.analyzer.analyze(body[:500], source)

            # Combine: headline weighted 70%, body 30%
            combined_score = 0.7 * headline_signal.raw_score + 0.3 * body_signal.raw_score
            combined_confidence = 0.7 * headline_signal.confidence + 0.3 * body_signal.confidence

            # Merge entities and topics
            entities = list(set(headline_signal.entities + body_signal.entities))
            topics = list(set(headline_signal.topics + body_signal.topics))
        else:
            combined_score = headline_signal.raw_score
            combined_confidence = headline_signal.confidence
            entities = headline_signal.entities
            topics = headline_signal.topics

        # Apply source credibility
        source_weight = self.SOURCE_WEIGHTS.get(source.lower(), 0.3)
        combined_confidence *= source_weight

        # Classify
        sentiment = self.analyzer._classify_sentiment(combined_score)

        return SentimentSignal(
            text=headline,
            sentiment=sentiment,
            confidence=combined_confidence,
            entities=entities,
            topics=topics,
            timestamp=timestamp or datetime.now(),
            source=source,
            raw_score=combined_score,
            metadata={"source_weight": source_weight}
        )

    def calculate_time_decay(self, signal_time: datetime, current_time: datetime) -> float:
        """Calculate time decay factor for news signal."""
        elapsed_seconds = (current_time - signal_time).total_seconds()
        half_life = self.config.aggregation_window  # seconds

        if elapsed_seconds < 0:
            return 1.0

        decay = np.power(self.config.decay_factor, elapsed_seconds / half_life)
        return decay


# =============================================================================
# Social Media Analyzer
# =============================================================================

class SocialMediaAnalyzer:
    """
    Social media sentiment analyzer.

    Handles:
    - Twitter/X posts
    - Reddit posts
    - Telegram messages
    - Influencer weighting
    """

    def __init__(self, analyzer: SentimentAnalyzer, config: NLPConfig):
        self.analyzer = analyzer
        self.config = config

        # Known crypto influencers (example)
        self.influencer_weights: Dict[str, float] = {
            # Would be populated with real influencer data
        }

    def process_post(
        self,
        text: str,
        platform: str,
        author: Optional[str] = None,
        followers: Optional[int] = None,
        engagement: Optional[Dict[str, int]] = None,
        timestamp: Optional[datetime] = None
    ) -> SentimentSignal:
        """
        Process a social media post.

        Args:
            text: Post content
            platform: Platform name
            author: Author username
            followers: Author's follower count
            engagement: Dict with likes, retweets, comments
            timestamp: Post timestamp
        """
        # Analyze text
        signal = self.analyzer.analyze(text, platform)

        # Calculate author weight
        author_weight = 1.0
        if author and author in self.influencer_weights:
            author_weight = self.influencer_weights[author]
        elif followers:
            # Weight by follower count (log scale)
            author_weight = min(2.0, np.log10(max(followers, 10)) / 5)

        # Calculate engagement weight
        engagement_weight = 1.0
        if engagement:
            total_engagement = sum(engagement.values())
            engagement_weight = min(2.0, np.log10(max(total_engagement, 1) + 1) / 3)

        # Adjust confidence
        combined_weight = author_weight * engagement_weight
        adjusted_confidence = signal.confidence * min(combined_weight, 1.5)

        return SentimentSignal(
            text=signal.text,
            sentiment=signal.sentiment,
            confidence=adjusted_confidence,
            entities=signal.entities,
            topics=signal.topics,
            timestamp=timestamp or signal.timestamp,
            source=platform,
            raw_score=signal.raw_score,
            metadata={
                "author": author,
                "followers": followers,
                "engagement": engagement,
                "author_weight": author_weight,
                "engagement_weight": engagement_weight
            }
        )


# =============================================================================
# Sentiment Aggregator
# =============================================================================

class SentimentAggregator:
    """
    Aggregates sentiment signals for assets.

    Produces time-weighted, source-weighted sentiment
    scores suitable for trading signals.
    """

    def __init__(self, config: NLPConfig):
        self.config = config
        self.signals: Dict[str, List[SentimentSignal]] = {}  # asset -> signals

    def add_signal(self, signal: SentimentSignal):
        """Add a sentiment signal."""
        for entity in signal.entities:
            if entity not in self.signals:
                self.signals[entity] = []
            self.signals[entity].append(signal)

    def get_aggregated_sentiment(
        self,
        asset: str,
        current_time: Optional[datetime] = None
    ) -> Optional[AggregatedSentiment]:
        """
        Get aggregated sentiment for an asset.

        Args:
            asset: Asset ticker
            current_time: Current timestamp for decay calculation

        Returns:
            AggregatedSentiment or None if no signals
        """
        if asset not in self.signals or not self.signals[asset]:
            return None

        current_time = current_time or datetime.now()
        signals = self.signals[asset]

        # Filter to recent signals
        window_seconds = self.config.aggregation_window * 24  # 24 hour window
        recent_signals = [
            s for s in signals
            if (current_time - s.timestamp).total_seconds() < window_seconds
        ]

        if not recent_signals:
            return None

        # Calculate time-weighted scores
        weighted_scores = []
        weights = []
        sources: Dict[str, float] = {}

        for signal in recent_signals:
            # Time decay
            elapsed = (current_time - signal.timestamp).total_seconds()
            time_weight = np.power(
                self.config.decay_factor,
                elapsed / self.config.aggregation_window
            )

            # Combined weight
            weight = time_weight * signal.confidence
            weighted_scores.append(signal.raw_score * weight)
            weights.append(weight)

            # Track source contributions
            if signal.source not in sources:
                sources[signal.source] = 0
            sources[signal.source] += weight

        total_weight = sum(weights)
        if total_weight == 0:
            return None

        # Weighted average sentiment
        sentiment_score = sum(weighted_scores) / total_weight

        # Normalize sources
        for source in sources:
            sources[source] /= total_weight

        # Calculate sentiment momentum
        momentum = self._calculate_momentum(recent_signals, current_time)

        # Bullish/bearish ratios
        bullish_count = sum(1 for s in recent_signals if s.raw_score > 0.1)
        bearish_count = sum(1 for s in recent_signals if s.raw_score < -0.1)
        total = len(recent_signals)

        return AggregatedSentiment(
            asset=asset,
            sentiment_score=np.clip(sentiment_score, -1, 1),
            confidence=min(1.0, total_weight / 10),
            n_signals=len(recent_signals),
            bullish_ratio=bullish_count / total if total > 0 else 0.5,
            bearish_ratio=bearish_count / total if total > 0 else 0.5,
            volume_weighted_score=sentiment_score,
            momentum=momentum,
            sources=sources,
            timestamp=current_time
        )

    def _calculate_momentum(
        self,
        signals: List[SentimentSignal],
        current_time: datetime
    ) -> float:
        """Calculate sentiment momentum (change over time)."""
        if len(signals) < 2:
            return 0.0

        # Sort by time
        sorted_signals = sorted(signals, key=lambda s: s.timestamp)

        # Split into recent and older
        mid_time = current_time - (current_time - sorted_signals[0].timestamp) / 2
        recent = [s for s in sorted_signals if s.timestamp > mid_time]
        older = [s for s in sorted_signals if s.timestamp <= mid_time]

        if not recent or not older:
            return 0.0

        recent_avg = np.mean([s.raw_score for s in recent])
        older_avg = np.mean([s.raw_score for s in older])

        return recent_avg - older_avg

    def clear_old_signals(self, max_age_hours: int = 24):
        """Remove signals older than max_age_hours."""
        cutoff = datetime.now()
        for asset in self.signals:
            self.signals[asset] = [
                s for s in self.signals[asset]
                if (cutoff - s.timestamp).total_seconds() < max_age_hours * 3600
            ]


# =============================================================================
# Main NLP Engine
# =============================================================================

class NLPEngine:
    """
    Main NLP engine orchestrating all components.

    Provides a unified interface for:
    - Processing various text sources
    - Generating sentiment signals
    - Aggregating sentiment for trading
    """

    def __init__(self, config: Optional[NLPConfig] = None):
        self.config = config or NLPConfig()

        self.analyzer = SentimentAnalyzer(self.config)
        self.news_processor = NewsProcessor(self.analyzer, self.config)
        self.social_processor = SocialMediaAnalyzer(self.analyzer, self.config)
        self.aggregator = SentimentAggregator(self.config)

    def process_text(self, text: str, source: str = "unknown") -> SentimentSignal:
        """Process generic text."""
        signal = self.analyzer.analyze(text, source)
        self.aggregator.add_signal(signal)
        return signal

    def process_news(
        self,
        headline: str,
        body: Optional[str] = None,
        source: str = "unknown",
        timestamp: Optional[datetime] = None
    ) -> SentimentSignal:
        """Process news article."""
        signal = self.news_processor.process_article(headline, body, source, timestamp)
        self.aggregator.add_signal(signal)
        return signal

    def process_social_post(
        self,
        text: str,
        platform: str,
        author: Optional[str] = None,
        followers: Optional[int] = None,
        engagement: Optional[Dict[str, int]] = None,
        timestamp: Optional[datetime] = None
    ) -> SentimentSignal:
        """Process social media post."""
        signal = self.social_processor.process_post(
            text, platform, author, followers, engagement, timestamp
        )
        self.aggregator.add_signal(signal)
        return signal

    def get_sentiment(self, asset: str) -> Optional[AggregatedSentiment]:
        """Get aggregated sentiment for asset."""
        return self.aggregator.get_aggregated_sentiment(asset)

    def get_trading_signal(self, asset: str) -> Dict[str, Any]:
        """
        Get trading signal based on sentiment.

        Returns dict with:
        - signal: 'buy', 'sell', or 'hold'
        - strength: 0 to 1
        - confidence: 0 to 1
        """
        sentiment = self.get_sentiment(asset)

        if sentiment is None:
            return {
                "signal": "hold",
                "strength": 0.0,
                "confidence": 0.0,
                "sentiment_score": 0.0
            }

        # Determine signal
        threshold = self.config.sentiment_threshold

        if sentiment.sentiment_score > threshold:
            signal = "buy"
            strength = min(1.0, sentiment.sentiment_score / (threshold * 2))
        elif sentiment.sentiment_score < -threshold:
            signal = "sell"
            strength = min(1.0, abs(sentiment.sentiment_score) / (threshold * 2))
        else:
            signal = "hold"
            strength = 0.0

        # Adjust for momentum
        if sentiment.momentum > 0.1 and signal == "buy":
            strength = min(1.0, strength * 1.2)
        elif sentiment.momentum < -0.1 and signal == "sell":
            strength = min(1.0, strength * 1.2)

        return {
            "signal": signal,
            "strength": strength,
            "confidence": sentiment.confidence,
            "sentiment_score": sentiment.sentiment_score,
            "momentum": sentiment.momentum,
            "n_signals": sentiment.n_signals,
            "bullish_ratio": sentiment.bullish_ratio,
            "bearish_ratio": sentiment.bearish_ratio
        }

    def batch_process(self, texts: List[Dict[str, Any]]) -> List[SentimentSignal]:
        """
        Process multiple texts in batch.

        Args:
            texts: List of dicts with 'text', 'source', 'type' keys

        Returns:
            List of SentimentSignals
        """
        signals = []

        for item in texts:
            text = item.get("text", "")
            source = item.get("source", "unknown")
            text_type = item.get("type", "generic")
            timestamp = item.get("timestamp")

            if text_type == "news":
                signal = self.process_news(
                    headline=text,
                    body=item.get("body"),
                    source=source,
                    timestamp=timestamp
                )
            elif text_type == "social":
                signal = self.process_social_post(
                    text=text,
                    platform=source,
                    author=item.get("author"),
                    followers=item.get("followers"),
                    engagement=item.get("engagement"),
                    timestamp=timestamp
                )
            else:
                signal = self.process_text(text, source)

            signals.append(signal)

        return signals


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Create NLP engine
    config = NLPConfig(
        embedding_dim=64,
        sentiment_threshold=0.25
    )
    engine = NLPEngine(config)

    # Sample texts
    texts = [
        {
            "text": "$BTC breaking out! Massive volume spike, this is going to moon! 🚀",
            "source": "twitter",
            "type": "social",
            "followers": 50000
        },
        {
            "text": "Bitcoin faces resistance at $45k, bears gaining control",
            "source": "reuters",
            "type": "news"
        },
        {
            "text": "Very bullish on $ETH after the upgrade announcement",
            "source": "reddit",
            "type": "social"
        },
        {
            "text": "Market crash incoming, sell everything now!",
            "source": "twitter",
            "type": "social",
            "followers": 1000
        }
    ]

    # Process texts
    print("Processing texts...\n")
    for item in texts:
        if item.get("type") == "social":
            signal = engine.process_social_post(
                text=item["text"],
                platform=item["source"],
                followers=item.get("followers")
            )
        else:
            signal = engine.process_news(
                headline=item["text"],
                source=item["source"]
            )

        print(f"Text: {item['text'][:50]}...")
        print(f"  Sentiment: {signal.sentiment.name}")
        print(f"  Confidence: {signal.confidence:.2f}")
        print(f"  Entities: {signal.entities}")
        print()

    # Get aggregated sentiment
    print("\nAggregated Sentiment:")
    for asset in ["BTC", "ETH"]:
        sentiment = engine.get_sentiment(asset)
        if sentiment:
            print(f"\n{asset}:")
            print(f"  Score: {sentiment.sentiment_score:.3f}")
            print(f"  Confidence: {sentiment.confidence:.3f}")
            print(f"  Signals: {sentiment.n_signals}")
            print(f"  Bullish ratio: {sentiment.bullish_ratio:.2f}")

    # Get trading signals
    print("\nTrading Signals:")
    for asset in ["BTC", "ETH"]:
        signal = engine.get_trading_signal(asset)
        print(f"\n{asset}: {signal['signal'].upper()}")
        print(f"  Strength: {signal['strength']:.2f}")
        print(f"  Confidence: {signal['confidence']:.2f}")
