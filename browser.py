import os
import sys

import nodriver as uc

from utils import log

_browser = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)


async def get_browser(headless=None):
    """Start (or reuse) a nodriver browser. Visible by default so the user can type the OTP."""
    global _browser
    if _browser is not None:
        return _browser

    if headless is None:
        headless = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")

    _browser = await uc.start(
        headless=headless,
        user_data_dir=None,
        browser_args=[
            f"--user-agent={USER_AGENT}",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no_sandbox",
            "--disable-popup-blocking",
            "--lang=en-US",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        window_size=(1366, 850),
    )
    log(f"Browser started (headless={headless})", "OK")
    return _browser


async def navigate(url: str):
    """Navigate the main tab to url and return the tab."""
    browser = await get_browser()
    return await browser.get(url)


async def close_browser():
    global _browser
    if _browser:
        try:
            _browser.stop()
        except Exception:
            pass
    _browser = None
    log("Browser closed", "INFO")
