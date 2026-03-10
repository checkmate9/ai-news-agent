"""
setup_session.py

Opens a browser window so you can log into Twitter/X manually.
Once you're on the home timeline, press ENTER in this terminal
and the session will be saved automatically.

Usage:
    python3 setup_session.py
"""

import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()


def setup_session():
    session_file = os.getenv("SESSION_FILE", "session/twitter_auth.json")
    session_path = Path(session_file)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 55)
    print("  Twitter/X Session Setup")
    print("=" * 55)
    print()
    print("A browser window will open at x.com/login.")
    print("Log in manually (handle any 2FA/CAPTCHA yourself).")
    print()
    print("Once you can see your Twitter home timeline,")
    print("come back here and press ENTER to save the session.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")

        print("Browser is open. Log in now...")
        print()
        input(">>> Press ENTER here once you're on the home timeline: ")

        # Verify we're actually logged in
        current_url = page.url
        if "login" in current_url or "i/flow" in current_url:
            print()
            print("⚠️  Looks like you might not be fully logged in yet.")
            print(f"   Current URL: {current_url}")
            input("   Press ENTER again when you're on the home timeline: ")

        # Save session
        context.storage_state(path=str(session_path))
        browser.close()

    print()
    print(f"✅  Session saved to: {session_path}")
    print()
    print("You can now run the agent:")
    print("  python3 main.py --run-now   ← test a single digest")
    print("  python3 main.py             ← start scheduled operation")


if __name__ == "__main__":
    setup_session()
