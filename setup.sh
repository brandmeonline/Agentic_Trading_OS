#!/bin/bash
#
# Agentic Trading OS - Setup Script
# Installs dependencies and configures the environment
#

set -e

echo "======================================"
echo "  Agentic Trading OS - Setup"
echo "======================================"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Python version
echo -e "\n${YELLOW}Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo -e "${RED}Error: Python 3.8+ required (found $PYTHON_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python $PYTHON_VERSION${NC}"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo -e "\n${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi

# Activate virtual environment
echo -e "\n${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"

# Upgrade pip
echo -e "\n${YELLOW}Upgrading pip...${NC}"
pip install --upgrade pip

# Install core dependencies
echo -e "\n${YELLOW}Installing core dependencies...${NC}"
pip install numpy>=1.21.0
pip install scipy>=1.7.0

# Install optional dependencies with fallbacks
echo -e "\n${YELLOW}Installing optional dependencies...${NC}"

# WebSocket
pip install websocket-client || echo "Warning: websocket-client failed to install"

# Cryptography for secure credentials
pip install cryptography || echo "Warning: cryptography failed to install"

# HTTP library
pip install requests || echo "Warning: requests failed to install"

# Async support
pip install aiohttp || echo "Warning: aiohttp failed to install"

# Data handling
pip install pandas || echo "Warning: pandas failed to install"

# Check if requirements.txt exists
if [ -f "Alpha IO/requirements.txt" ]; then
    echo -e "\n${YELLOW}Installing from requirements.txt...${NC}"
    pip install -r "Alpha IO/requirements.txt" || echo "Warning: Some requirements failed"
fi

# Create directories
echo -e "\n${YELLOW}Creating directories...${NC}"
mkdir -p logs
mkdir -p data
mkdir -p config
mkdir -p .credentials

# Create default config
if [ ! -f "config/development.json" ]; then
    echo -e "\n${YELLOW}Creating default configuration...${NC}"
    cat > config/development.json << 'EOF'
{
    "trading": {
        "mode": "paper",
        "initial_capital": 100000.0,
        "base_currency": "USDT",
        "max_position_size": 0.20,
        "risk_per_trade": 0.02,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15
    },
    "exchange": {
        "name": "binance",
        "testnet": true,
        "rate_limit_per_second": 10.0
    },
    "database": {
        "type": "sqlite",
        "database": "data/trading.db"
    },
    "logging": {
        "level": "INFO",
        "log_dir": "logs",
        "enable_console": true,
        "enable_file": true
    },
    "api": {
        "host": "0.0.0.0",
        "port": 8080,
        "enable_auth": true
    }
}
EOF
    echo -e "${GREEN}✓ Default config created${NC}"
fi

# Create .env template
if [ ! -f ".env" ]; then
    echo -e "\n${YELLOW}Creating .env template...${NC}"
    cat > .env << 'EOF'
# Agentic Trading OS Environment Variables
# Copy this to .env and fill in your values

# Exchange API Keys (testnet recommended for testing)
# Get Binance testnet keys: https://testnet.binance.vision/
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Get Coinbase sandbox keys: https://public.sandbox.exchange.coinbase.com/
COINBASE_API_KEY=
COINBASE_API_SECRET=
COINBASE_PASSPHRASE=

# Credentials encryption password (auto-generated if not set)
CREDENTIALS_PASSWORD=

# Database (optional - uses SQLite by default)
DB_USERNAME=
DB_PASSWORD=

# API JWT Secret (auto-generated if not set)
JWT_SECRET=

# Logging level
LOG_LEVEL=INFO
EOF
    echo -e "${GREEN}✓ .env template created${NC}"
fi

# Run tests
echo -e "\n${YELLOW}Running module tests...${NC}"
cd "Alpha IO"

# Test imports
python3 -c "
import sys
sys.path.insert(0, '.')
errors = []

modules = [
    'core.config',
    'core.models',
    'core.logger',
    'core.risk',
    'core.execution',
    'core.strategy',
    'core.market_data',
    'core.backtest_engine',
    'core.portfolio',
    'core.analytics',
    'core.deep_learning',
    'core.feature_engine',
    'core.nlp_engine',
    'core.realtime_data',
    'core.smart_router',
    'core.monitoring',
    'core.advanced_backtest',
    'core.stress_testing',
    'core.unified_system',
    'core.exchange_connectors',
    'core.rest_api',
    'core.database',
    'core.advanced_rl',
    'core.config_manager',
    'core.live_data',
]

for mod in modules:
    try:
        __import__(mod)
        print(f'  ✓ {mod}')
    except Exception as e:
        print(f'  ✗ {mod}: {e}')
        errors.append(mod)

if errors:
    print(f'\n{len(errors)} module(s) failed to import')
else:
    print(f'\n✓ All {len(modules)} modules imported successfully')
"

cd ..

echo -e "\n${GREEN}======================================"
echo "  Setup Complete!"
echo "======================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Activate venv:  source venv/bin/activate"
echo "  2. Edit .env with your API keys"
echo "  3. Run the system:  python 'Alpha IO/trading_system.py' --mode paper"
echo ""
echo "Quick start commands:"
echo "  python 'Alpha IO/trading_system.py' --mode backtest   # Run backtest"
echo "  python 'Alpha IO/trading_system.py' --mode paper      # Paper trading"
echo "  python 'Alpha IO/trading_system.py' --mode all-tests  # Run all tests"
echo ""
