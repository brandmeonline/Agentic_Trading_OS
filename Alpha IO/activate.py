#!/usr/bin/env python3
"""
Agentic Trading OS - Activation & Control Center

This script provides:
- System activation and credential setup
- Live system control
- Status monitoring
- Interactive management
"""

import os
import sys
import json
import time
import getpass
import argparse
from pathlib import Path
from datetime import datetime

# Add paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "core"))

# =============================================================================
# Banner
# =============================================================================

BANNER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║     █████╗  ██████╗ ███████╗███╗   ██╗████████╗██╗ ██████╗                   ║
║    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██║██╔════╝                   ║
║    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ██║██║                        ║
║    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ██║██║                        ║
║    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ██║╚██████╗                   ║
║    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝ ╚═════╝                   ║
║                                                                              ║
║                    ████████╗██████╗  █████╗ ██████╗ ██╗███╗   ██╗ ██████╗    ║
║                    ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██║████╗  ██║██╔════╝    ║
║                       ██║   ██████╔╝███████║██║  ██║██║██╔██╗ ██║██║  ███╗   ║
║                       ██║   ██╔══██╗██╔══██║██║  ██║██║██║╚██╗██║██║   ██║   ║
║                       ██║   ██║  ██║██║  ██║██████╔╝██║██║ ╚████║╚██████╔╝   ║
║                       ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝    ║
║                                                                              ║
║                              ██████╗ ███████╗                                ║
║                             ██╔═══██╗██╔════╝                                ║
║                             ██║   ██║███████╗                                ║
║                             ██║   ██║╚════██║                                ║
║                             ╚██████╔╝███████║                                ║
║                              ╚═════╝ ╚══════╝                                ║
║                                                                              ║
║                    Production-Ready Algorithmic Trading                      ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""


# =============================================================================
# System Check
# =============================================================================

def check_system():
    """Check system components and dependencies."""
    print("\n" + "="*60)
    print("  System Component Check")
    print("="*60)

    components = {
        "config_manager": False,
        "credentials": False,
        "live_data": False,
        "database": False,
        "rest_api": False,
        "exchange_connectors": False,
        "strategies": False,
        "orchestrator": False,
        "advanced_rl": False,
    }

    # Check each component
    try:
        from core.config_manager import ConfigManager
        components["config_manager"] = True
    except ImportError:
        pass

    try:
        from core.credentials import CredentialsManager
        components["credentials"] = True
    except ImportError:
        pass

    try:
        from core.live_data import LiveDataManager
        components["live_data"] = True
    except ImportError:
        pass

    try:
        from core.database import DatabaseManager
        components["database"] = True
    except ImportError:
        pass

    try:
        from core.rest_api import RESTAPIServer
        components["rest_api"] = True
    except ImportError:
        pass

    try:
        from core.exchange_connectors import ExchangeConnector
        components["exchange_connectors"] = True
    except ImportError:
        pass

    try:
        from core.strategy import Strategy
        components["strategies"] = True
    except ImportError:
        pass

    try:
        from core.orchestrator import TradingOrchestrator
        components["orchestrator"] = True
    except ImportError:
        pass

    try:
        from core.advanced_rl import PPOAgent
        components["advanced_rl"] = True
    except ImportError:
        pass

    # Print results
    total = len(components)
    ready = sum(components.values())

    for name, status in components.items():
        icon = "✓" if status else "✗"
        color = "\033[92m" if status else "\033[91m"
        reset = "\033[0m"
        print(f"  {color}{icon}{reset} {name.replace('_', ' ').title()}")

    print(f"\n  Components ready: {ready}/{total}")
    print("="*60)

    return components, ready == total


# =============================================================================
# Credential Setup
# =============================================================================

