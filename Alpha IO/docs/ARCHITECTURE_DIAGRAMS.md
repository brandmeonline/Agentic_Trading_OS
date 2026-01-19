# Agentic Trading OS - Architecture Diagrams

This document contains comprehensive Mermaid diagrams illustrating the platform's architecture, data flows, integrations, and site structure.

---

## 1. SITE MAP

```mermaid
flowchart TB
    subgraph Public
        LOGIN["/login<br/>Authentication"]
    end

    subgraph Main["Main Application"]
        DASH["/dashboard<br/>Portfolio Overview"]
        TRADE["/trading<br/>Order Management"]
        ANALYTICS["/analytics<br/>Performance Metrics"]
        ALERTS["/alerts<br/>Price Alerts"]
    end

    subgraph Social["Social Trading"]
        MARKET["/marketplace<br/>Strategy Discovery"]
        LEADER["/leaderboard<br/>Trader Rankings"]
    end

    subgraph Advanced["Advanced Features"]
        AI["/ai-assistant<br/>NLP Trading"]
        DEFI["/defi<br/>DeFi Dashboard"]
    end

    subgraph Admin["Administration"]
        ADMIN["/admin<br/>System Control"]
        SETTINGS["/settings<br/>User Preferences"]
    end

    LOGIN --> DASH
    DASH --> TRADE
    DASH --> ANALYTICS
    DASH --> ALERTS
    DASH --> MARKET
    DASH --> LEADER
    DASH --> AI
    DASH --> DEFI
    DASH --> ADMIN
    DASH --> SETTINGS

    MARKET <--> LEADER
    TRADE <--> ANALYTICS
```

---

## 2. HIGH-LEVEL SYSTEM ARCHITECTURE

```mermaid
flowchart TB
    subgraph Client["Client Layer"]
        BROWSER[("Web Browser")]
        MOBILE[("Mobile App<br/>(Future)")]
    end

    subgraph Presentation["Presentation Layer"]
        FLASK["Flask Web Server<br/>Port 5000"]
        TEMPLATES["Jinja2 Templates<br/>12 HTML Pages"]
        STATIC["Static Assets<br/>CSS/JS/Images"]
    end

    subgraph API["API Layer"]
        REST["REST API<br/>78 Endpoints"]
        SSE["SSE Stream<br/>/api/stream"]
        AUTH["Auth Middleware<br/>@login_required"]
    end

    subgraph Core["Core Business Logic"]
        TRADING["Trading Engine<br/>trading.py"]
        SIGNALS["Signal Generator<br/>signals.py"]
        RISK["Risk Manager<br/>risk.py"]
        ALERTS_MOD["Alert System<br/>alerts.py"]
        INDICATORS["Indicators<br/>indicators.py"]
        MARKETPLACE_MOD["Marketplace<br/>marketplace.py"]
        AI_MOD["AI Assistant<br/>ai_assistant.py"]
        BLOCKCHAIN["Blockchain<br/>blockchain.py"]
        ANALYTICS_MOD["Analytics<br/>advanced_analytics.py"]
    end

    subgraph Data["Data Layer"]
        STATE["Trading State<br/>In-Memory"]
        CONFIG["Config Files<br/>JSON"]
        CACHE["Price Cache<br/>In-Memory"]
    end

    subgraph External["External Services"]
        ALPACA["Alpaca API<br/>Stocks/Crypto"]
        DISCORD["Discord<br/>Webhooks"]
        SLACK["Slack<br/>Webhooks"]
        TELEGRAM["Telegram<br/>Bot API"]
        DEX["DEX Aggregators<br/>1inch/0x/Paraswap"]
        CHAINS["Blockchains<br/>ETH/Polygon/etc"]
    end

    BROWSER --> FLASK
    MOBILE -.-> FLASK
    FLASK --> TEMPLATES
    FLASK --> STATIC
    FLASK --> REST
    FLASK --> SSE
    REST --> AUTH
    AUTH --> TRADING
    AUTH --> SIGNALS
    AUTH --> RISK
    AUTH --> ALERTS_MOD
    AUTH --> INDICATORS
    AUTH --> MARKETPLACE_MOD
    AUTH --> AI_MOD
    AUTH --> BLOCKCHAIN
    AUTH --> ANALYTICS_MOD

    TRADING --> STATE
    TRADING --> CONFIG
    SIGNALS --> CACHE

    TRADING --> ALPACA
    ALERTS_MOD --> DISCORD
    ALERTS_MOD --> SLACK
    ALERTS_MOD --> TELEGRAM
    BLOCKCHAIN --> DEX
    BLOCKCHAIN --> CHAINS
```

