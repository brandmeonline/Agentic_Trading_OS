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
    password = os.environ.get('WEB_PASSWORD', 'admin')

    print(f"\n  Starting web server...")
    print(f"  URL: http://localhost:{port}")
    print(f"  Login: admin / {password}")
    print("\n  Press Ctrl+C to stop.\n")

    run_server(
        host=host,
        port=port,
        debug=debug,
        admin_password=password
    )

if __name__ == "__main__":
    main()