def setup_credentials_interactive():
    """Interactive credential setup."""
    print("\n" + "="*60)
    print("  Credential Setup")
    print("="*60)

    try:
        from core.credentials import get_credentials_manager, TESTNET_ENDPOINTS
    except ImportError:
        print("  ✗ Credentials module not available")
        return False

    manager = get_credentials_manager()

    print("\n  Available exchanges:")
    print("  1. Binance (testnet)")
    print("  2. Binance (production)")
    print("  3. Coinbase (sandbox)")
    print("  4. Skip credential setup")

    choice = input("\n  Select exchange (1-4): ").strip()

    if choice == "1":
        print("\n  Binance Testnet Setup")
        print("  Get free testnet API keys at: https://testnet.binance.vision/")
        print("  (No KYC required, unlimited test funds)")

        api_key = input("\n  API Key: ").strip()
        api_secret = getpass.getpass("  API Secret: ")

        if api_key and api_secret:
            manager.add_credential(
                name="binance_testnet",
                api_key=api_key,
                api_secret=api_secret,
                exchange="binance",
                environment="testnet"
            )
            print("\n  ✓ Binance testnet credentials saved")
            return True
        else:
            print("\n  ✗ Invalid credentials")
            return False

    elif choice == "2":
        print("\n  Binance Production Setup")
        print("  ⚠ WARNING: This will use REAL funds!")
        confirm = input("  Type 'CONFIRM' to proceed: ")

        if confirm != "CONFIRM":
            print("  Cancelled")
            return False

        api_key = input("\n  API Key: ").strip()
        api_secret = getpass.getpass("  API Secret: ")

        if api_key and api_secret:
            manager.add_credential(
                name="binance_production",
                api_key=api_key,
                api_secret=api_secret,
                exchange="binance",
                environment="production"
            )
            print("\n  ✓ Binance production credentials saved")
            return True

    elif choice == "3":
        print("\n  Coinbase Sandbox Setup")
        print("  Get sandbox API keys at: https://public.sandbox.exchange.coinbase.com/")

        api_key = input("\n  API Key: ").strip()
        api_secret = getpass.getpass("  API Secret: ")
        passphrase = getpass.getpass("  Passphrase: ")

        if api_key and api_secret:
            manager.add_credential(
                name="coinbase_sandbox",
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                exchange="coinbase",
                environment="testnet"
            )
            print("\n  ✓ Coinbase sandbox credentials saved")
            return True

    elif choice == "4":
        print("\n  Skipping credential setup")
        print("  You can still use public market data (no trading)")
        return True

    return False


def setup_credentials_embedded(api_key: str, api_secret: str, exchange: str = "binance",
                              environment: str = "testnet", passphrase: str = None):
    """Setup credentials programmatically."""
    try:
        from core.credentials import get_credentials_manager
        manager = get_credentials_manager()

        name = f"{exchange}_{environment}"
        manager.add_credential(
            name=name,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            exchange=exchange,
            environment=environment
        )
        print(f"  ✓ Credentials saved: {name}")
        return True

    except Exception as e:
        print(f"  ✗ Failed to save credentials: {e}")
        return False


# =============================================================================
# System Activation
# =============================================================================

def activate_system(mode: str = "paper", symbols: list = None, capital: float = 100000.0):
    """Activate the trading system."""
    print("\n" + "="*60)
    print("  System Activation")
    print("="*60)

    try:
        from core.orchestrator import create_orchestrator
    except ImportError as e:
        print(f"  ✗ Failed to import orchestrator: {e}")
        return None

    symbols = symbols or ["BTC/USDT", "ETH/USDT"]

    print(f"\n  Mode: {mode}")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Capital: ${capital:,.2f}")

    orchestrator = create_orchestrator(
        mode=mode,
        symbols=symbols,
        initial_capital=capital
    )

    if orchestrator.initialize():
        print("\n  ✓ System initialized successfully")
        return orchestrator
    else:
        print("\n  ✗ System initialization failed")
        return None


def run_system(orchestrator, duration_minutes: int = None):
    """Run the trading system."""
    if orchestrator is None:
        return

    if not orchestrator.start():
        return

    print("\n  System running...")
    print("  Press Ctrl+C to stop\n")

    try:
        if duration_minutes:
            end_time = time.time() + (duration_minutes * 60)
            while time.time() < end_time and orchestrator._running:
                time.sleep(1)
            orchestrator.stop()
        else:
            orchestrator.run_forever()

    except KeyboardInterrupt:
        orchestrator.stop()


# =============================================================================
# Quick Start Menu
# =============================================================================