---

## 3. DATA FLOW DIAGRAMS

### 3.1 Order Execution Flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend<br/>(trading.html)
    participant API as Flask API
    participant TE as Trading Engine
    participant RM as Risk Manager
    participant AL as Alpaca API
    participant N as Notifications

    U->>FE: Submit Order Form
    FE->>FE: Validate Input
    FE->>API: POST /api/place-order
    API->>API: Authenticate Session
    API->>RM: Check Risk Limits

    alt Risk Check Passed
        RM-->>API: Approved
        API->>TE: Execute Order
        TE->>AL: Submit to Alpaca
        AL-->>TE: Order Confirmation
        TE->>TE: Update Positions
        TE->>N: Trigger Alert
        N-->>U: Send Notification
        TE-->>API: Success Response
        API-->>FE: {success: true, id: "..."}
        FE-->>U: Show Success Toast
    else Risk Check Failed
        RM-->>API: Rejected (reason)
        API-->>FE: {success: false, error: "..."}
        FE-->>U: Show Error Toast
    end
```

### 3.2 Real-Time Data Stream Flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend<br/>(dashboard.html)
    participant SSE as SSE Endpoint<br/>(/api/stream)
    participant TS as Trading State
    participant AL as Alpaca API

    U->>FE: Load Dashboard
    FE->>FE: Call startRealTimeUpdates()
    FE->>SSE: EventSource Connect

    loop Every 1 Second
        SSE->>TS: Get Current State
        TS->>AL: Fetch Prices (if needed)
        AL-->>TS: Price Data
        TS-->>SSE: Stats + Prices
        SSE-->>FE: data: {stats, prices, positions}
        FE->>FE: Update Charts
        FE->>FE: Update Stats Cards
        FE->>FE: Update Position List
    end

    Note over FE,SSE: On disconnect, retry with exponential backoff
```

### 3.3 AI Assistant Flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Frontend<br/>(ai_assistant.html)
    participant API as Flask API
    participant NLP as NLP Processor
    participant MA as Market Analyzer
    participant TE as Trading Engine

    U->>FE: "Buy 10 shares of AAPL"
    FE->>API: POST /api/ai/chat
    API->>NLP: Parse Message
    NLP->>NLP: Extract Intent (TRADE)
    NLP->>NLP: Extract Entities<br/>(symbol=AAPL, qty=10, action=buy)
    NLP-->>API: ParsedCommand

    API->>MA: Get Market Context
    MA->>MA: Fetch Price History
    MA->>MA: Calculate Indicators
    MA->>MA: Generate Recommendation
    MA-->>API: Analysis Result

    API->>TE: Execute Trade
    TE-->>API: Trade Result

    API-->>FE: AIResponse {<br/>  message: "Executed...",<br/>  action_taken: {...},<br/>  analysis: {...}<br/>}
    FE-->>U: Display Response
```

### 3.4 Alert System Flow

```mermaid
flowchart LR
    subgraph Trigger["Alert Triggers"]
        PRICE["Price Alert<br/>(above/below)"]
        PCT["Percent Change<br/>(+/- %)"]
        VOL["Volume Spike"]
        TECH["Technical Signal<br/>(RSI/MACD)"]
    end

    subgraph Engine["Alert Engine"]
        CHECK["Alert Checker<br/>(runs every tick)"]
        EVAL["Condition Evaluator"]
        QUEUE["Notification Queue"]
    end

    subgraph Delivery["Delivery Channels"]
        WEB["Web UI<br/>Toast + Badge"]
        WEBHOOK["Custom Webhook"]
        DISC["Discord Bot"]
        SLCK["Slack Bot"]
        TELE["Telegram Bot"]
        EMAIL["Email (SMTP)"]
    end

    PRICE --> CHECK
    PCT --> CHECK
    VOL --> CHECK
    TECH --> CHECK

    CHECK --> EVAL
    EVAL -->|Triggered| QUEUE

    QUEUE --> WEB
    QUEUE --> WEBHOOK
    QUEUE --> DISC
    QUEUE --> SLCK
    QUEUE --> TELE
    QUEUE --> EMAIL
