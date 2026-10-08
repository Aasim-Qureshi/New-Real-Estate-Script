import asyncio
import time
from datetime import datetime


def log(msg: str, level: str = "INFO"):
    stamp = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "[i]", "OK": "[+]", "ERR": "[x]", "WARN": "[!]", "STEP": "[>]"}
    print(f"{icons.get(level, '[i]')} [{stamp}] {msg}", flush=True)


async def wait_for_element(page, selector, timeout=30, check_interval=0.2):
    start = time.time()
    while time.time() - start < timeout:
        try:
            element = await page.query_selector(selector)
            if element:
                return element
        except Exception:
            pass
        await asyncio.sleep(check_interval)
    return None


async def get_current_url(page) -> str:
    try:
        return str(await page.evaluate("window.location.href") or "")
    except Exception:
        return ""


async def get_text(page, selector: str) -> str:
    try:
        js = f"(document.querySelector({selector!r}) || {{}}).innerText || ''"
        return str(await page.evaluate(js) or "").strip()
    except Exception:
        return ""

async def click_button_by_text(page, text: str, timeout: int = 30) -> bool:
    js = f"""
    (() => {{
        const btn = [...document.querySelectorAll('button')]
            .find(b => b.innerText && b.innerText.includes({text!r}) && !b.disabled);
        if (!btn) return false;
        btn.scrollIntoView({{block: 'center'}});
        btn.click();
        return true;
    }})()
    """
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            if await page.evaluate(js):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False