def quick_start_menu():
    """Interactive quick start menu."""
    print(BANNER)

    while True:
        print("\n  Quick Start Menu")
        print("  ─────────────────")
        print("  1. Check System Status")
        print("  2. Setup Exchange Credentials")
        print("  3. Start Paper Trading")
        print("  4. Start Live Trading")
        print("  5. Run Backtest")
        print("  6. View Stored Credentials")
        print("  7. Test Live Data Connection")
        print("  8. Exit")

        choice = input("\n  Select option (1-8): ").strip()

        if choice == "1":
            check_system()

        elif choice == "2":
            setup_credentials_interactive()

        elif choice == "3":
            symbols_input = input("\n  Symbols (comma-separated) [BTC/USDT,ETH/USDT]: ").strip()
            symbols = [s.strip() for s in symbols_input.split(",")] if symbols_input else ["BTC/USDT", "ETH/USDT"]

            capital_input = input("  Initial capital [$100000]: ").strip()
            capital = float(capital_input) if capital_input else 100000.0

            orchestrator = activate_system("paper", symbols, capital)
            if orchestrator:
                run_system(orchestrator)

        elif choice == "4":
            print("\n  ⚠ WARNING: Live trading uses REAL funds!")
            confirm = input("  Type 'I UNDERSTAND' to proceed: ")

            if confirm == "I UNDERSTAND":
                orchestrator = activate_system("live")
                if orchestrator:
                    run_system(orchestrator)
            else:
                print("  Cancelled")

        elif choice == "5":
            print("\n  Running backtest mode...")
            orchestrator = activate_system("backtest")
            if orchestrator:
                run_system(orchestrator, duration_minutes=1)

        elif choice == "6":
            try:
                from core.credentials import get_credentials_manager
                manager = get_credentials_manager()
                creds = manager.list_credentials()

                print("\n  Stored Credentials:")
                print("  ─────────────────────")
                if creds:
                    for cred in creds:
                        print(f"  • {cred['name']} ({cred['exchange']}, {cred['environment']})")
                else:
                    print("  No credentials stored")

            except ImportError:
                print("  Credentials module not available")

        elif choice == "7":
            print("\n  Testing live data connection...")
            try:
                from core.live_data import create_binance_client
                client = create_binance_client()
                ticker = client.get_ticker("BTCUSDT")
                print(f"\n  ✓ Connection successful!")
                print(f"  BTC/USDT: ${ticker.price:,.2f}")
            except Exception as e:
                print(f"\n  ✗ Connection failed: {e}")

        elif choice == "8":
            print("\n  Goodbye!")
            break

        else:
            print("\n  Invalid option")


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Agentic Trading OS - Activation & Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python activate.py                    # Interactive menu
  python activate.py --mode paper       # Start paper trading
  python activate.py --mode live        # Start live trading
  python activate.py --check            # Check system status
  python activate.py --setup-creds      # Setup credentials
        """
    )

    parser.add_argument("--mode", choices=["paper", "live", "backtest"],
                       help="Trading mode")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT"],
                       help="Trading symbols")
    parser.add_argument("--capital", type=float, default=100000.0,
                       help="Initial capital")
    parser.add_argument("--check", action="store_true",
                       help="Check system status")
    parser.add_argument("--setup-creds", action="store_true",
                       help="Setup credentials interactively")
    parser.add_argument("--embed-creds", action="store_true",
                       help="Embed credentials from environment variables")

    args = parser.parse_args()

    # Check mode
    if args.check:
        print(BANNER)
        check_system()
        return

    # Setup credentials
    if args.setup_creds:
        print(BANNER)
        setup_credentials_interactive()
        return

    # Embed credentials from env
    if args.embed_creds:
        print(BANNER)
        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        if api_key and api_secret:
            setup_credentials_embedded(api_key, api_secret)
        else:
            print("  Set BINANCE_API_KEY and BINANCE_API_SECRET environment variables")
        return

    # Run with mode
    if args.mode:
        print(BANNER)
        orchestrator = activate_system(args.mode, args.symbols, args.capital)
        if orchestrator:
            run_system(orchestrator)
        return

    # Interactive menu
    quick_start_menu()


if __name__ == "__main__":
    main()