```

---

## 4. API ENDPOINT MAP

```mermaid
flowchart TB
    subgraph Core["Core Trading APIs"]
        direction LR
        A1["/api/stats"]
        A2["/api/prices"]
        A3["/api/positions"]
        A4["/api/orders"]
        A5["/api/trades"]
        A6["/api/place-order"]
        A7["/api/account"]
        A8["/api/stream"]
    end

    subgraph Alerts["Alert APIs"]
        direction LR
        B1["GET /api/alerts"]
        B2["POST /api/alerts"]
        B3["DELETE /api/alerts/:id"]
        B4["POST /api/alerts/:id/enable"]
        B5["GET /api/notifications"]
    end

    subgraph Indicators["Indicator APIs"]
        direction LR
        C1["GET /api/indicators"]
        C2["POST /api/indicators/calculate"]
        C3["GET /api/indicators/chart/:symbol"]
    end

    subgraph Market["Marketplace APIs"]
        direction LR
        D1["GET /api/marketplace/strategies"]
        D2["POST /api/marketplace/strategies"]
        D3["GET /api/leaderboard"]
        D4["POST /api/copy-trading/setup"]
        D5["GET /api/signals"]
    end

    subgraph AIBlock["AI Assistant APIs"]
        direction LR
        E1["POST /api/ai/chat"]
        E2["GET /api/ai/analyze/:symbol"]
    end

    subgraph Blockchain["Blockchain/DeFi APIs"]
        direction LR
        F1["GET /api/blockchain/portfolio"]
        F2["GET /api/blockchain/chains"]
        F3["GET /api/blockchain/defi"]
        F4["POST /api/blockchain/swap/quote"]
        F5["GET /api/blockchain/yield/opportunities"]
        F6["GET /api/blockchain/gas/:chain"]
    end

    subgraph Analytics["Analytics APIs"]
        direction LR
        G1["POST /api/analytics/monte-carlo"]
        G2["POST /api/analytics/optimize"]
        G3["POST /api/analytics/scenarios"]
        G4["POST /api/analytics/risk"]
    end

    subgraph Settings["Settings APIs"]
        direction LR
        H1["GET /api/settings"]
        H2["POST /api/settings/:category"]
        H3["POST /api/settings/password"]
        H4["GET /api/settings/export/:type"]
        H5["POST /api/settings/reset-account"]
    end
```

---

## 5. MODULE DEPENDENCY GRAPH

```mermaid
flowchart TB
    subgraph Web["Web Layer"]
        APP["web/app.py<br/>Flask Application"]
    end

    subgraph Core["Core Modules"]
        TRADING["core/trading.py"]
        SIGNALS["core/signals.py"]
        RISK["core/risk.py"]
        ALERTS["core/alerts.py"]
        INDICATORS["core/indicators.py"]
        MARKETPLACE["core/marketplace.py"]
        AI["core/ai_assistant.py"]
        BLOCKCHAIN["core/blockchain.py"]
        ANALYTICS["core/advanced_analytics.py"]
    end

    subgraph External["External Dependencies"]
        ALPACA_SDK["alpaca-trade-api"]
        FLASK_LIB["flask"]
        NUMPY["numpy"]
        WEB3["web3.py"]
    end

    APP --> TRADING
    APP --> SIGNALS
    APP --> RISK
    APP --> ALERTS
    APP --> INDICATORS
    APP --> MARKETPLACE
    APP --> AI
    APP --> BLOCKCHAIN
    APP --> ANALYTICS

    TRADING --> SIGNALS
    TRADING --> RISK
    SIGNALS --> INDICATORS
    AI --> INDICATORS
    ANALYTICS --> NUMPY

    TRADING --> ALPACA_SDK
    BLOCKCHAIN --> WEB3
    APP --> FLASK_LIB
