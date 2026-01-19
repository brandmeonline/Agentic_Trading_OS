# Agentic Trading OS - API Documentation

This document provides comprehensive API documentation for the Agentic Trading OS platform.

## Table of Contents

- [Authentication](#authentication)
- [Core Trading APIs](#core-trading-apis)
- [Alert APIs](#alert-apis)
- [Indicator APIs](#indicator-apis)
- [Marketplace APIs](#marketplace-apis)
- [AI Assistant APIs](#ai-assistant-apis)
- [Blockchain/DeFi APIs](#blockchaindefi-apis)
- [Analytics APIs](#analytics-apis)
- [Settings APIs](#settings-apis)
- [Error Handling](#error-handling)

---

## Authentication

All API endpoints require authentication via session cookie.

### Login
```
POST /login
Content-Type: application/x-www-form-urlencoded

username=admin&password=admin
```

### Logout
```
GET /logout
```

---

## Core Trading APIs

### Get Trading Stats
```
GET /api/stats

Response:
{
  "is_running": false,
  "current_capital": 100000.0,
  "initial_capital": 100000.0,
  "total_pnl": 0.0,
  "pnl_percent": 0.0,
  "total_trades": 0,
  "win_rate": 0.0,
  "positions_count": 0
}
```

### Get Current Prices
```
GET /api/prices

Response:
{
  "AAPL": 185.50,
  "GOOGL": 142.30,
  "BTC/USD": 67500.00
}
```

### Get Price History
```
GET /api/price-history/<symbol>

Response:
{
  "symbol": "AAPL",
  "prices": [184.20, 185.00, 185.50],
  "timestamps": ["2024-01-15T10:00:00", ...]
}
```

### Get Positions
```
GET /api/positions

Response:
[
  {
    "symbol": "AAPL",
    "qty": 10,
    "side": "buy",
    "entry_price": 180.00,
    "current_price": 185.50,
    "market_value": 1855.00,
    "unrealized_pl": 55.00
  }
]
```

### Get Orders
```
GET /api/orders

Response:
[
  {
    "id": "order_123",
    "symbol": "AAPL",
    "side": "buy",
    "qty": 10,
    "type": "limit",
    "limit_price": 180.00,
    "status": "filled"
  }
]
```

### Get Trade History
```
GET /api/trades?limit=100

Response:
[
  {
    "id": "trade_123",
    "symbol": "AAPL",
    "side": "buy",
    "qty": 10,
    "price": 180.00,
    "pnl": 55.00,
    "time": "2024-01-15T10:30:00"
  }
]
```

### Place Order
```
POST /api/place-order
Content-Type: application/json

{
  "symbol": "AAPL",
  "side": "buy",
  "qty": 10,
  "type": "market",
  "limit_price": null  // Required for limit orders
}

Response:
{
  "success": true,
  "id": "order_123",
  "symbol": "AAPL",
  "side": "buy",
  "qty": 10,
  "status": "accepted"
}
```

### Get Account Info
```
GET /api/account

Response:
{
  "cash": 100000.00,
  "portfolio_value": 105000.00,
  "buying_power": 200000.00,
  "equity": 105000.00
}
```

### Start Trading
```
POST /api/start

Response:
{
  "success": true,
  "message": "Trading started"
}
```

### Stop Trading
```
POST /api/stop

Response:
{
  "success": true,
  "message": "Trading stopped"
}
```

### Real-time Stream (SSE)
```
GET /api/stream

Response: Server-Sent Events stream
data: {"stats": {...}, "prices": {...}, "positions": [...]}
```

---

## Alert APIs

### Get All Alerts
```
GET /api/alerts

Response:
{
  "success": true,
  "alerts": [
    {
      "id": "alert_123",
      "name": "AAPL Price Alert",
      "symbol": "AAPL",
      "condition": "above",
      "target_price": 190.00,
      "enabled": true,
      "triggered": false
    }
  ]
}
```

### Create Alert
```
POST /api/alerts
Content-Type: application/json

{
  "name": "AAPL Price Alert",
  "symbol": "AAPL",
  "condition": "above",  // above, below, crosses_above, crosses_below, percent_change
  "target_price": 190.00,
  "channels": ["discord", "email"],
  "webhook_url": "https://...",
  "message": "AAPL hit target!"
}

Response:
{
  "success": true,
  "id": "alert_123"
}
```

### Delete Alert
```
DELETE /api/alerts/<alert_id>

Response:
{
  "success": true,
  "message": "Alert deleted"
}
```

### Enable/Disable Alert
```
POST /api/alerts/<alert_id>/enable
POST /api/alerts/<alert_id>/disable

Response:
{
  "success": true,
  "enabled": true
}
```

### Get Notifications
```
GET /api/notifications?limit=50

Response:
{
  "success": true,
  "notifications": [
    {
      "id": "notif_123",
      "type": "alert_triggered",
      "title": "AAPL Price Alert",
      "message": "AAPL crossed above $190",
      "timestamp": "2024-01-15T10:30:00",
      "read": false
    }
  ],
  "unread_count": 5
}
```

---

## Indicator APIs

### List Available Indicators
```
GET /api/indicators

Response:
{
  "indicators": [
    {"name": "sma", "description": "Simple Moving Average", "parameters": ["period"]},
    {"name": "ema", "description": "Exponential Moving Average", "parameters": ["period"]},
    {"name": "rsi", "description": "Relative Strength Index", "parameters": ["period"]},
    {"name": "macd", "description": "MACD", "parameters": ["fast", "slow", "signal"]},
    {"name": "bollinger", "description": "Bollinger Bands", "parameters": ["period", "std_dev"]},
    // ... 13 total indicators
  ]
}
```

### Calculate Indicator
```
POST /api/indicators/calculate
Content-Type: application/json

{
  "indicator": "rsi",
  "symbol": "AAPL",
  "parameters": {"period": 14}
}

Response:
{
  "success": true,
  "indicator": "rsi",
  "values": [65.5, 62.3, 58.1, ...],
  "timestamps": ["2024-01-15T10:00:00", ...]
}
```

### Get Chart Data with Indicators
```
GET /api/indicators/chart/<symbol>?indicators=rsi,macd,sma_20

Response:
{
  "symbol": "AAPL",
  "prices": [...],
  "indicators": {
    "rsi": [...],
    "macd": {"line": [...], "signal": [...], "histogram": [...]},
    "sma_20": [...]
  }
}
```

---

## Marketplace APIs

### List Strategies
```
GET /api/marketplace/strategies?category=momentum&sort_by=rating&limit=20

Response:
{
  "success": true,
  "strategies": [
    {
      "id": "strat_123",
      "name": "Momentum Master",
      "description": "High-frequency momentum strategy",
      "author": "trader_1",
      "category": "momentum",
      "rating": 4.5,
      "followers": 150,
      "monthly_return": 12.5,
      "sharpe_ratio": 2.1
    }
  ]
}
```

### Create Strategy
```
POST /api/marketplace/strategies
Content-Type: application/json

{
  "name": "My Strategy",
  "description": "Description here",
  "category": "momentum",
  "visibility": "public",
  "settings": {...}
}

Response:
{
  "success": true,
  "id": "strat_123"
}
```

### Get Leaderboard
```
GET /api/leaderboard?sort_by=total_return&timeframe=monthly&limit=50

Response:
{
  "success": true,
  "leaderboard": [
    {
      "rank": 1,
      "user_id": "user_123",
      "username": "TopTrader",
      "total_return": 45.5,
      "win_rate": 72.0,
      "total_trades": 250,
      "sharpe_ratio": 2.8
    }
  ]
}
```

### Setup Copy Trading
```
POST /api/copy-trading/setup
Content-Type: application/json

{
  "leader_id": "user_123",
  "allocation_percent": 10.0,
  "max_trade_size": 1000,
  "stop_loss_percent": 5.0
}

Response:
{
  "success": true,
  "settings_id": "copy_123"
}
```

---

## AI Assistant APIs

### Chat with AI
```
POST /api/ai/chat
Content-Type: application/json

{
  "message": "Buy 10 shares of AAPL",
  "session_id": "session_123"  // Optional
}

Response:
{
  "success": true,
  "response": {
    "message": "I'll execute a buy order for 10 shares of AAPL...",
    "intent": "TRADE",
    "action_taken": {
      "type": "order",
      "symbol": "AAPL",
      "side": "buy",
      "qty": 10,
      "status": "executed"
    },
    "confidence": 0.95
  }
}
```

### Analyze Symbol
```
GET /api/ai/analyze/<symbol>

Response:
{
  "success": true,
  "analysis": {
    "symbol": "AAPL",
    "current_price": 185.50,
    "technical": {
      "trend": "bullish",
      "rsi": 62.5,
      "macd_signal": "buy",
      "support": 180.00,
      "resistance": 190.00
    },
    "sentiment": "positive",
    "recommendation": "BUY",
    "confidence": 0.78,
    "reasoning": "Strong momentum with RSI not overbought..."
  }
}
```

---

## Blockchain/DeFi APIs

### Get Portfolio Summary
```
GET /api/blockchain/portfolio

Response:
{
  "success": true,
  "portfolio": {
    "total_value_usd": 50000.00,
    "chains": {
      "ethereum": 30000.00,
      "polygon": 15000.00,
      "arbitrum": 5000.00
    },
    "tokens": [...]
  }
}
```

### Get Supported Chains
```
GET /api/blockchain/chains

Response:
{
  "success": true,
  "chains": [
    {"id": "ethereum", "name": "Ethereum", "chain_id": 1, "native_token": "ETH"},
    {"id": "polygon", "name": "Polygon", "chain_id": 137, "native_token": "MATIC"},
    {"id": "arbitrum", "name": "Arbitrum", "chain_id": 42161, "native_token": "ETH"},
    // ... 8 total chains
  ]
}
```

### Get Swap Quote
```
POST /api/blockchain/swap/quote
Content-Type: application/json

{
  "from_token": "ETH",
  "to_token": "USDC",
  "amount": "1.0",
  "chain": "ethereum",
  "slippage": 0.5
}

Response:
{
  "success": true,
  "quotes": [
    {
      "dex": "1inch",
      "to_amount": "3250.00",
      "price_impact": 0.1,
      "gas_estimate": 150000
    },
    {
      "dex": "paraswap",
      "to_amount": "3248.50",
      "price_impact": 0.12,
      "gas_estimate": 145000
    }
  ]
}
```

### Get Yield Opportunities
```
GET /api/blockchain/yield/opportunities?token=USDC&min_apy=5

Response:
{
  "success": true,
  "opportunities": [
    {
      "protocol": "Aave",
      "chain": "ethereum",
      "token": "USDC",
      "apy": 8.5,
      "tvl": 1500000000,
      "risk_level": "low"
    }
  ]
}
```

### Get Gas Prices
```
GET /api/blockchain/gas/<chain>

Response:
{
  "success": true,
  "chain": "ethereum",
  "gas_prices": {
    "slow": 25,
    "standard": 35,
    "fast": 50,
    "instant": 75
  },
  "base_fee": 30,
  "priority_fee": 2
}
```

---

## Analytics APIs

### Monte Carlo Simulation
```
POST /api/analytics/monte-carlo
Content-Type: application/json

{
  "initial_value": 100000,
  "expected_return": 0.12,
  "volatility": 0.20,
  "time_horizon_days": 252,
  "num_simulations": 10000
}

Response:
{
  "success": true,
  "results": {
    "mean_final_value": 112500,
    "median_final_value": 110200,
    "percentiles": {
      "5": 85000,
      "25": 98000,
      "75": 125000,
      "95": 150000
    },
    "probability_of_loss": 0.22,
    "max_drawdown_avg": 0.15
  }
}
```

### Portfolio Optimization
```
POST /api/analytics/optimize
Content-Type: application/json

{
  "assets": ["AAPL", "GOOGL", "MSFT", "BTC"],
  "expected_returns": [0.15, 0.12, 0.14, 0.25],
  "risk_free_rate": 0.05,
  "target": "max_sharpe"  // or "min_variance", "target_return"
}

Response:
{
  "success": true,
  "optimal_weights": {
    "AAPL": 0.30,
    "GOOGL": 0.25,
    "MSFT": 0.35,
    "BTC": 0.10
  },
  "expected_return": 0.156,
  "expected_volatility": 0.18,
  "sharpe_ratio": 2.1
}
```

### Scenario Analysis
```
POST /api/analytics/scenarios
Content-Type: application/json

{
  "portfolio": {"AAPL": 0.3, "BTC": 0.2, "bonds": 0.5},
  "scenarios": ["market_crash", "recession", "crypto_winter"]
}

Response:
{
  "success": true,
  "results": {
    "market_crash": {"impact": -35.5, "recovery_months": 18},
    "recession": {"impact": -22.0, "recovery_months": 24},
    "crypto_winter": {"impact": -18.0, "recovery_months": 12}
  }
}
```

### Risk Metrics
```
POST /api/analytics/risk
Content-Type: application/json

{
  "returns": [0.02, -0.01, 0.03, -0.02, 0.01, ...]
}

Response:
{
  "success": true,
  "risk_metrics": {
    "var_95": 2.5,
    "cvar_95": 3.2,
    "sortino_ratio": 1.8,
    "daily_volatility": 1.2,
    "annualized_volatility": 19.0
  }
}
```

---

## Settings APIs

### Get All Settings
```
GET /api/settings

Response:
{
  "success": true,
  "settings": {
    "general": {
      "timezone": "America/New_York",
      "currency": "USD",
      "dark_mode": true,
      "sound_alerts": false
    },
    "notifications": {
      "notify_trades": true,
      "notify_signals": true,
      "email": "user@example.com"
    },
    "strategy": {
      "strategy_mode": "momentum",
      "timeframe": "1h",
      "signal_threshold": 70
    }
  }
}
```

### Update Settings
```
POST /api/settings/<category>
Content-Type: application/json

{
  "timezone": "America/Los_Angeles",
  "currency": "EUR"
}

Response:
{
  "success": true,
  "settings": {...}
}
```

### Export Data
```
GET /api/settings/export/<type>

Types: trades, positions, all

Response: CSV or JSON file download
```

### Reset Account
```
POST /api/settings/reset-account

Response:
{
  "success": true,
  "message": "Account reset to initial capital",
  "initial_capital": 100000
}
```

---

## Error Handling

All endpoints return consistent error responses:

```json
{
  "success": false,
  "error": "Error message here"
}
```

### Common HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request - Invalid parameters |
| 401 | Unauthorized - Login required |
| 404 | Not Found - Resource doesn't exist |
| 500 | Internal Server Error |

---

## Rate Limiting

Currently no rate limiting is implemented. For production deployments, consider adding rate limiting via nginx or a middleware.

Recommended limits:
- General endpoints: 100 requests/minute
- Trading endpoints: 10 requests/second
- SSE streams: 1 connection per user

---

## WebSocket/SSE Events

The `/api/stream` endpoint sends real-time updates every second:

```javascript
const eventSource = new EventSource('/api/stream');

eventSource.onmessage = function(event) {
  const data = JSON.parse(event.data);
  // data.stats - Trading statistics
  // data.prices - Current prices
  // data.positions - Open positions
  // data.is_running - Trading status
};
```

---

*API Version: 2.0*
*Last Updated: January 2026*
