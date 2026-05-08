"""Interactive Playwright verification for PLAN milestones.
Run with: python tests/playwright_interactive_check.py
"""
from pathlib import Path
import subprocess
import sys
import time

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"Playwright unavailable: {exc}")
        return 2

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [sys.executable, "run_web.py"],
        cwd=root / "Alpha IO",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        time.sleep(4)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("http://127.0.0.1:5000/login", wait_until="networkidle")
            page.fill("input[name='username']", "admin")
            page.fill("input[name='password']", "admin")
            page.click("button[type='submit']")
            for route in ["/", "/trading", "/analytics", "/settings"]:
                page.goto(f"http://127.0.0.1:5000{route}", wait_until="networkidle")
                page.screenshot(path=str(ARTIFACT_DIR / f"{('dashboard' if route=='/' else route.strip('/'))}.png"), full_page=True)
            browser.close()
        print(f"Artifacts written to {ARTIFACT_DIR}")
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