```

---

## 6. INTEGRATION ARCHITECTURE

```mermaid
flowchart TB
    subgraph Platform["Agentic Trading OS"]
        CORE["Core Platform"]
    end

    subgraph Brokers["Broker Integrations"]
        ALP["Alpaca<br/>US Stocks + Crypto"]
        BIN["Binance<br/>(Planned)"]
        CB["Coinbase<br/>(Planned)"]
        IBKR["Interactive Brokers<br/>(Planned)"]
    end

    subgraph DEXs["DEX Aggregators"]
        INCH["1inch"]
        PARA["Paraswap"]
        ZRX["0x Protocol"]
        KYBER["Kyber Network"]
    end

    subgraph Chains["Blockchain Networks"]
        ETH["Ethereum"]
        POLY["Polygon"]
        ARB["Arbitrum"]
        OPT["Optimism"]
        BASE["Base"]
        AVAX["Avalanche"]
        BSC["BNB Chain"]
        SOL["Solana"]
    end

    subgraph Notifications["Notification Services"]
        DISC["Discord"]
        SLCK["Slack"]
        TELE["Telegram"]
        SMTP["Email (SMTP)"]
        HOOK["Custom Webhooks"]
    end

    subgraph Data["Market Data"]
        ALPDATA["Alpaca Data"]
        CGECKO["CoinGecko<br/>(Planned)"]
        CMARKETCAP["CoinMarketCap<br/>(Planned)"]
    end

    CORE <-->|REST API| ALP
    CORE -.->|Future| BIN
    CORE -.->|Future| CB
    CORE -.->|Future| IBKR

    CORE <-->|HTTP| INCH
    CORE <-->|HTTP| PARA
    CORE <-->|HTTP| ZRX
    CORE <-->|HTTP| KYBER

    CORE <-->|RPC/WebSocket| ETH
    CORE <-->|RPC| POLY
    CORE <-->|RPC| ARB
    CORE <-->|RPC| OPT
    CORE <-->|RPC| BASE
    CORE <-->|RPC| AVAX
    CORE <-->|RPC| BSC
    CORE <-->|RPC| SOL

    CORE -->|Webhook| DISC
    CORE -->|Webhook| SLCK
    CORE -->|Bot API| TELE
    CORE -->|SMTP| SMTP
    CORE -->|HTTP POST| HOOK

    CORE <-->|WebSocket| ALPDATA
    CORE -.->|Future| CGECKO
    CORE -.->|Future| CMARKETCAP
```

---

## 7. USER JOURNEY FLOWS

### 7.1 New User Onboarding

```mermaid
flowchart LR
    A[Visit Site] --> B[Login Page]
    B --> C{Has Account?}
    C -->|No| D[Create Account]
    D --> E[Enter Alpaca Keys]
    C -->|Yes| F[Enter Credentials]
    E --> G[Dashboard]
    F --> G
    G --> H[View Portfolio]
    H --> I{First Trade?}
    I -->|Yes| J[Trading Tutorial]
    J --> K[Place First Order]
    I -->|No| L[Continue Trading]
    K --> L
```

### 7.2 Copy Trading Journey

```mermaid
flowchart TB
    A[Browse Leaderboard] --> B[View Top Traders]
    B --> C[Select Trader]
    C --> D[View Performance]
    D --> E{Copy Trader?}
    E -->|Yes| F[Set Allocation %]
    F --> G[Set Risk Limits]
    G --> H[Confirm Copy]
    H --> I[Auto-Execute Trades]
    I --> J[Monitor Performance]
    J --> K{Satisfied?}
    K -->|No| L[Adjust Settings]
    L --> I
    K -->|Yes| M[Continue]
    E -->|No| B
