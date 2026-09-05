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
        import flask  # noqa: F401 - availability probe
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
        print("\nFlask is not installed. Install it with:")
        print(f"  {sys.executable} -m pip install flask")
        return 1

    # Import and run
    from web.app import run_server

    # Get config from environment or defaults
    # Loopback unless WEB_HOST says otherwise. A dashboard that can place
    # orders should not be reachable from the network by default.
    host = os.environ.get('WEB_HOST', '127.0.0.1')
    port = int(os.environ.get('WEB_PORT', '5000'))
    debug = os.environ.get('WEB_DEBUG', '').lower() == 'true'
    password = os.environ.get('WEB_PASSWORD') or os.environ.get('ADMIN_PASSWORD')
    default_hash_file = Path(__file__).parent / "config" / "dashboard_admin.hash"
    password_hash_file = Path(os.environ.get('ADMIN_PASSWORD_HASH_FILE', default_hash_file))
    has_password_hash = bool(os.environ.get('ADMIN_PASSWORD_HASH'))
    has_password_hash_file = password_hash_file.exists()

    if not password and not has_password_hash and not has_password_hash_file:
        print("Admin credentials are not configured.")
        print("Set ADMIN_PASSWORD_HASH, ADMIN_PASSWORD_HASH_FILE, or set ADMIN_PASSWORD/WEB_PASSWORD for local startup.")
        return 1

    if not debug and not (os.environ.get('WEB_SECRET_KEY') or os.environ.get('SECRET_KEY')):
        print("WEB_SECRET_KEY or SECRET_KEY is required when WEB_DEBUG is not true.")
        return 1

    # Background services (ingestion, scheduled brief, corpus alert sweep) are
    # all opt-in. Starting the dashboard must not silently begin polling
    # external feeds; see core/services.py for the environment variables.
    from core.services import describe_startup, start_background_services, stop_background_services

    services = start_background_services()
    print(f"  {describe_startup(services)}")

    print("\n  Starting web server...")
    print(f"  URL: http://localhost:{port}")
    print("  Login: configured admin user")
    print("\n  Press Ctrl+C to stop.\n")

    try:
        run_server(
            host=host,
            port=port,
            debug=debug,
            admin_password=password,
            admin_password_hash_path=str(password_hash_file)
        )
    finally:
        stop_background_services()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
