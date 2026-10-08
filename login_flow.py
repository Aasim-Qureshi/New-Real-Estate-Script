
import asyncio
import os
import sys
from dotenv import load_dotenv
from browser import close_browser, navigate

from utils import click_button_by_text, get_current_url, get_text, log, wait_for_element

from data_fillers import (
    select_mat_option, click_testid_button, close_modal_by_empty_click,
    wait_form_ready, set_number, fill_form, inspect_form_step, TODAY, ALL,
    peek_options, fill_asset_form, click_button_scoped, report_invalid, fill_method_form, click_button_by_icon
)

import random
import string

import os

async def upload_file(page, path: str, testid: str = "input", timeout: float = 20) -> bool:
    """Attach a file to <input type=file data-testid=...> via CDP (no OS dialog)."""
    loop = asyncio.get_event_loop()
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        print(f"[!] File not found: {abs_path}")
        return False

    # The input may exist but be hidden, so only require it to be in the DOM.
    el = None
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        el = await page.query_selector(f"input[type='file'][data-testid='{testid}']") \
             or await page.query_selector("input[type='file']")
        if el:
            break
        await asyncio.sleep(0.3)
    if not el:
        print("[!] file input never appeared")
        return False

    await el.send_file(abs_path)   # nodriver: DOM.setFileInputFiles + dispatches change
    # Safety net: make sure Angular sees the change event.
    await el.apply("(e) => { e.dispatchEvent(new Event('input', {bubbles: true}));"
                   " e.dispatchEvent(new Event('change', {bubbles: true})); }")

    # Verify: the input holds the file, and wait for the upload to finish
    # (the "حفظ ومتابعة" button becoming enabled is the real signal).
    await asyncio.sleep(1)
    name = await el.apply("(e) => e.files && e.files[0] ? e.files[0].name : ''")
    print(f"[{'+' if name else '!'}] file input holds: {name!r}")
    return bool(name)

def generate_report_number() -> str:
    return (
        random.choice(string.ascii_uppercase)
        + "".join(random.choices(string.digits, k=5))
        + random.choice(string.ascii_uppercase)
    )

load_dotenv()

LOGIN_URL = (
    "https://sso.taqeem.gov.sa/realms/REL_TAQEEM/protocol/openid-connect/auth"
    "?client_id=cli-web&redirect_uri=https%3A%2F%2Feservices-evaluators.taqeem.gov.sa%2F"
    "&state=c84eb62e-bf9d-45ff-9e6e-a8089bfa734e&response_mode=fragment"
    "&response_type=code&scope=openid&nonce=8d845815-a680-4c60-9d6e-3b798de08fc1"
    "&code_challenge=H5y2KOEDbIZ9w4tUqG84jaVCXNRCn20YMlcn_ZqRNuw&code_challenge_method=S256"
)
# state/nonce/code_challenge are single-use, so if the URL above is rejected we fall back
# to the app root, which redirects to a fresh SSO login URL automatically.
FALLBACK_URL = "https://eservices-evaluators.taqeem.gov.sa/"
REPORTS_URL = "https://eservices-evaluators.taqeem.gov.sa/my-reports"
APP_HOST = "eservices-evaluators.taqeem.gov.sa"

OTP_TIMEOUT = int(os.getenv("OTP_WAIT_TIMEOUT", "300"))  # seconds the user has to type the OTP

STEP1_DATA = {
    "نوع التقرير": "سردي (شامل)",
    "رقم التقرير": generate_report_number(),
    "اسم العميل": "Example Person",
    "الغرض من التقييم": "رهن عقاري",
    "تاريخ المعاينة": TODAY,
    "تاريخ التقييم": TODAY,
    "تاريخ إصدار التقرير": TODAY,
    "أساس القيمة": "القيمة السوقية",
    "فرضية القيمة": "أعلى وأفضل استخدام",
}

STEP1_PARTICIPANTS = [
    {
        "المقيم": "1210000414",
        "نسبة المشاركة (%)": 100,
        "الأدوار": ALL,
    },
]

STEP2_DATA = {
    "المنطقة": "الرياض",
    "المدينة": "الرياض",
    "نوع الأصل": "فيلا",
    "الاستخدام الحالي للأرض": "سكني",
    "رقم صك الملكية أو السجل العيني": "1234567890",
    "اسم المالك": "Example Owner",
    "رقم القطعة": "1234",
    "رقم البلوك": "56",
    "مساحة الأرض (متر مربع)": "500",
    "مساحة المبنى (متر مربع)": "300",
}

STEP2_LAT = "24.7136"
STEP2_LNG = "46.6753"