```

### 7.3 AI Assistant Interaction

```mermaid
flowchart TB
    A[Open AI Assistant] --> B[Type Command]
    B --> C{Command Type?}

    C -->|Trade| D["Buy 10 AAPL"]
    D --> E[Parse & Validate]
    E --> F[Show Confirmation]
    F --> G[Execute Trade]
    G --> H[Show Result]

    C -->|Analysis| I["Analyze BTC"]
    I --> J[Fetch Data]
    J --> K[Run Indicators]
    K --> L[Generate Insights]
    L --> M[Show Analysis]

    C -->|Question| N["What's my P&L?"]
    N --> O[Query State]
    O --> P[Format Response]
    P --> Q[Show Answer]

    H --> R[Continue Chat]
    M --> R
    Q --> R
```

---

## 8. DEPLOYMENT ARCHITECTURE

```mermaid
flowchart TB
    subgraph Development["Development Environment"]
        DEV["Local Machine<br/>Flask Debug Mode"]
        DEVDB["Local JSON Files"]
    end

    subgraph Production["Production Environment (Recommended)"]
        subgraph Container["Docker Container"]
            PROD["Gunicorn + Flask<br/>4 Workers"]
            PRODDB["PostgreSQL<br/>(Recommended)"]
        end

        NGINX["Nginx<br/>Reverse Proxy"]
        SSL["SSL/TLS<br/>Let's Encrypt"]
    end

    subgraph Monitoring["Monitoring Stack"]
        PROM["Prometheus<br/>Metrics"]
        GRAF["Grafana<br/>Dashboards"]
        LOG["Log Aggregation"]
    end

    USER["Users"] --> SSL
    SSL --> NGINX
    NGINX --> PROD
    PROD --> PRODDB

    PROD --> PROM
    PROM --> GRAF
    PROD --> LOG

    DEV --> DEVDB
```

---

## 9. SECURITY ARCHITECTURE

```mermaid
flowchart TB
    subgraph External["External Boundary"]
        USER["User Browser"]
        ATTACKER["Potential Attacker"]
    end

    subgraph Edge["Edge Security"]
        WAF["Web Application Firewall<br/>(Recommended)"]
        RATE["Rate Limiter"]
        SSL["TLS 1.3"]
    end

    subgraph App["Application Security"]
        AUTH["Session Authentication"]
        CSRF["CSRF Protection"]
        VALID["Input Validation"]
        SANITIZE["Output Sanitization"]
    end

    subgraph Data["Data Security"]
        ENCRYPT["Encryption at Rest"]
        SECRETS["Secrets Management<br/>(API Keys)"]
        AUDIT["Audit Logging"]
    end

    USER --> SSL
    ATTACKER -.->|Blocked| WAF
    SSL --> WAF
    WAF --> RATE
    RATE --> AUTH
    AUTH --> CSRF
    CSRF --> VALID
    VALID --> SANITIZE
    SANITIZE --> ENCRYPT
    ENCRYPT --> SECRETS

    AUTH --> AUDIT
    VALID --> AUDIT
```

---

## 10. STATE MANAGEMENT

```mermaid
stateDiagram-v2
    [*] --> Stopped: System Start

    Stopped --> Connecting: Start Trading
    Connecting --> Running: Connection Success
    Connecting --> Error: Connection Failed

    Running --> Paused: Pause Trading
    Paused --> Running: Resume Trading

    Running --> Error: Critical Error
    Error --> Connecting: Retry
    Error --> Stopped: Give Up

    Running --> Stopped: Stop Trading
    Paused --> Stopped: Stop Trading

    state Running {
        [*] --> Idle
        Idle --> Analyzing: New Signal
        Analyzing --> Idle: No Action
        Analyzing --> Trading: Execute Order
        Trading --> Idle: Order Complete
        Trading --> Idle: Order Failed
    }
```

---

## Diagram Legend

| Symbol | Meaning |
|--------|---------|
| Solid Arrow (→) | Active Integration |
| Dashed Arrow (-.→) | Planned/Future |
| Rectangle | Component/Module |
| Cylinder | Database/Storage |
| Diamond | Decision Point |
| Rounded Rectangle | External Service |

---

*Generated for Agentic Trading OS v2.0*
*Last Updated: January 2026*
