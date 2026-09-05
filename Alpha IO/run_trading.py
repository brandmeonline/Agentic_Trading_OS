#!/usr/bin/env python3
"""
Quick Start Script for Agentic Trading OS with Alpaca.

Run this on your local machine to start paper trading.
"""

import os
import sys
import json
import hashlib
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

# Load credentials
# ATOS-P0-SEC-001: the environment is the authoritative credential source. A
# local config file is a development-only fallback and is gitignored; it must
# never be committed. Example placeholders live in the .example.json alongside.
CONFIG_FILE = Path(__file__).parent / "config" / "alpaca_credentials.json"
EXAMPLE_FILE = Path(__file__).parent / "config" / "alpaca_credentials.example.json"

def load_credentials():
    """Load Alpaca credentials, preferring environment variables.

    Returns (api_key, api_secret). Values are never logged in full.
    """
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    api_secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    if api_key and api_secret:
        return api_key, api_secret

    # Development fallback: untracked local file.
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            data = json.load(f)
            cred = data.get("alpaca_paper", {})
            return cred.get("api_key", "").strip(), cred.get("api_secret", "").strip()
    return "", ""

def main():
    """Main entry point."""
    print("\n" + "="*60)
    print("  Agentic Trading OS - Alpaca Paper Trading")
    print("="*60)

    api_key, api_secret = load_credentials()

    if not api_key or not api_secret:
        print("\n  ✗ No credentials found!")
        print("  Set them in the environment (preferred):")
        print("    export ALPACA_API_KEY=...")
        print("    export ALPACA_API_SECRET=...")
        print("  Or, for local development only, copy")
        print(f"    {EXAMPLE_FILE.name}")
        print(f"  to {CONFIG_FILE} and fill it in. That file is gitignored;")
        print("  never commit it, and rotate any key that has ever been committed.")
        return

    # Never print credential material, not even a prefix: 12 of 26 characters
    # is a material disclosure in a log or a screen share. A short digest
    # identifies which key is loaded and reveals nothing about it.
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:8]
    print(f"\n  API Key fingerprint: {fingerprint}")
    print("  Mode: Paper Trading (simulated)")
    print("  Symbols: AAPL, SPY, BTC/USD")
    print("  Capital: $100,000 (simulated)")

    # Test connection first
    print("\n  Testing Alpaca connection...")
    try:
        from core.alpaca_connector import create_alpaca_client
        client = create_alpaca_client(api_key, api_secret, paper=True)

        if client.connect():
            account = client.get_account()
            print(f"  ✓ Connected to Alpaca!")
            print(f"    Account Status: {account.get('status', 'N/A')}")
            print(f"    Buying Power: ${float(account.get('buying_power', 0)):,.2f}")
            print(f"    Portfolio Value: ${float(account.get('portfolio_value', 0)):,.2f}")
        else:
            print("  ✗ Connection failed - check your API credentials")
            return

    except Exception as e:
        print(f"  ✗ Connection error: {e}")
        return

    # Start the orchestrator
    print("\n  Starting trading system...")
    try:
        from core.orchestrator import create_orchestrator

        orchestrator = create_orchestrator(
            mode="paper",
            symbols=["AAPL", "SPY", "BTC/USD"],
            initial_capital=100000.0,
            exchange="alpaca",
            alpaca_api_key=api_key,
            alpaca_api_secret=api_secret
        )

        if orchestrator.initialize():
            print("\n  ✓ System initialized!")
            print("\n  Press Ctrl+C to stop.\n")
            orchestrator.run_forever()
        else:
            print("\n  ✗ Initialization failed")

    except KeyboardInterrupt:
        print("\n\n  Shutting down...")
    except Exception as e:
        print(f"\n  ✗ Error: {e}")

if __name__ == "__main__":
    main()