async def fill_credentials(page, username: str, password: str) -> bool:
    user_input = await wait_for_element(page, "#username", 20)
    if not user_input:
        return False

    await user_input.send_keys(username)

    pass_input = await wait_for_element(page, "input[type='password']", 15)
    if not pass_input:
        log("Password input not found", "ERR")
        return False
    await pass_input.send_keys(password)

    login_btn = await wait_for_element(page, "#kc-login", 15)
    if not login_btn:
        log("Login button (#kc-login) not found", "ERR")
        return False
    await login_btn.click()
    return True


async def wait_for_login_success(page, timeout: int) -> bool:
    """
    Login is considered successful when we're back on the evaluators site
    (not the SSO host) AND an <h1> exists (the 'مرحبًا ...' greeting).
    The URL check avoids false positives, since the SSO pages may also contain an <h1>.
    """
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    last_notice = 0.0

    while loop.time() < end:
        url = await get_current_url(page)
        if APP_HOST in url and "sso.taqeem.gov.sa" not in url:
            h1 = await wait_for_element(page, "h1", 3)
            if h1:
                greeting = await get_text(page, "h1")
                log(f"Login confirmed. h1: {greeting}", "OK")
                return True

        now = loop.time()
        if now - last_notice > 15:
            log("Waiting for you to enter the OTP in the browser...", "STEP")
            last_notice = now
        await asyncio.sleep(1)

    return False


