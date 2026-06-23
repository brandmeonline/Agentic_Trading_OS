#!/usr/bin/env python3
"""
Agentic Trading OS - Web Dashboard Launcher

Start the web-based trading dashboard.
"""

import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

def check_dependencies():
    """Check if required packages are installed."""
    missing = []

    try:
        import flask
    except ImportError:
        missing.append('flask')

    if missing:
        print("Missing dependencies detected!")
        print(f"Run: pip install {' '.join(missing)}")
        return False

    return True

def main():
    """Main entry point."""
    print("\n" + "="*60)
    print("  Agentic Trading OS - Web Dashboard")
    print("="*60)

    if not check_dependencies():
        print("\nInstalling Flask...")
        os.system(f"{sys.executable} -m pip install flask")

    # Import and run
    from web.app import run_server

    # Get config from environment or defaults
    host = os.environ.get('WEB_HOST', '0.0.0.0')
    port = int(os.environ.get('WEB_PORT', '5000'))
    debug = os.environ.get('WEB_DEBUG', '').lower() == 'true'
    password = os.environ.get('WEB_PASSWORD') or os.environ.get('ADMIN_PASSWORD')
    has_password_hash = bool(os.environ.get('ADMIN_PASSWORD_HASH'))

    if not password and not has_password_hash:
        print("Admin credentials are not configured.")
        print("Set ADMIN_PASSWORD_HASH, or set ADMIN_PASSWORD/WEB_PASSWORD for local startup.")
        return 1

    if not debug and not (os.environ.get('WEB_SECRET_KEY') or os.environ.get('SECRET_KEY')):
        print("WEB_SECRET_KEY or SECRET_KEY is required when WEB_DEBUG is not true.")
        return 1

    print(f"\n  Starting web server...")
    print(f"  URL: http://localhost:{port}")
    print("  Login: configured admin user")
    print("\n  Press Ctrl+C to stop.\n")

    run_server(
        host=host,
        port=port,
        debug=debug,
        admin_password=password
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
