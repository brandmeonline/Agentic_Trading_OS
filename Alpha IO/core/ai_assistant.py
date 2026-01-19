"""
Agentic Trading OS - AI Trading Assistant.

Next-generation AI-powered trading assistant with:
- Natural language interface for trading commands
- Market analysis and insights
- Portfolio recommendations
- Risk assessment
- Sentiment analysis integration
- Autonomous trading suggestions
"""

from __future__ import annotations

import re
import json
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple
from enum import Enum
import threading


class IntentType(Enum):
    """User intent classification."""
    TRADE = "trade"
    ANALYSIS = "analysis"
    PORTFOLIO = "portfolio"
    PRICE = "price"
    ALERT = "alert"
    STRATEGY = "strategy"
    RISK = "risk"
    NEWS = "news"
    HELP = "help"
    UNKNOWN = "unknown"


class ActionType(Enum):
    """Action types for trading commands."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    ANALYZE = "analyze"
    SET_ALERT = "set_alert"
    GET_PRICE = "get_price"
    GET_PORTFOLIO = "get_portfolio"
    GET_RISK = "get_risk"
    EXPLAIN = "explain"


@dataclass
class ParsedCommand:
    """Parsed natural language command."""
    intent: IntentType
    action: Optional[ActionType] = None
    symbol: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    condition: Optional[str] = None
    timeframe: Optional[str] = None
    confidence: float = 0.0
    raw_text: str = ""
    entities: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AIResponse:
    """AI assistant response."""
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    actions: List[Dict] = field(default_factory=list)
    confidence: float = 0.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class ConversationContext:
    """Conversation context for multi-turn interactions."""
    session_id: str = ""
    messages: List[Dict] = field(default_factory=list)
    current_topic: str = ""
    active_symbols: List[str] = field(default_factory=list)
    pending_action: Optional[Dict] = None
    user_preferences: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    last_activity: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        self.last_activity = datetime.now().isoformat()

    def add_message(self, role: str, content: str):
        """Add message to conversation."""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_activity = datetime.now().isoformat()
        # Keep last 20 messages for context
        if len(self.messages) > 20:
            self.messages = self.messages[-20:]


class NLPProcessor:
    """Natural Language Processing for trading commands."""

    # Symbol patterns
    SYMBOL_PATTERNS = [
        r'\b([A-Z]{1,5})\b',  # Stock symbols
        r'\b(BTC|ETH|SOL|ADA|DOT|AVAX|MATIC|LINK|UNI|AAVE)(/USD)?\b',  # Crypto
        r'\$([A-Z]{1,5})\b',  # $SYMBOL format
    ]

    # Quantity patterns
    QUANTITY_PATTERNS = [
        r'(\d+(?:\.\d+)?)\s*(?:shares?|units?|coins?|tokens?)',
        r'(?:buy|sell)\s+(\d+(?:\.\d+)?)\s+',
        r'(\d+(?:\.\d+)?)\s+(?:of|worth)',
        r'\$(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:worth|of)',
    ]

    # Price patterns
    PRICE_PATTERNS = [
        r'(?:at|@|price)\s*\$?(\d+(?:\.\d+)?)',
        r'\$(\d+(?:\.\d+)?)\s*(?:each|per)',
        r'limit\s+(?:at\s+)?\$?(\d+(?:\.\d+)?)',
    ]

    # Intent keywords
    INTENT_KEYWORDS = {
        IntentType.TRADE: [
            'buy', 'sell', 'trade', 'purchase', 'acquire', 'dump', 'long', 'short',
            'execute', 'order', 'position', 'enter', 'exit', 'close'
        ],
        IntentType.ANALYSIS: [
            'analyze', 'analysis', 'technical', 'fundamental', 'chart', 'trend',
            'pattern', 'indicator', 'signal', 'outlook', 'forecast', 'predict'
        ],
        IntentType.PORTFOLIO: [
            'portfolio', 'holdings', 'positions', 'balance', 'allocation',
            'diversification', 'exposure', 'my stocks', 'my crypto'
        ],
        IntentType.PRICE: [
            'price', 'quote', 'cost', 'value', 'worth', 'trading at', 'current'
        ],
        IntentType.ALERT: [
            'alert', 'notify', 'notification', 'watch', 'monitor', 'when',
            'if price', 'reaches', 'hits', 'drops', 'rises'
        ],
        IntentType.STRATEGY: [
            'strategy', 'bot', 'automate', 'backtest', 'optimize', 'algorithm',
            'dca', 'grid', 'momentum', 'mean reversion'
        ],
        IntentType.RISK: [
            'risk', 'exposure', 'var', 'volatility', 'drawdown', 'stop loss',
            'take profit', 'hedge', 'protect', 'safe'
        ],
        IntentType.NEWS: [
            'news', 'sentiment', 'social', 'twitter', 'reddit', 'headlines',
            'events', 'earnings', 'announcement'
        ],
        IntentType.HELP: [
            'help', 'how', 'what', 'explain', 'tutorial', 'guide', 'learn'
        ]
    }

    def __init__(self):
        self._compiled_patterns = {}
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns."""
        for pattern_list in [self.SYMBOL_PATTERNS, self.QUANTITY_PATTERNS, self.PRICE_PATTERNS]:
            for pattern in pattern_list:
                if pattern not in self._compiled_patterns:
                    self._compiled_patterns[pattern] = re.compile(pattern, re.IGNORECASE)

    def parse(self, text: str) -> ParsedCommand:
        """Parse natural language command."""
        text_lower = text.lower().strip()

        # Classify intent
        intent, confidence = self._classify_intent(text_lower)

        # Extract entities
        symbol = self._extract_symbol(text)
        quantity = self._extract_quantity(text)
        price = self._extract_price(text)
        action = self._extract_action(text_lower, intent)
        timeframe = self._extract_timeframe(text_lower)

        return ParsedCommand(
            intent=intent,
            action=action,
            symbol=symbol,
            quantity=quantity,
            price=price,
            timeframe=timeframe,
            confidence=confidence,
            raw_text=text,
            entities={
                "symbol": symbol,
                "quantity": quantity,
                "price": price,
                "timeframe": timeframe
            }
        )

    def _classify_intent(self, text: str) -> Tuple[IntentType, float]:
        """Classify user intent."""
        scores = {}

        for intent, keywords in self.INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scores[intent] = score

        if not scores:
            return IntentType.UNKNOWN, 0.0

        best_intent = max(scores, key=scores.get)
        confidence = min(scores[best_intent] / 3.0, 1.0)

        return best_intent, confidence

    def _extract_symbol(self, text: str) -> Optional[str]:
        """Extract trading symbol from text."""
        for pattern in self.SYMBOL_PATTERNS:
            match = self._compiled_patterns[pattern].search(text)
            if match:
                symbol = match.group(1).upper()
                # Filter out common words
                if symbol not in ['I', 'A', 'THE', 'AND', 'FOR', 'TO', 'OF', 'IS', 'IT', 'AT', 'OR', 'IF', 'MY', 'ME', 'BE', 'SO', 'NO', 'ON', 'UP', 'BY']:
                    return symbol
        return None

    def _extract_quantity(self, text: str) -> Optional[float]:
        """Extract quantity from text."""
        for pattern in self.QUANTITY_PATTERNS:
            match = self._compiled_patterns[pattern].search(text)
            if match:
                try:
                    qty = match.group(1).replace(',', '')
                    return float(qty)
                except (ValueError, IndexError):
                    pass
        return None

    def _extract_price(self, text: str) -> Optional[float]:
        """Extract price from text."""
        for pattern in self.PRICE_PATTERNS:
            match = self._compiled_patterns[pattern].search(text)
            if match:
                try:
                    return float(match.group(1).replace(',', ''))
                except (ValueError, IndexError):
                    pass
        return None

    def _extract_action(self, text: str, intent: IntentType) -> Optional[ActionType]:
        """Extract action type."""
        if intent == IntentType.TRADE:
            if any(w in text for w in ['buy', 'purchase', 'acquire', 'long']):
                return ActionType.BUY
            elif any(w in text for w in ['sell', 'dump', 'short', 'exit', 'close']):
                return ActionType.SELL
        elif intent == IntentType.ANALYSIS:
            return ActionType.ANALYZE
        elif intent == IntentType.PRICE:
            return ActionType.GET_PRICE
        elif intent == IntentType.PORTFOLIO:
            return ActionType.GET_PORTFOLIO
        elif intent == IntentType.RISK:
            return ActionType.GET_RISK
        elif intent == IntentType.ALERT:
            return ActionType.SET_ALERT
        return None

    def _extract_timeframe(self, text: str) -> Optional[str]:
        """Extract timeframe from text."""
        timeframe_map = {
            '1m': ['1 minute', '1min', '1m'],
            '5m': ['5 minute', '5min', '5m'],
            '15m': ['15 minute', '15min', '15m'],
            '1h': ['1 hour', '1hr', '1h', 'hourly'],
            '4h': ['4 hour', '4hr', '4h'],
            '1d': ['1 day', 'daily', '1d', 'day'],
            '1w': ['1 week', 'weekly', '1w', 'week'],
        }

        for tf, patterns in timeframe_map.items():
            if any(p in text for p in patterns):
                return tf
        return None


