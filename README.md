# Agentic Trading OS

<div align="center">

![Version](https://img.shields.io/badge/version-2.0-blue)
![Python](https://img.shields.io/badge/python-3.9+-green)
![License](https://img.shields.io/badge/license-MIT-purple)
![Status](https://img.shields.io/badge/status-production%20ready-brightgreen)

**A self-hosted, AI-powered algorithmic trading platform for stocks and crypto**

[Features](#features) | [Quick Start](#quick-start) | [Documentation](#documentation) | [Architecture](#architecture)

</div>

---

## Overview

Agentic Trading OS is a comprehensive, modular trading intelligence system that combines:

- **Traditional Algorithmic Trading** - Technical indicators, signal generation, risk management
- **AI/ML Integration** - Natural language trading commands, market analysis, predictions
- **DeFi & Blockchain** - Multi-chain support, DEX aggregation, yield optimization
- **Social Trading** - Strategy marketplace, copy trading, leaderboards

Built for traders who want full control over their trading infrastructure without relying on third-party platforms.

## Features

### Core Trading
- Real-time order execution via Alpaca (stocks + crypto)
- 13 technical indicators (RSI, MACD, Bollinger Bands, etc.)
- Automated signal generation and strategy execution
- Comprehensive risk management with position sizing
- Paper trading mode for safe testing

### AI Assistant
- Natural language trading commands ("Buy 10 AAPL", "Analyze BTC")
- Market sentiment analysis and predictions
- Intent classification for 9 command types
- Context-aware responses with trade execution

### Analytics & Visualization
- Real-time portfolio dashboard with SSE streaming
- Equity curves, drawdown analysis, P&L distribution
- Monte Carlo simulations for portfolio projections
- Modern Portfolio Theory optimization
- Scenario stress testing (market crash, recession, etc.)

### Social Trading
- Strategy marketplace for sharing and discovery
- Copy trading with configurable allocation
- Trader leaderboards and rankings
- Signal marketplace

### DeFi Integration
- Multi-chain support (Ethereum, Polygon, Arbitrum, etc.)
- DEX aggregation (1inch, Paraswap, 0x, Kyber)
- Yield farming optimization
- Cross-chain bridge aggregation
- Real-time gas tracking

### Alerts & Notifications
- Price alerts with multiple conditions
- Multi-channel delivery (Discord, Slack, Telegram, Email)
- Custom webhook integration
- Real-time notification feed

## Quick Start

### Prerequisites
- Python 3.9+
- Alpaca account (free paper trading)

### Installation

```bash
# Clone the repository
git clone https://github.com/brandmeonline/Agentic_Trading_OS.git
cd Agentic_Trading_OS

# Install dependencies
cd "Alpha IO"
pip install -r requirements.txt

# Configure API keys (optional - can be done in web UI)
cp config/alpaca_credentials.example.json config/alpaca_credentials.json
# Edit with your Alpaca API keys
```

### Running the Platform

```bash
# Start the web dashboard (recommended)
python run_web.py

# Or run the trading system directly
python run_trading.py
```

Access the dashboard at `http://localhost:5000`

**Default Login:** admin / admin

## Project Structure

```
Agentic_Trading_OS/
└── Alpha IO/
    ├── core/                    # Core trading modules
    │   ├── trading.py          # Trading engine
    │   ├── signals.py          # Signal generation
    │   ├── risk.py             # Risk management
    │   ├── alerts.py           # Alert system
    │   ├── indicators.py       # Technical indicators
    │   ├── marketplace.py      # Strategy marketplace
    │   ├── ai_assistant.py     # AI trading assistant
    │   ├── blockchain.py       # DeFi integration
    │   └── advanced_analytics.py  # Monte Carlo, optimization
    │
    ├── web/                     # Web application
    │   ├── app.py              # Flask API (78 endpoints)
    │   ├── templates/          # 12 HTML templates
    │   └── static/             # CSS, JS, images
    │
    ├── docs/                    # Documentation
    │   ├── ARCHITECTURE_DIAGRAMS.md  # Mermaid diagrams
    │   ├── READINESS_ASSESSMENT.md   # Production readiness
    │   └── COMPETITIVE_ANALYSIS.md   # Market comparison
    │
    ├── config/                  # Configuration files
    ├── data/                    # Data storage
    └── tests/                   # Test suite
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture Diagrams](Alpha%20IO/docs/ARCHITECTURE_DIAGRAMS.md) | System architecture, data flows, integrations |
| [Readiness Assessment](Alpha%20IO/docs/READINESS_ASSESSMENT.md) | Production readiness checklist |
| [Competitive Analysis](Alpha%20IO/docs/COMPETITIVE_ANALYSIS.md) | Comparison with QuantConnect, 3Commas, etc. |
| [Installation Guide](Alpha%20IO/docs/INSTALL.md) | Detailed setup instructions |
| [User Guide](Alpha%20IO/docs/USER_GUIDE.md) | How to use the platform |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Browser                             │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Flask Web Server                           │
│              (78 REST + SSE Endpoints)                       │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    Core Modules                              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐   │
│  │ Trading │ │ Signals │ │  Risk   │ │   Indicators    │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────────┘   │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐   │
│  │ Alerts  │ │Marketplace│ │   AI   │ │   Blockchain   │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────────┘   │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                  External Services                           │
│     Alpaca API  │  DEX Aggregators  │  Notification Hooks   │
└─────────────────────────────────────────────────────────────┘
```

## API Endpoints

The platform exposes 78 REST API endpoints organized by category:

| Category | Endpoints | Description |
|----------|-----------|-------------|
| Trading | 8 | Orders, positions, trades, account |
| Analytics | 5 | Monte Carlo, optimization, risk |
| Alerts | 8 | Create, manage, notifications |
| Marketplace | 12 | Strategies, leaderboard, copy trading |
| AI | 2 | Chat, analysis |
| Blockchain | 6 | Portfolio, chains, swaps, yields |
| Settings | 7 | Preferences, export, reset |

See [API Documentation](Alpha%20IO/docs/README.md) for full details.

## Configuration

### Environment Variables

```bash
# Alpaca API (required for live trading)
ALPACA_API_KEY=your_api_key
ALPACA_API_SECRET=your_api_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # Use paper for testing

# Optional
FLASK_SECRET_KEY=your_secret_key
ADMIN_PASSWORD=your_admin_password
```

### Web UI Configuration

All settings can be configured through the web interface at `/settings`:
- Timezone and currency preferences
- Notification channels
- Strategy parameters
- API key management

## Competitive Comparison

| Feature | Agentic OS | QuantConnect | 3Commas | TradingView |
|---------|------------|--------------|---------|-------------|
| Self-Hosted | Yes | No | No | No |
| AI Assistant | Yes | No | No | No |
| DeFi/Blockchain | Yes | No | Yes | No |
| Copy Trading | Yes | Yes | Yes | Yes |
| Free | Yes | Freemium | Paid | Freemium |
| Open Source | Yes | Partial | No | No |

**Overall Score: 398/450** (Industry-leading)

## Contributing

Contributions are welcome! Please read our contributing guidelines before submitting PRs.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/brandmeonline/Agentic_Trading_OS/issues)
- **Discussions**: [GitHub Discussions](https://github.com/brandmeonline/Agentic_Trading_OS/discussions)

---

<div align="center">
<strong>Built for traders, by traders</strong>
<br>
<sub>Agentic Trading OS - Your AI-Powered Trading Command Center</sub>
</div>