async def run(username: str, password: str) -> int:
    try:
        log("Opening login page", "STEP")
        page = await navigate(LOGIN_URL)

        if not await wait_for_element(page, "#username", 15):
            log("Login form not found on given URL, trying fallback entry URL", "WARN")
            page = await navigate(FALLBACK_URL)

        if not await fill_credentials(page, username, password):
            log("Could not submit credentials", "ERR")
            return 1
        log("Credentials submitted. Please type the OTP manually.", "OK")

        if not await wait_for_login_success(page, OTP_TIMEOUT):
            log(f"Login not confirmed within {OTP_TIMEOUT}s", "ERR")
            return 2

        log("Navigating to my-reports", "STEP")
        page = await navigate(REPORTS_URL)
        await asyncio.sleep(3)
        log(f"Now at: {await get_current_url(page)}", "OK")

        log("Clicking 'إنشاء تقرير'", "STEP")
        if await click_button_by_text(page, "إنشاء تقرير", timeout=30):
            log("Create-report button clicked", "OK")
            await asyncio.sleep(2)
            log(f"Now at: {await get_current_url(page)}", "OK")
            if await click_button_by_text(page, "إنشاء تقرير", timeout=30):
                log("Create-report button clicked", "OK")
                await asyncio.sleep(2)

                log("Selecting facility", "STEP")
                if await select_mat_option(page, "createReportFacility", "11000149"):
                    log("Facility selected", "OK")
                else:
                    log("Failed to select facility", "ERR")
                    return 4

                log("Clicking Continue", "STEP")
                if await click_testid_button(page, "continue"):
                    log("Continue clicked", "OK")
                    await asyncio.sleep(2)
                    log(f"Now at: {await get_current_url(page)}", "OK")
                else:
                    log("Continue button not clickable", "ERR")
                    return 5

                log("Closing modal", "STEP")
                if not await close_modal_by_empty_click(page):
                    log("Modal did not close", "WARN")

                log("Filling form step 1", "STEP")
                if not await wait_form_ready(page, ready_label="نوع التقرير"):
                    return 7

                ok = await fill_form(page, STEP1_DATA, STEP1_PARTICIPANTS)
                log(f"Step 1 filled ok={ok}", "OK" if ok else "WARN")
                await asyncio.sleep(0.5)

                log("Clicking save & continue (step 1)", "STEP")
                await click_button_by_text(page, "حفظ ومتابعة", timeout=15)
                print("[+] Navigated to next step.")

                log("Clicking add asset", "STEP")
                await click_button_by_text(page, "إضافة أصل", timeout=30)

                if not await wait_form_ready(page, ready_label="رقم القطعة"):
                    log("Asset form never loaded", "ERR")
                    return 8

                log("Filling asset form (step 2)", "STEP")
                ok2 = await fill_asset_form(page, STEP2_DATA, STEP2_LAT, STEP2_LNG)
                log(f"Asset form filled ok={ok2}", "OK" if ok2 else "WARN")

                # --- First "حفظ وإضافة الأصل" (asset form -> next page) ---
                await asyncio.sleep(2)   # let validation settle after the last field
                log("Clicking 'حفظ وإضافة الأصل' (1st)", "STEP")
                if not await click_button_scoped(page, ["حفظ وإضافة الأصل"], timeout=20):
                    log("Button never became enabled; dumping invalid controls", "ERR")
                    await report_invalid(page)
                    await inspect_form_step(page, "asset_after_fill")
                    await asyncio.Event().wait()

                # Wait until the asset form is really gone (page changed)
                if not await wait_form_ready(page, gone_label="رقم القطعة", timeout=40):
                    log("Still on the asset form after saving", "ERR")
                    return 13
                await asyncio.sleep(2)
                log(f"Now at: {await get_current_url(page)}", "OK")

                # Dump this intermediate page so we know what's on it
                await inspect_form_step(page, "page_after_first_save")

                # --- Second "حفظ وإضافة الأصل" ---
                log("Clicking 'حفظ وإضافة الأصل' (2nd)", "STEP")
                if not await click_button_scoped(page, ["حفظ ومتابعة"], timeout=30):
                    log("2nd button not clickable; see button list above", "ERR")
                    await inspect_form_step(page, "page_after_first_save")
                    await asyncio.Event().wait()

                await asyncio.sleep(3)   # navigation + skeleton loaders
                await wait_form_ready(page)
                log(f"Now at: {await get_current_url(page)}", "OK")

                # --- Step 3: أسلوب التقييم ---
                if not await wait_form_ready(page, ready_selector="app-report-step-method"):
                    log("Step 3 never loaded", "ERR")
                    return 9
                log(f"Now at step 3: {await get_current_url(page)}", "OK")

                AMOUNT = 1000000

                log("Opening method form", "STEP")
                if not await click_button_by_icon(page, "إضافة الأسلوب والطريقة", has_icon=True):
                    await inspect_form_step(page, "method_opener_failed")
                    await asyncio.Event().wait()
                if not await wait_form_ready(page, ready_selector="mat-radio-group[data-testid='methodType']"):
                    log("Method form did not open", "ERR")
                    return 10
                await asyncio.sleep(1)

                log("Filling method form (step 3)", "STEP")
                ok3 = await fill_method_form(page, "السوق", AMOUNT)
                if not ok3:
                    await inspect_form_step(page, "method_fill_failed")
                    await asyncio.Event().wait()
                log(f"Method form filled ok={ok3}", "OK" if ok3 else "WARN")
                await asyncio.sleep(1)

                log("Submitting method", "STEP")
                if not await click_button_by_icon(page, "إضافة الأسلوب والطريقة", has_icon=False, timeout=20):
                    await report_invalid(page)
                    await inspect_form_step(page, "method_form_failed")
                    await asyncio.Event().wait()
                await asyncio.sleep(2)

                # Final opinion value: same number as the method value
                log("Setting final value", "STEP")
                await set_number(page, "رقم الأصل 1", AMOUNT)
                await asyncio.sleep(1)

                log("Clicking save & continue (step 3)", "STEP")
                if not await click_button_scoped(page, ["حفظ ومتابعة"], timeout=30):
                    await report_invalid(page)
                    await inspect_form_step(page, "step3_failed")
                    await asyncio.Event().wait()
                await asyncio.sleep(3)
                log(f"Now at: {await get_current_url(page)}", "OK")

                # --- Step 4: أصل التقرير ---
                if not await wait_form_ready(page, ready_selector="app-report-step-origin"):
                    log("Step 4 never loaded", "ERR")
                    return 11
                log(f"Now at step 4: {await get_current_url(page)}", "OK")

                log("Uploading test.pdf", "STEP")
                pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test.pdf")
                if not await upload_file(page, pdf_path):
                    await inspect_form_step(page, "step4_upload_failed")
                    await asyncio.Event().wait()

                # Wait for upload processing: the continue button enables when it's done
                log("Saving step 4", "STEP")
                if not await click_button_scoped(page, ["حفظ ومتابعة"], timeout=60):
                    await inspect_form_step(page, "step4_failed")
                    await asyncio.Event().wait()
                await asyncio.sleep(3)
                log(f"Now at: {await get_current_url(page)}", "OK")

                await inspect_form_step(page, "form_step5")
                print("[+] Step 5 inspected. Browser will remain open.")
                await asyncio.Event().wait()
        else:
            log("Create-report button not found", "ERR")
            return 3

        # Keep the browser open so you can work in it; Ctrl+C to quit.
        log("Done. Press Ctrl+C to close the browser.", "INFO")
        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    finally:
        await close_browser()


def main():
    username = os.getenv("TAQEEM_USER") or (sys.argv[1] if len(sys.argv) > 1 else "")
    password = os.getenv("TAQEEM_PASS") or (sys.argv[2] if len(sys.argv) > 2 else "")
    if not username or not password:
        print("Set TAQEEM_USER and TAQEEM_PASS (env/.env) or pass: python login_flow.py <user> <pass>")
        sys.exit(1)
    try:
        sys.exit(asyncio.run(run(username, password)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