class MarketAnalyzer:
    """AI-powered market analysis engine."""

    def __init__(self):
        self.analysis_cache = {}
        self._lock = threading.Lock()

    def analyze_symbol(self, symbol: str, price_history: List[float] = None) -> Dict:
        """Perform comprehensive analysis on a symbol."""
        if not price_history:
            price_history = self._generate_mock_history()

        analysis = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "technical": self._technical_analysis(price_history),
            "sentiment": self._sentiment_score(),
            "risk_metrics": self._calculate_risk_metrics(price_history),
            "ai_prediction": self._generate_prediction(price_history),
            "support_resistance": self._find_support_resistance(price_history),
            "recommendation": self._generate_recommendation(price_history)
        }

        with self._lock:
            self.analysis_cache[symbol] = analysis

        return analysis

    def _technical_analysis(self, prices: List[float]) -> Dict:
        """Technical indicator analysis."""
        if len(prices) < 20:
            return {"error": "Insufficient data"}

        # Calculate basic indicators
        sma_20 = sum(prices[-20:]) / 20
        sma_50 = sum(prices[-50:]) / 50 if len(prices) >= 50 else sma_20
        current = prices[-1]

        # RSI calculation
        gains = []
        losses = []
        for i in range(1, min(15, len(prices))):
            diff = prices[-i] - prices[-i-1]
            if diff > 0:
                gains.append(diff)
            else:
                losses.append(abs(diff))

        avg_gain = sum(gains) / 14 if gains else 0.001
        avg_loss = sum(losses) / 14 if losses else 0.001
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))

        # Trend
        if current > sma_20 > sma_50:
            trend = "bullish"
            trend_strength = min((current - sma_50) / sma_50 * 100, 100)
        elif current < sma_20 < sma_50:
            trend = "bearish"
            trend_strength = min((sma_50 - current) / sma_50 * 100, 100)
        else:
            trend = "neutral"
            trend_strength = 50

        return {
            "trend": trend,
            "trend_strength": round(trend_strength, 1),
            "sma_20": round(sma_20, 2),
            "sma_50": round(sma_50, 2),
            "rsi": round(rsi, 1),
            "rsi_signal": "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral",
            "momentum": "positive" if current > prices[-5] else "negative",
            "volatility": self._calculate_volatility(prices)
        }

    def _sentiment_score(self) -> Dict:
        """Simulated sentiment analysis."""
        # In production, integrate with Twitter/Reddit/News APIs
        social_score = random.uniform(30, 80)
        news_score = random.uniform(40, 70)

        return {
            "overall": round((social_score + news_score) / 2, 1),
            "social": round(social_score, 1),
            "news": round(news_score, 1),
            "fear_greed_index": random.randint(25, 75),
            "trending": random.choice([True, False]),
            "mentions_24h": random.randint(100, 10000)
        }

    def _calculate_risk_metrics(self, prices: List[float]) -> Dict:
        """Calculate risk metrics."""
        if len(prices) < 10:
            return {}

        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]

        # Volatility
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        volatility = variance ** 0.5 * (252 ** 0.5)  # Annualized

        # Max drawdown
        peak = prices[0]
        max_dd = 0
        for price in prices:
            if price > peak:
                peak = price
            dd = (peak - price) / peak
            if dd > max_dd:
                max_dd = dd

        # VaR (simplified 95%)
        sorted_returns = sorted(returns)
        var_index = int(len(sorted_returns) * 0.05)
        var_95 = sorted_returns[var_index] if var_index < len(sorted_returns) else sorted_returns[0]

        return {
            "volatility": round(volatility * 100, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "var_95": round(var_95 * 100, 2),
            "sharpe_estimate": round(mean_return * 252 / (volatility if volatility > 0 else 0.01), 2),
            "risk_level": "high" if volatility > 0.5 else "medium" if volatility > 0.25 else "low"
        }

    def _calculate_volatility(self, prices: List[float]) -> str:
        """Calculate volatility level."""
        if len(prices) < 5:
            return "unknown"

        returns = [abs(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        avg_return = sum(returns) / len(returns)

        if avg_return > 0.03:
            return "very high"
        elif avg_return > 0.02:
            return "high"
        elif avg_return > 0.01:
            return "moderate"
        return "low"

    def _generate_prediction(self, prices: List[float]) -> Dict:
        """Generate AI price prediction."""
        if len(prices) < 20:
            return {"error": "Insufficient data"}

        current = prices[-1]

        # Simple momentum-based prediction
        short_trend = (prices[-1] - prices[-5]) / prices[-5] if len(prices) >= 5 else 0
        medium_trend = (prices[-1] - prices[-20]) / prices[-20] if len(prices) >= 20 else 0

        # Prediction factors
        trend_factor = (short_trend * 0.6 + medium_trend * 0.4)
        noise = random.uniform(-0.02, 0.02)

        # Price targets
        predicted_1d = current * (1 + trend_factor * 0.1 + noise)
        predicted_1w = current * (1 + trend_factor * 0.3 + noise * 2)
        predicted_1m = current * (1 + trend_factor * 0.8 + noise * 3)

        confidence = max(30, min(85, 60 + abs(trend_factor) * 500))

        return {
            "current_price": round(current, 2),
            "prediction_1d": round(predicted_1d, 2),
            "prediction_1w": round(predicted_1w, 2),
            "prediction_1m": round(predicted_1m, 2),
            "direction": "up" if trend_factor > 0 else "down",
            "confidence": round(confidence, 1),
            "model": "momentum_ml_v2"
        }

    def _find_support_resistance(self, prices: List[float]) -> Dict:
        """Find support and resistance levels."""
        if len(prices) < 20:
            return {}

        current = prices[-1]

        # Simple pivot points
        high = max(prices[-20:])
        low = min(prices[-20:])
        close = prices[-1]

        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low
        r2 = pivot + (high - low)
        s1 = 2 * pivot - high
        s2 = pivot - (high - low)

        return {
            "pivot": round(pivot, 2),
            "resistance_1": round(r1, 2),
            "resistance_2": round(r2, 2),
            "support_1": round(s1, 2),
            "support_2": round(s2, 2),
            "nearest_support": round(s1, 2),
            "nearest_resistance": round(r1, 2),
            "distance_to_support": round((current - s1) / current * 100, 2),
            "distance_to_resistance": round((r1 - current) / current * 100, 2)
        }

    def _generate_recommendation(self, prices: List[float]) -> Dict:
        """Generate trading recommendation."""
        tech = self._technical_analysis(prices)
        risk = self._calculate_risk_metrics(prices)

        # Score calculation
        trend_score = 60 if tech.get("trend") == "bullish" else 40 if tech.get("trend") == "bearish" else 50
        rsi = tech.get("rsi", 50)
        rsi_score = 70 if rsi < 30 else 30 if rsi > 70 else 50

        total_score = (trend_score * 0.4 + rsi_score * 0.3 + 50 * 0.3)

        if total_score > 60:
            action = "BUY"
            reasoning = "Bullish trend with favorable RSI"
        elif total_score < 40:
            action = "SELL"
            reasoning = "Bearish trend with unfavorable conditions"
        else:
            action = "HOLD"
            reasoning = "Mixed signals, wait for confirmation"

        return {
            "action": action,
            "score": round(total_score, 1),
            "confidence": round(min(abs(total_score - 50) * 2, 90), 1),
            "reasoning": reasoning,
            "risk_reward": round(random.uniform(1.5, 3.5), 2),
            "suggested_stop_loss": round(prices[-1] * 0.95, 2),
            "suggested_take_profit": round(prices[-1] * 1.10, 2)
        }

    def _generate_mock_history(self) -> List[float]:
        """Generate mock price history for testing."""
        base = 100
        prices = [base]
        for _ in range(100):
            change = random.uniform(-0.03, 0.035)
            prices.append(prices[-1] * (1 + change))
        return prices


class AITradingAssistant:
    """Main AI Trading Assistant class."""

    def __init__(self):
        self.nlp = NLPProcessor()
        self.analyzer = MarketAnalyzer()
        self.conversations: Dict[str, ConversationContext] = {}
        self._lock = threading.Lock()

        # Response templates
        self.templates = {
            "trade_confirm": "I'll execute a {action} order for {quantity} {symbol} at {price}. Shall I proceed?",
            "price_response": "{symbol} is currently trading at ${price}. {trend_info}",
            "analysis_response": "Here's my analysis for {symbol}:\n\n{analysis}",
            "portfolio_response": "Your portfolio summary:\n{summary}",
            "alert_set": "Alert created! I'll notify you when {symbol} {condition}.",
            "help_response": "I can help you with:\n- Trading: 'Buy 10 AAPL at $150'\n- Analysis: 'Analyze BTC'\n- Portfolio: 'Show my portfolio'\n- Alerts: 'Alert me when ETH hits $3000'\n- Risk: 'What's my portfolio risk?'",
            "unknown": "I'm not sure I understand. Could you rephrase that? Try 'help' for examples."
        }

    def get_or_create_session(self, session_id: str) -> ConversationContext:
        """Get or create conversation context."""
        with self._lock:
            if session_id not in self.conversations:
                self.conversations[session_id] = ConversationContext(session_id=session_id)
            return self.conversations[session_id]

    def process_message(self, message: str, session_id: str = "default") -> AIResponse:
        """Process user message and generate response."""
        context = self.get_or_create_session(session_id)
        context.add_message("user", message)

        # Parse the command
        command = self.nlp.parse(message)

        # Generate response based on intent
        response = self._handle_intent(command, context)

        context.add_message("assistant", response.message)
        return response

    def _handle_intent(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle different intents."""
        handlers = {
            IntentType.TRADE: self._handle_trade,
            IntentType.ANALYSIS: self._handle_analysis,
            IntentType.PORTFOLIO: self._handle_portfolio,
            IntentType.PRICE: self._handle_price,
            IntentType.ALERT: self._handle_alert,
            IntentType.STRATEGY: self._handle_strategy,
            IntentType.RISK: self._handle_risk,
            IntentType.NEWS: self._handle_news,
            IntentType.HELP: self._handle_help,
            IntentType.UNKNOWN: self._handle_unknown
        }

        handler = handlers.get(command.intent, self._handle_unknown)
        return handler(command, context)

    def _handle_trade(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle trade intent."""
        if not command.symbol:
            return AIResponse(
                message="Which asset would you like to trade? Please specify a symbol.",
                suggestions=["Buy 10 AAPL", "Sell 5 SPY", "Trade BTC"],
                confidence=0.5
            )

        if not command.quantity:
            return AIResponse(
                message=f"How much {command.symbol} would you like to {command.action.value if command.action else 'trade'}?",
                suggestions=[f"Buy 10 {command.symbol}", f"Buy $1000 of {command.symbol}"],
                confidence=0.6
            )

        action = command.action.value if command.action else "trade"
        price_str = f"at ${command.price}" if command.price else "at market price"

        # Create pending action
        pending = {
            "action": action,
            "symbol": command.symbol,
            "quantity": command.quantity,
            "price": command.price,
            "type": "limit" if command.price else "market"
        }
        context.pending_action = pending

        return AIResponse(
            message=f"Ready to {action} {command.quantity} {command.symbol} {price_str}. Confirm with 'yes' or 'execute'.",
            data=pending,
            actions=[{"type": "confirm_trade", "data": pending}],
            suggestions=["Yes, execute", "Cancel", "Change quantity"],
            confidence=command.confidence
        )

    def _handle_analysis(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle analysis intent."""
        symbol = command.symbol or (context.active_symbols[0] if context.active_symbols else "SPY")

        analysis = self.analyzer.analyze_symbol(symbol)

        # Format analysis
        tech = analysis["technical"]
        pred = analysis["ai_prediction"]
        rec = analysis["recommendation"]

        message = f"""
**{symbol} Analysis**

**Technical:**
- Trend: {tech.get('trend', 'N/A').upper()} (strength: {tech.get('trend_strength', 0)}%)
- RSI: {tech.get('rsi', 'N/A')} ({tech.get('rsi_signal', 'N/A')})
- Volatility: {tech.get('volatility', 'N/A')}

**AI Prediction:**
- 1 Day: ${pred.get('prediction_1d', 'N/A')} ({pred.get('direction', 'N/A')})
- 1 Week: ${pred.get('prediction_1w', 'N/A')}
- Confidence: {pred.get('confidence', 0)}%

**Recommendation:** {rec.get('action', 'HOLD')}
- {rec.get('reasoning', '')}
- Risk/Reward: {rec.get('risk_reward', 'N/A')}
- Stop Loss: ${rec.get('suggested_stop_loss', 'N/A')}
- Take Profit: ${rec.get('suggested_take_profit', 'N/A')}
"""

        context.active_symbols = [symbol]

        return AIResponse(
            message=message.strip(),
            data=analysis,
            suggestions=[f"Buy {symbol}", f"Set alert for {symbol}", "Analyze another"],
            confidence=0.85
        )

    def _handle_portfolio(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle portfolio intent."""
        # Mock portfolio data
        portfolio = {
            "total_value": 125000.00,
            "cash": 15000.00,
            "positions": [
                {"symbol": "AAPL", "qty": 50, "value": 8750, "pnl": 450, "pnl_pct": 5.4},
                {"symbol": "SPY", "qty": 100, "value": 45000, "pnl": 1200, "pnl_pct": 2.7},
                {"symbol": "BTC/USD", "qty": 0.5, "value": 21000, "pnl": -800, "pnl_pct": -3.7},
            ],
            "daily_pnl": 850,
            "total_pnl": 5200,
            "allocation": {"stocks": 65, "crypto": 20, "cash": 15}
        }

        positions_str = "\n".join([
            f"  - {p['symbol']}: {p['qty']} units (${p['value']:,.0f}) | P&L: ${p['pnl']:+,.0f} ({p['pnl_pct']:+.1f}%)"
            for p in portfolio['positions']
        ])

        message = f"""
**Portfolio Summary**

**Total Value:** ${portfolio['total_value']:,.2f}
**Cash Available:** ${portfolio['cash']:,.2f}
**Today's P&L:** ${portfolio['daily_pnl']:+,.2f}
**Total P&L:** ${portfolio['total_pnl']:+,.2f}

**Positions:**
{positions_str}

**Allocation:** Stocks {portfolio['allocation']['stocks']}% | Crypto {portfolio['allocation']['crypto']}% | Cash {portfolio['allocation']['cash']}%
"""

        return AIResponse(
            message=message.strip(),
            data=portfolio,
            suggestions=["Analyze my risk", "Rebalance portfolio", "Show trades"],
            confidence=0.9
        )

    def _handle_price(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle price intent."""
        symbol = command.symbol or "SPY"

        # Mock price data
        price = random.uniform(100, 500)
        change = random.uniform(-5, 5)
        change_pct = change / price * 100

        message = f"{symbol} is trading at **${price:.2f}** ({change_pct:+.2f}% today)"

        context.active_symbols = [symbol]

        return AIResponse(
            message=message,
            data={"symbol": symbol, "price": price, "change": change, "change_pct": change_pct},
            suggestions=[f"Buy {symbol}", f"Analyze {symbol}", f"Set alert for {symbol}"],
            confidence=0.95
        )

    def _handle_alert(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle alert intent."""
        symbol = command.symbol or (context.active_symbols[0] if context.active_symbols else None)

        if not symbol:
            return AIResponse(
                message="Which asset should I monitor? Please specify a symbol.",
                suggestions=["Alert me when AAPL hits $200", "Watch BTC at $50000"],
                confidence=0.5
            )

        if not command.price:
            return AIResponse(
                message=f"At what price should I alert you for {symbol}?",
                suggestions=[f"Alert at $150", f"When {symbol} drops 5%"],
                confidence=0.6
            )

        return AIResponse(
            message=f"Alert set! I'll notify you when {symbol} reaches ${command.price:.2f}.",
            data={"symbol": symbol, "price": command.price, "type": "price_alert"},
            actions=[{"type": "create_alert", "symbol": symbol, "price": command.price}],
            suggestions=["Set another alert", "View all alerts"],
            confidence=0.9
        )

    def _handle_strategy(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle strategy intent."""
        strategies = [
            {"name": "Momentum Pro", "return": 23.5, "risk": "medium"},
            {"name": "Mean Reversion", "return": 18.2, "risk": "low"},
            {"name": "AI Trend Follower", "return": 31.2, "risk": "high"},
        ]

        message = """
**Available Strategies:**

1. **Momentum Pro** - +23.5% annual return | Medium risk
   - Follows strong price momentum with ML signals

2. **Mean Reversion** - +18.2% annual return | Low risk
   - Trades oversold/overbought conditions

3. **AI Trend Follower** - +31.2% annual return | High risk
   - Deep learning model for trend prediction

Would you like to activate or backtest any strategy?
"""

        return AIResponse(
            message=message.strip(),
            data={"strategies": strategies},
            suggestions=["Backtest Momentum Pro", "Activate AI Trend Follower", "Create custom strategy"],
            confidence=0.8
        )

    def _handle_risk(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle risk intent."""
        risk_data = {
            "portfolio_var_95": -3.2,
            "max_drawdown": -12.5,
            "sharpe_ratio": 1.45,
            "beta": 1.12,
            "correlation_to_spy": 0.78,
            "concentration_risk": "medium",
            "recommendations": [
                "Consider adding bonds to reduce volatility",
                "BTC position adds 40% of total risk",
                "Well diversified across sectors"
            ]
        }

        message = f"""
**Portfolio Risk Analysis**

**Key Metrics:**
- VaR (95%): {risk_data['portfolio_var_95']:.1f}% (max daily loss)
- Max Drawdown: {risk_data['max_drawdown']:.1f}%
- Sharpe Ratio: {risk_data['sharpe_ratio']:.2f}
- Beta: {risk_data['beta']:.2f}
- S&P Correlation: {risk_data['correlation_to_spy']:.0%}

**Risk Level:** {risk_data['concentration_risk'].upper()}

**Recommendations:**
- {risk_data['recommendations'][0]}
- {risk_data['recommendations'][1]}
- {risk_data['recommendations'][2]}
"""

        return AIResponse(
            message=message.strip(),
            data=risk_data,
            suggestions=["Reduce risk", "Hedge portfolio", "Rebalance"],
            confidence=0.85
        )

    def _handle_news(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle news/sentiment intent."""
        symbol = command.symbol or "Market"

        news = {
            "sentiment": "neutral",
            "score": 52,
            "headlines": [
                {"title": "Fed signals potential rate pause", "sentiment": "positive"},
                {"title": "Tech earnings beat expectations", "sentiment": "positive"},
                {"title": "Global growth concerns persist", "sentiment": "negative"},
            ],
            "social_mentions": 5420,
            "trending": True
        }

        headlines_str = "\n".join([f"  - {h['title']} ({h['sentiment']})" for h in news['headlines']])

        message = f"""
**{symbol} Sentiment Analysis**

**Overall Sentiment:** {news['sentiment'].upper()} (Score: {news['score']}/100)
**Social Mentions (24h):** {news['social_mentions']:,}
**Trending:** {"Yes" if news['trending'] else "No"}

**Recent Headlines:**
{headlines_str}
"""

        return AIResponse(
            message=message.strip(),
            data=news,
            suggestions=["Show more news", "Analyze impact", "Set news alert"],
            confidence=0.75
        )

    def _handle_help(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle help intent."""
        message = """
**AI Trading Assistant - Help**

I can help you with:

**Trading:**
- "Buy 10 AAPL" or "Sell 5 SPY at $450"
- "Long BTC" or "Short ETH"

**Analysis:**
- "Analyze TSLA" or "Technical analysis on BTC"
- "What's the trend for SPY?"

**Portfolio:**
- "Show my portfolio" or "What are my holdings?"
- "Portfolio performance"

**Prices:**
- "Price of AAPL" or "How much is Bitcoin?"

**Alerts:**
- "Alert me when ETH hits $3000"
- "Notify if AAPL drops 5%"

**Risk:**
- "What's my portfolio risk?"
- "Risk analysis"

**Strategies:**
- "Show strategies" or "Backtest momentum"

Just type naturally - I'll understand!
"""

        return AIResponse(
            message=message.strip(),
            suggestions=["Analyze SPY", "Show portfolio", "Price of BTC"],
            confidence=1.0
        )

    def _handle_unknown(self, command: ParsedCommand, context: ConversationContext) -> AIResponse:
        """Handle unknown intent."""
        return AIResponse(
            message="I'm not sure I understand that. Could you try rephrasing? Type 'help' for examples of what I can do.",
            suggestions=["Help", "Show portfolio", "Analyze SPY"],
            confidence=0.3
        )


# =============================================================================
# Singleton Factory
# =============================================================================

_assistant: Optional[AITradingAssistant] = None


def get_ai_assistant() -> AITradingAssistant:
    """Get or create the AI assistant singleton."""
    global _assistant
    if _assistant is None:
        _assistant = AITradingAssistant()
    return _assistant
