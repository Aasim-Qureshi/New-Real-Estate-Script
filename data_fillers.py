import asyncio
import json
import re
import unicodedata

from nodriver import cdp

from utils import click_button_by_text

TODAY = "__TODAY__"   # sentinel: pick today's date from the calendar popup
ALL = "__ALL__"       # sentinel: tick every option in a multi-select

_PANEL_JS = """(cid) => {
    const s = document.getElementById(cid);
    let p = s && document.getElementById(s.getAttribute('aria-controls') || '');
    if (!p) {
        const a = [...document.querySelectorAll('.cdk-overlay-pane')];
        p = a[a.length - 1] || null;
    }
    return p;
}"""


async def multi_select_by_text(page, cid: str, option_text: str) -> bool:
    """Tick one specific option in a multi-select by its visible text."""
    if not await _open_select(page, cid):
        print("[!] multi-select dropdown never opened")
        await _close_overlays(page)
        return False

    opts = await _jeval(page, _opts_expr(cid))
    want = _norm(option_text)

    print(f"[i] Looking for role: {option_text!r}")

    target = None

    for o in opts:
        if o["dis"]:
            continue

        if _norm(o["text"]) == want:
            target = o
            break

    if target is None:
        print(f"[!] Role {option_text!r} not found")
        print("[i] Available options:")
        for o in opts:
            print(f"      - {o['text']!r}")

        await _escape(page)
        return False

    if not target["sel"]:
        await _click_option(page, cid, target["i"])
        await asyncio.sleep(0.25)

    # Verify selection
    cur = await _jeval(page, _opts_expr(cid))
    selected = cur[target["i"]]["sel"]

    await _escape(page)
    await asyncio.sleep(0.3)

    print(
        f"[{'+' if selected else '!'}] "
        f"{option_text!r}: {'selected' if selected else 'NOT selected'}"
    )

    return selected


def _opts_expr(cid: str) -> str:
    """JS expression: options of THIS select's open panel only."""
    return f"""(() => {{
        const p = ({_PANEL_JS})({cid!r});
        if (!p) return [];
        return [...p.querySelectorAll('mat-option')].map((o, i) => ({{
            i,
            text: (o.innerText || '').trim().replace(/\\s+/g, ' '),
            sel: o.getAttribute('aria-selected') === 'true',
            dis: o.getAttribute('aria-disabled') === 'true'
        }}));
    }})()"""


async def _close_overlays(page):
    if await page.evaluate(
            "!!document.querySelector('.cdk-overlay-backdrop-showing, .mat-mdc-select-panel')"):
        await _escape(page)
        await asyncio.sleep(0.3)


async def _open_select(page, cid: str, attempts: int = 3) -> bool:
    """Real mouse click on the select's trigger; succeed only when it's expanded WITH options."""
    loop = asyncio.get_event_loop()
    sel = await page.query_selector(f"#{cid}")
    if not sel:
        return False
    st = {}
    for n in range(1, attempts + 1):
        await _close_overlays(page)
        await sel.scroll_into_view()
        trig = await page.query_selector(f"#{cid} .mat-mdc-select-trigger") or sel
        await trig.mouse_move()
        await trig.mouse_click()
        deadline = loop.time() + 4
        while loop.time() < deadline:
            st = await _jeval(page, f"""(() => {{
                const s = document.getElementById({cid!r});
                const p = ({_PANEL_JS})({cid!r});
                return {{expanded: !!s && s.getAttribute('aria-expanded') === 'true',
                         n: p ? p.querySelectorAll('mat-option').length : 0}};
            }})()""")
            if st["expanded"] and st["n"]:
                return True
            await asyncio.sleep(0.15)
        print(f"[i] open attempt {n}/{attempts} for #{cid}: {st}")
    return False


async def _click_option(page, cid: str, i: int, js: bool = False) -> bool:
    """Tag option #i of this select's panel, scroll it into view, then click it."""
    tagged = await page.evaluate(f"""(() => {{
        document.querySelectorAll('[data-pick]').forEach(e => e.removeAttribute('data-pick'));
        const p = ({_PANEL_JS})({cid!r});
        const o = p && p.querySelectorAll('mat-option')[{i}];
        if (!o) return false;
        o.setAttribute('data-pick', '1');
        return true;
    }})()""")
    if not tagged:
        return False
    el = await page.query_selector("[data-pick='1']")
    if not el:
        return False
    if js:
        await el.apply("(e) => e.click()")
    else:
        await el.scroll_into_view()   # <- the valuer fix: option may be below the fold
        await el.mouse_move()
        await el.mouse_click()
    return True


async def peek_options(page, label: str, index: int = 0) -> list:
    """Print a select's options without choosing anything (for discovering values)."""
    cid = await find_control_id(page, label, index)
    if not cid:
        print(f"[!] peek: no field {label!r}")
        return []
    info = await _jeval(page, f"""(() => {{
        const e = document.getElementById({cid!r});
        return {{dis: e.getAttribute('aria-disabled') === 'true',
                 val: (e.querySelector('.mat-mdc-select-value-text')?.innerText || '').trim()}};
    }})()""")
    if info["dis"]:
        print(f"[i] {label}: disabled (value={info['val']!r})")
        return []
    if not await _open_select(page, cid):
        print(f"[!] {label}: could not open")
        return []
    opts = await _jeval(page, _opts_expr(cid))
    print(f"[i] {label}: current={info['val']!r}; {len(opts)} options:")
    for o in opts:
        print(f"      - {o['text']!r}")
    await _escape(page)
    await asyncio.sleep(0.3)
    return opts

_AR_NOISE = re.compile(r"[\u064B-\u065F\u0670\u0640\u200e\u200f\u202a-\u202e]")
_SELECT_ALL_RE = re.compile(r"^(تحديد |اختيار )?(الكل|الجميع)$|^(select )?all$")

_OPTS_JS = r"""
[...document.querySelectorAll('.cdk-overlay-container mat-option')].map((o, i) => ({
    i,
    text: (o.innerText || '').trim().replace(/\s+/g, ' '),
    sel: o.getAttribute('aria-selected') === 'true'
         || o.classList.contains('mdc-list-item--selected')
         || !!o.querySelector('.mat-pseudo-checkbox-checked'),
    dis: o.getAttribute('aria-disabled') === 'true'
}))
"""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = _AR_NOISE.sub("", s)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


async def _jeval(page, expr: str):
    """evaluate() and always get real Python data back (round-trips through JSON)."""
    return json.loads(await page.evaluate(f"JSON.stringify({expr})"))


async def _find_option(page, cid: str, option_text: str, timeout: float = 10):
    """Find an option by normalised text (exact first, then substring).
    On failure, print every option that was visible."""
    loop = asyncio.get_event_loop()
    want = _norm(option_text)
    deadline = loop.time() + timeout
    raw = []
    while loop.time() < deadline:
        opts = await page.query_selector_all(f"#{cid}-panel mat-option")
        if not opts:
            opts = await page.query_selector_all(".cdk-overlay-container mat-option")
        raw = [await o.apply("(el) => (el.innerText || '').trim()") for o in opts]
        norm = [_norm(r) for r in raw]
        for o, t, r in zip(opts, norm, raw):
            if t == want:
                return o, r
        for o, t, r in zip(opts, norm, raw):
            if want in t:
                return o, r
        await asyncio.sleep(0.1)

    print(f"[!] Option {option_text!r} not found. Options seen ({len(raw)}):")
    for r in raw:
        print(f"      - {r!r}")
    return None, ""

async def pick_today(page, cid: str, timeout: float = 8) -> bool:
    """Open the datepicker next to control #cid and click today's cell."""
    opened = await page.evaluate(f"""
        (() => {{
            const el = document.getElementById({cid!r});
            const b = el && el.closest('mat-form-field')
                         ?.querySelector('mat-datepicker-toggle button');
            if (!b) return false;
            b.scrollIntoView({{block: 'center'}});
            b.click();
            return true;
        }})()
    """)
    if not opened:
        print(f"[!] No datepicker toggle for #{cid}")
        return False

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        clicked = await page.evaluate("""
            (() => {
                const t = document.querySelector('.mat-calendar-body-today');
                if (!t) return false;
                (t.closest('.mat-calendar-body-cell') || t).click();
                return true;
            })()
        """)
        if clicked:
            break
        await asyncio.sleep(0.1)
    else:
        print("[!] Today cell not found in calendar")
        return False

    await asyncio.sleep(0.4)
    el = await page.query_selector(f"#{cid}")
    got = await el.apply("(e) => e.value")
    print(f"[{'+' if got else '!'}] date #{cid}: {got!r}")
    return bool(got)


async def multi_select_all(page, cid: str) -> bool:
    """Tick every real role. The header/select-all row (icon text 'check_box...') is never clicked."""
    if not await _open_select(page, cid):
        print("[!] roles dropdown never opened")
        await _close_overlays(page)
        return False

    snap = await _jeval(page, _opts_expr(cid))
    print(f"[i] {len(snap)} options: " + " | ".join(o["text"] for o in snap))

    def is_header(o):
        return "check_box" in o["text"] or "indeterminate" in o["text"]

    rows = [o for o in snap if not is_header(o) and not o["dis"]]
    for o in rows:
        cur = await _jeval(page, _opts_expr(cid))      # re-read live state before each click
        if cur[o["i"]]["sel"]:
            continue
        await _click_option(page, cid, o["i"])
        await asyncio.sleep(0.25)

    cur = await _jeval(page, _opts_expr(cid))
    for o in rows:                                      # one JS-click retry for stragglers
        if not cur[o["i"]]["sel"]:
            await _click_option(page, cid, o["i"], js=True)
            await asyncio.sleep(0.25)

    cur = await _jeval(page, _opts_expr(cid))
    missing = [cur[o["i"]]["text"] for o in rows if not cur[o["i"]]["sel"]]
    await _escape(page)
    await asyncio.sleep(0.3)
    ok = bool(rows) and not missing
    print(f"[{'+' if ok else '!'}] roles: {len(rows) - len(missing)}/{len(rows)} ticked"
          + (f", missing: {missing}" if missing else ""))
    return ok

async def wait_form_ready(page, ready_label: str | None = None,
                          ready_selector: str | None = None,
                          gone_label: str | None = None,
                          timeout: float = 30) -> bool:
    """
    Wait until the form step is really loaded.

    ready_label    : text of an <input-label> that must be visible (unique to this step)
    ready_selector : CSS selector that must match a visible element
    gone_label     : optional label from the PREVIOUS step that must have disappeared
    Always also requires: no visible skeleton-loader.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout

    js = f"""
    (() => {{
        const vis = e => e.getBoundingClientRect().width > 0;
        const readyLabel = {json.dumps(ready_label, ensure_ascii=False)};
        const readySel   = {json.dumps(ready_selector)};
        const goneLabel  = {json.dumps(gone_label, ensure_ascii=False)};

        if ([...document.querySelectorAll('skeleton-loader')].some(vis)) return false;

        const hasLabel = t => [...document.querySelectorAll('input-label')]
            .some(l => vis(l) && l.innerText.replace('*', '').trim().includes(t));

        if (goneLabel && hasLabel(goneLabel)) return false;
        if (readyLabel && !hasLabel(readyLabel)) return false;
        if (readySel && ![...document.querySelectorAll(readySel)].some(vis)) return false;

        if (!readyLabel && !readySel) {{
            // fallback: old generic behaviour
            return [...document.querySelectorAll(
                'input.mat-mdc-input-element, mat-select')].some(vis);
        }}
        return true;
    }})()
    """

    while loop.time() < deadline:
        if await page.evaluate(js):
            await asyncio.sleep(0.5)   # small settle time for Angular to finish rendering
            return True
        await asyncio.sleep(0.2)

    print(f"[!] Form never became ready (label={ready_label!r}, selector={ready_selector!r})")
    return False


async def find_control_id(page, label: str, index: int = 0, exact: bool = False):
    return await page.evaluate(f"""
        (() => {{
            const vis = e => e.getBoundingClientRect().width > 0;
            const clean = l => l.innerText.replace('*', '').replace(/\\s+/g, ' ').trim();
            const labels = [...document.querySelectorAll('input-label')]
                .filter(l => vis(l) && ({'true' if exact else 'false'}
                    ? clean(l) === {label!r}
                    : clean(l).includes({label!r})));
            const l = labels[{index}];
            if (!l) return null;
            const ctl = l.parentElement.querySelector('input, mat-select');
            return ctl ? ctl.id : null;
        }})()
    """)


async def wait_form_stable(page, quiet: float = 1.5, timeout: float = 30) -> bool:
    """Wait until the number of visible controls has not changed for `quiet` seconds
    and no skeleton loader is showing. Catches fields that render late."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last, since = None, loop.time()
    while loop.time() < deadline:
        sig = await page.evaluate("""
            (() => {
                const vis = e => e.getBoundingClientRect().width > 0;
                if ([...document.querySelectorAll('skeleton-loader')].some(vis)) return 'loading';
                return [...document.querySelectorAll('input-label, app-shared-map')]
                    .filter(vis).length + ':' +
                    [...document.querySelectorAll('input, mat-select')].filter(vis).length;
            })()
        """)
        if sig != last:
            last, since = sig, loop.time()
        elif sig != 'loading' and loop.time() - since >= quiet:
            print(f"[i] form stable: {sig}")
            return True
        await asyncio.sleep(0.2)
    print("[!] form never stabilised")
    return False


async def read_value(page, label: str, index: int = 0) -> str:
    """Current value of the control under `label` (input value or select text)."""
    cid = await find_control_id(page, label, index)
    if not cid:
        return ""
    return await page.evaluate(f"""
        (() => {{
            const e = document.getElementById({cid!r});
            if (!e) return '';
            if (e.tagName === 'MAT-SELECT')
                return e.classList.contains('mat-mdc-select-empty') ? '' :
                    (e.querySelector('.mat-mdc-select-value-text')?.innerText || '').trim();
            return e.value || '';
        }})()
    """)

async def select_by_label(page, label: str, option_text: str, index: int = 0,
                          timeout: float = 10) -> bool:
    loop = asyncio.get_event_loop()
    cid = await find_control_id(page, label, index)
    if not cid:
        print(f"[!] No select labelled {label!r}")
        return False

    select = await page.query_selector(f"#{cid}")
    await select.scroll_into_view()

    deadline = loop.time() + timeout          # cascading selects: wait until enabled
    while loop.time() < deadline:
        if await select.apply("(e) => e.getAttribute('aria-disabled') !== 'true'"):
            break
        await asyncio.sleep(0.1)
    else:
        print(f"[!] {label} stayed disabled")
        return False

    if not await _open_select(page, cid):
        print(f"[!] {label}: dropdown never opened / has no options")
        await _close_overlays(page)
        return False

    opts = await _jeval(page, _opts_expr(cid))
    want = _norm(option_text)
    hit = (next((o for o in opts if _norm(o["text"]) == want), None)
           or next((o for o in opts if want in _norm(o["text"])), None))
    if not hit:
        print(f"[!] Option {option_text!r} not found for {label}. Options seen ({len(opts)}):")
        for o in opts:
            print(f"      - {o['text']!r}")
        await _close_overlays(page)
        return False

    async def confirmed(wait=2.0):
        end = loop.time() + wait
        st = {}
        while loop.time() < end:
            st = json.loads(await select.apply("""(e) => JSON.stringify({
                empty: e.classList.contains('mat-mdc-select-empty'),
                value: (e.querySelector('.mat-mdc-select-value-text')?.innerText || '').trim()
            })"""))
            if not st["empty"]:
                return st
            await asyncio.sleep(0.05)
        return None

    await _click_option(page, cid, hit["i"])
    st = await confirmed()
    if not st:                                  # fallback: JS click on the same option
        await _click_option(page, cid, hit["i"], js=True)
        st = await confirmed()
    await _close_overlays(page)                 # never leave a panel open
    if st:
        print(f"[+] {label}: {st['value'] or hit['text']!r}")
        return True
    print(f"[!] {label}: selection not confirmed")
    return False

async def select_mat_option(page, testid: str, option_text: str, timeout: float = 10) -> bool:
    loop = asyncio.get_event_loop()

    # Pick the visible, topmost instance (last in DOM = latest overlay)
    sel_id = await page.evaluate(f"""
        (() => {{
            const els = [...document.querySelectorAll("mat-select[data-testid='{testid}']")];
            const vis = els.filter(e => e.getBoundingClientRect().width > 0);
            console.log(els.length);
            return vis.length ? vis[vis.length - 1].id : null;
        }})()
    """)
    if not sel_id:
        print("[!] no visible mat-select found")
        return False

    count = await page.evaluate(
        f"document.querySelectorAll(\"mat-select[data-testid='{testid}']\").length")
    print(f"[i] {count} instance(s) of {testid}; using #{sel_id}")

    select = await page.query_selector(f"#{sel_id}")
    await select.scroll_into_view()
    await select.click()

    panel = f"#{sel_id}-panel"
    target, deadline = None, loop.time() + timeout
    while loop.time() < deadline and not target:
        for opt in await page.query_selector_all(f"{panel} mat-option"):
            text = await opt.apply("(el) => (el.innerText || '').trim()")
            if option_text in text:
                target = opt
                break
        if not target:
            await asyncio.sleep(0.05)
    if not target:
        print("[!] option not found in this select's panel")
        return False

    await target.mouse_move()
    await target.mouse_click()

    # Verify against THIS select only
    deadline = loop.time() + 3
    state = None
    while loop.time() < deadline:
        state = await select.apply("""
            (el) => ({
                empty: el.classList.contains('mat-mdc-select-empty'),
                value: (el.querySelector('.mat-mdc-select-value-text')?.innerText || '').trim()
            })
        """)
        if not state["empty"] and option_text in state["value"]:
            return True
        await asyncio.sleep(0.05)

    print(f"[!] not confirmed: {state}")
    return False

async def select_first_matching(page, label: str, contains: str | None = None,
                                timeout: float = 10) -> bool:
    """Pick the first option (optionally whose text contains `contains`) of the select
    labelled exactly `label`. Falls back to the first enabled option."""
    loop = asyncio.get_event_loop()
    cid = await find_control_id(page, label, 0, exact=True) \
          or await find_control_id(page, label, 0)
    if not cid:
        print(f"[!] No select labelled {label!r}")
        return False
    select = await page.query_selector(f"#{cid}")
    await select.scroll_into_view()

    deadline = loop.time() + timeout            # cascading: wait until enabled
    while loop.time() < deadline:
        if await select.apply("(e) => e.getAttribute('aria-disabled') !== 'true'"):
            break
        await asyncio.sleep(0.2)
    else:
        print(f"[!] {label} stayed disabled")
        return False

    # options may load late after the previous select changes
    opts = []
    for _ in range(3):
        if not await _open_select(page, cid):
            await _close_overlays(page)
            await asyncio.sleep(0.5)
            continue
        opts = await _jeval(page, _opts_expr(cid))
        if opts:
            break
    enabled = [o for o in opts if not o["dis"]]
    if not enabled:
        print(f"[!] {label}: no options")
        await _close_overlays(page)
        return False

    want = _norm(contains) if contains else None
    hit = next((o for o in enabled if want and want in _norm(o["text"])), enabled[0])
    await _click_option(page, cid, hit["i"])
    await asyncio.sleep(0.5)
    await _close_overlays(page)
    val = await read_value_by_id(page, cid)
    print(f"[{'+' if val else '!'}] {label}: picked {hit['text']!r} (value={val!r})")
    return bool(val)


async def read_value_by_id(page, cid: str) -> str:
    return await page.evaluate(f"""
        (() => {{
            const e = document.getElementById({cid!r});
            if (!e) return '';
            if (e.tagName === 'MAT-SELECT')
                return e.classList.contains('mat-mdc-select-empty') ? '' :
                    (e.querySelector('.mat-mdc-select-value-text')?.innerText || '').trim();
            return e.value || '';
        }})()
    """)


async def select_radio(page, testid: str, text: str, timeout: float = 10) -> bool:
    """Click a mat-radio-button inside mat-radio-group[data-testid=...] by visible text."""
    loop = asyncio.get_event_loop()
    want = _norm(text)
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        found = await page.evaluate(f"""
            (() => {{
                document.querySelectorAll('[data-pick]').forEach(e => e.removeAttribute('data-pick'));
                const g = document.querySelector("mat-radio-group[data-testid='{testid}']");
                if (!g) return false;
                const r = [...g.querySelectorAll('mat-radio-button')]
                    .find(r => (r.innerText || '').trim().includes({text!r}));
                if (!r) return false;
                (r.querySelector('label') || r).setAttribute('data-pick', '1');
                return true;
            }})()
        """)
        if found:
            el = await page.query_selector("[data-pick='1']")
            await el.scroll_into_view()
            await el.mouse_move()
            await el.mouse_click()
            await asyncio.sleep(0.5)
            checked = await page.evaluate(f"""
                (() => {{
                    const g = document.querySelector("mat-radio-group[data-testid='{testid}']");
                    return [...g.querySelectorAll('mat-radio-button')]
                        .some(r => r.classList.contains('mat-mdc-radio-checked')
                                   && (r.innerText || '').includes({text!r}));
                }})()
            """)
            print(f"[{'+' if checked else '!'}] radio {testid}: {text!r}")
            return bool(checked)
        await asyncio.sleep(0.2)
    print(f"[!] radio {testid}/{text!r} not found")
    return False


async def select_when_options(page, label: str, timeout: float = 15) -> bool:
    """Select the first enabled option of `label`, waiting for options to load
    and verifying the value actually stuck."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempt = 0
    while loop.time() < deadline:
        attempt += 1
        cid = await find_control_id(page, label, 0, exact=True)
        if not cid:
            await asyncio.sleep(0.3)
            continue
        sel = await page.query_selector(f"#{cid}")
        if await sel.apply("(e) => e.getAttribute('aria-disabled') === 'true'"):
            await asyncio.sleep(0.3)
            continue
        if await read_value_by_id(page, cid):
            return True                      # already filled
        if await _open_select(page, cid, attempts=1):
            opts = [o for o in await _jeval(page, _opts_expr(cid)) if not o["dis"]]
            if opts:
                await _click_option(page, cid, opts[0]["i"], js=(attempt % 2 == 0))
                await asyncio.sleep(0.5)
                await _close_overlays(page)
                val = await read_value_by_id(page, cid)
                if val:
                    print(f"[+] {label}: {val!r}")
                    return True
        await _close_overlays(page)
        await asyncio.sleep(0.5)
    print(f"[!] {label}: never got a value")
    return False


async def set_number(page, label: str, value) -> bool:
    """Clear a number input (it may hold '0') and type value."""
    cid = await find_control_id(page, label, 0, exact=True) \
          or await find_control_id(page, label, 0)
    if not cid:
        print(f"[!] No input labelled {label!r}")
        return False
    el = await page.query_selector(f"#{cid}")
    await el.scroll_into_view()
    await el.click()
    await el.apply("""(e) => { e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true})); }""")
    await el.send_keys(str(value))
    await el.apply("(e) => e.blur()")
    got = await read_value_by_id(page, cid)
    ok = str(got) == str(value)
    print(f"[{'+' if ok else '!'}] {label}: wanted {value!r}, got {got!r}")
    return ok


async def click_button_by_icon(page, text: str, has_icon: bool, timeout: float = 20) -> bool:
    """Click the enabled, visible button containing `text`, choosing between the two
    same-text buttons by whether it has a <mat-icon> (opener: yes, submit: no)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    js = f"""
    (() => {{
        const vis = e => e.getBoundingClientRect().width > 0;
        const want = {json.dumps(text, ensure_ascii=False)};
        const b = [...document.querySelectorAll('button')].find(b =>
            vis(b) && !b.disabled
            && (b.innerText || '').includes(want)
            && (!!b.querySelector('mat-icon')) === {'true' if has_icon else 'false'});
        if (!b) return false;
        b.scrollIntoView({{block: 'center'}});
        b.click();
        return true;
    }})()
    """
    while loop.time() < deadline:
        if await page.evaluate(js):
            print(f"[+] Clicked {text!r} (icon={has_icon})")
            return True
        await asyncio.sleep(0.3)
    print(f"[!] {text!r} (icon={has_icon}) never clickable")
    return False


async def fill_method_form(page, approach_hint: str = "السوق", amount: int = 1000000,
                           method_type: str = "أساسي") -> bool:
    ok = await select_radio(page, "methodType", method_type)
    await asyncio.sleep(1)                       # asset list may depend on the radio
    await peek_options(page, "رقم الأصل")
    ok &= await select_when_options(page, "رقم الأصل")
    ok &= await select_first_matching(page, "أسلوب التقييم", approach_hint)
    await asyncio.sleep(1)                       # method list loads after approach
    ok &= await select_first_matching(page, "طريقة التقييم")
    ok &= await set_number(page, "الوزن", 100)
    ok &= await set_number(page, "القيمة", amount)
    await report_invalid(page)
    return ok

async def inspect_mat_select(page, testid: str, target: str = ""):
    selector = f"mat-select[data-testid='{testid}']"

    print("\n" + "=" * 80)
    print(f"INSPECTING MAT-SELECT: {testid}")
    print("=" * 80)

    # ------------------------------------------------------------
    # 1. Find mat-select
    # ------------------------------------------------------------

    select = await page.query_selector(selector)

    if not select:
        print(f"[x] Could not find {selector}")
        return False

    print(f"[+] Found: {selector}")

    # ------------------------------------------------------------
    # 2. Inspect the actual DOM element using Element.apply()
    # ------------------------------------------------------------

    info = await select.apply("""
        (el) => ({
            tag: el.tagName,
            id: el.id || '',
            className: el.className?.toString() || '',
            ariaExpanded: el.getAttribute('aria-expanded'),
            ariaDisabled: el.getAttribute('aria-disabled'),
            ariaInvalid: el.getAttribute('aria-invalid'),
            ariaRequired: el.getAttribute('aria-required'),
            dataTestId: el.getAttribute('data-testid'),
            outerHTML: el.outerHTML
        })
    """)

    print("\n--- MAT-SELECT ELEMENT ---")

    if not info:
        print("[x] Could not inspect mat-select")
        return False

    print(f"Tag:           {info.get('tag')}")
    print(f"ID:            {info.get('id')}")
    print(f"Class:         {info.get('className')}")
    print(f"Expanded:      {info.get('ariaExpanded')}")
    print(f"Disabled:      {info.get('ariaDisabled')}")
    print(f"Invalid:       {info.get('ariaInvalid')}")
    print(f"Required:      {info.get('ariaRequired')}")
    print(f"Test ID:       {info.get('dataTestId')}")

    # ------------------------------------------------------------
    # 3. Open dropdown
    # ------------------------------------------------------------

    print("\n[>] Opening mat-select...")

    try:
        await select.click()
    except Exception as e:
        print(f"[x] Could not click mat-select: {e}")
        return False

    await asyncio.sleep(0.5)

    # ------------------------------------------------------------
    # 4. Inspect the rendered mat-options
    #
    # We use page.query_selector_all() to get Element objects,
    # then Element.apply() on each one.
    # ------------------------------------------------------------

    options = await page.query_selector_all("mat-option")

    print("\n--- RENDERED OPTIONS ---")
    print(f"Found {len(options)} options")

    for index, option in enumerate(options):

        try:
            option_info = await option.apply("""
                (el) => ({
                    index: 0,
                    text: (
                        el.innerText ||
                        el.textContent ||
                        ''
                    ).trim(),

                    id: el.id || '',

                    ariaSelected:
                        el.getAttribute('aria-selected'),

                    ariaDisabled:
                        el.getAttribute('aria-disabled'),

                    className:
                        el.className?.toString() || '',

                    outerHTML:
                        el.outerHTML
                })
            """)

            print("\n" + "-" * 70)
            print(f"INDEX:         {index}")
            print(f"TEXT:          {option_info.get('text')}")
            print(f"ID:            {option_info.get('id')}")
            print(
                f"ARIA SELECTED: "
                f"{option_info.get('ariaSelected')}"
            )
            print(
                f"ARIA DISABLED: "
                f"{option_info.get('ariaDisabled')}"
            )
            print(f"CLASS:         {option_info.get('className')}")

            print("HTML:")
            print(option_info.get('outerHTML'))

        except Exception as e:
            print(f"[!] Could not inspect option {index}: {e}")

    # ------------------------------------------------------------
    # 5. Inspect CDK overlay
    # ------------------------------------------------------------

    overlays = await page.query_selector_all(".cdk-overlay-pane")

    print("\n--- CDK OVERLAY ---")
    print(f"Found {len(overlays)} overlay panes")

    for index, overlay in enumerate(overlays):

        try:
            overlay_info = await overlay.apply("""
                (el) => ({
                    className:
                        el.className?.toString() || '',

                    html:
                        el.outerHTML
                })
            """)

            print("\n" + "-" * 70)
            print(f"PANEL {index}")
            print(f"CLASS: {overlay_info.get('className')}")
            print(overlay_info.get('html'))

        except Exception as e:
            print(f"[!] Could not inspect overlay {index}: {e}")

    # ------------------------------------------------------------
    # 6. Inspect state AFTER opening
    # ------------------------------------------------------------

    after_open = await select.apply("""
        (el) => ({
            ariaExpanded:
                el.getAttribute('aria-expanded'),

            className:
                el.className?.toString() || '',

            empty:
                el.classList.contains(
                    'mat-mdc-select-empty'
                ),

            valueText:
                el.querySelector(
                    '.mat-mdc-select-value'
                )?.innerText?.trim() || ''
        })
    """)

    print("\n--- STATE AFTER OPENING ---")

    if after_open:
        print(
            f"Expanded: "
            f"{after_open.get('ariaExpanded')}"
        )
        print(
            f"Empty:    "
            f"{after_open.get('empty')}"
        )
        print(
            f"Value:    "
            f"{after_open.get('valueText')}"
        )

    print("\n" + "=" * 80)
    print("INSPECTION COMPLETE")
    print("=" * 80)

    # Do NOT select anything yet.
    return True


async def click_testid_button(page, testid: str, timeout: float = 10) -> bool:
    """Click the visible, enabled <button data-testid=...>, waiting for it to become enabled."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    selector = f"button[data-testid='{testid}']"

    while loop.time() < deadline:
        btn_id = await page.evaluate(f"""
            (() => {{
                const btns = [...document.querySelectorAll("{selector}")]
                    .filter(b => b.getBoundingClientRect().width > 0 && !b.disabled);
                if (!btns.length) return null;
                const b = btns[btns.length - 1];
                if (!b.id) b.id = 'auto-' + Math.random().toString(36).slice(2);
                return b.id;
            }})()
        """)
        if btn_id:
            btn = await page.query_selector(f"#{btn_id}")
            if btn:
                await btn.scroll_into_view()
                await btn.click()
                print(f"[+] Clicked {testid}")
                return True
        await asyncio.sleep(0.1)

    print(f"[!] {testid} button never became enabled/visible")
    return False

async def close_modal_by_empty_click(page, appear_timeout: float = 5, close_timeout: float = 3) -> bool:
    """Wait for a modal, click the empty backdrop area (trusted CDP click), confirm it closed."""
    loop = asyncio.get_event_loop()
    modal_open_js = """
        (() => [...document.querySelectorAll(
            'mat-dialog-container, .cdk-overlay-backdrop-showing, .modal.show'
        )].some(e => e.getBoundingClientRect().width > 0))()
    """

    # 1. Wait for the modal to appear
    deadline = loop.time() + appear_timeout
    while loop.time() < deadline:
        if await page.evaluate(modal_open_js):
            break
        await asyncio.sleep(0.05)
    else:
        print("[i] No modal appeared, continuing")
        return True

    # 2. Click an empty corner (far from the centered modal)
    x, y = 15, 15
    await page.send(cdp.input_.dispatch_mouse_event("mouseMoved", x=x, y=y))
    await page.send(cdp.input_.dispatch_mouse_event(
        "mousePressed", x=x, y=y, button=cdp.input_.MouseButton.LEFT, click_count=1))
    await page.send(cdp.input_.dispatch_mouse_event(
        "mouseReleased", x=x, y=y, button=cdp.input_.MouseButton.LEFT, click_count=1))

    # 3. Confirm it closed
    deadline = loop.time() + close_timeout
    while loop.time() < deadline:
        if not await page.evaluate(modal_open_js):
            print("[+] Modal closed")
            return True
        await asyncio.sleep(0.05)

    # Fallback: Escape key
    print("[!] Backdrop click didn't close it, trying Escape")
    for t in ("keyDown", "keyUp"):
        await page.send(cdp.input_.dispatch_key_event(
            t, key="Escape", code="Escape",
            windows_virtual_key_code=27, native_virtual_key_code=27))
    await asyncio.sleep(0.4)
    closed = not await page.evaluate(modal_open_js)
    print("[+] Modal closed via Escape" if closed else "[!] Modal still open")
    return closed


async def inspect_form_step(page, dump_prefix: str = "form_step") -> list:
    """Dump every visible control in the current form step to console + files."""
    raw = await page.evaluate("""
        (() => {
            const vis = e => e.getBoundingClientRect().width > 0;
            const root = document.body;
            const nodes = [...root.querySelectorAll(
                'input, textarea, mat-select, mat-radio-group, mat-checkbox, mat-slide-toggle, button'
            )].filter(e => vis(e) && !e.closest('mat-radio-button') && !e.closest('mat-checkbox'));

            const out = nodes.map((el, i) => {
                const field = el.closest('mat-form-field');
                const section = el.closest('mat-expansion-panel, fieldset, mat-card, section, .card');
                const tag = el.tagName.toLowerCase();
                const info = {
                    i, tag,
                    type: el.getAttribute('type') || '',
                    id: el.id || '',
                    testid: el.getAttribute('data-testid') || '',
                    formcontrol: el.getAttribute('formcontrolname') || el.getAttribute('ng-reflect-name') || '',
                    name: el.getAttribute('name') || '',
                    label: (el.closest('app-dynamic-field')?.querySelector('input-label, mat-label, label')?.innerText
                            || field?.querySelector('mat-label, label')?.innerText
                            || el.getAttribute('aria-label') || '').replace('*', '').trim(),
                    required: el.required === true || el.getAttribute('aria-required') === 'true',
                    disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
                    readonly: el.readOnly === true,
                    invalid: el.classList.contains('ng-invalid'),
                    section: (section?.querySelector('h1,h2,h3,h4,legend,mat-panel-title')?.innerText || '').trim().slice(0, 60),
                    isDate: !!field?.querySelector('mat-datepicker-toggle, [matdatepicker]') || el.hasAttribute('matdatepicker'),
                };
                if (tag === 'mat-select') {
                    info.value = (el.querySelector('.mat-mdc-select-value-text')?.innerText || '').trim();
                    info.empty = el.classList.contains('mat-mdc-select-empty');
                    info.panelId = el.getAttribute('aria-controls') || '';
                } else if (tag === 'mat-radio-group') {
                    info.options = [...el.querySelectorAll('mat-radio-button')].map(r => r.innerText.trim());
                } else if (tag === 'button') {
                    info.value = el.innerText.trim();
                } else if (tag === 'input' || tag === 'textarea') {
                    info.value = el.value || '';
                }
                return info;
            });
            return JSON.stringify({
                url: location.href,
                headings: [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(h => h.innerText.trim()).slice(0, 10),
                stepper: [...document.querySelectorAll('mat-step-header')].map(h => h.innerText.trim().replace(/\\s+/g, ' ')),
                controls: out,
                formHTML: root.outerHTML
            });
        })()
    """)

    data = json.loads(raw)
    controls = data["controls"]

    print("\n" + "=" * 80)
    print("FORM STEP ANALYSIS")
    print("URL:      ", data["url"])
    print("Headings: ", data["headings"])
    print("Stepper:  ", data["stepper"])
    print(f"Controls:  {len(controls)}")
    print("=" * 80)
    for c in controls:
        flags = "".join([
            " REQ" if c["required"] else "",
            " DIS" if c["disabled"] else "",
            " RO" if c["readonly"] else "",
            " DATE" if c["isDate"] else "",
            " INVALID" if c["invalid"] else "",
        ])
        key = c["testid"] or c["formcontrol"] or c["id"] or c["name"] or "-"
        print(f"[{c['i']:>2}] {c['tag']:<16} {c['type']:<8} key={key:<28} "
              f"label={c['label']!r} value={c.get('value', '')!r}{flags}"
              + (f" options={c['options']}" if c.get("options") else ""))

    with open(f"{dump_prefix}.json", "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in data.items() if k != "formHTML"}, f, ensure_ascii=False, indent=2)
    with open(f"{dump_prefix}.html", "w", encoding="utf-8") as f:
        f.write(data["formHTML"])
    print(f"\n[+] Saved {dump_prefix}.json and {dump_prefix}.html")

    return controls

async def _escape(page):
    for t in ("keyDown", "keyUp"):
        await page.send(cdp.input_.dispatch_key_event(
            t, key="Escape", code="Escape",
            windows_virtual_key_code=27, native_virtual_key_code=27))


async def _control_kind(page, cid: str) -> str:
    """'multi' | 'select' | 'input'"""
    return await page.evaluate(f"""
        (() => {{
            const el = document.getElementById({cid!r});
            if (!el) return 'input';
            if (el.tagName === 'MAT-SELECT')
                return el.hasAttribute('multiple') ? 'multi' : 'select';
            return 'input';
        }})()
    """)


async def multi_select_by_label(page, label: str, options: list, index: int = 0,
                                timeout: float = 10) -> bool:
    loop = asyncio.get_event_loop()
    cid = await find_control_id(page, label, index)
    if not cid:
        print(f"[!] No multi-select labelled {label!r}")
        return False

    sel = await page.query_selector(f"#{cid}")
    await sel.scroll_into_view()
    await sel.click()

    ok_all = True
    for text in options:
        target, deadline = None, loop.time() + timeout
        while loop.time() < deadline and not target:
            opts = await page.query_selector_all(f"#{cid}-panel mat-option")
            if not opts:  # custom panelClass: fall back to the overlay container
                opts = await page.query_selector_all(".cdk-overlay-container mat-option")
            for opt in opts:
                t = await opt.apply("(el) => (el.innerText || '').trim()")
                if text in t:
                    target = opt
                    break
            if not target:
                await asyncio.sleep(0.05)
        if not target:
            print(f"[!] Option {text!r} not found for {label}")
            ok_all = False
            continue
        already = await target.apply("(el) => el.getAttribute('aria-selected') === 'true'")
        if not already:
            await target.mouse_move()
            await target.mouse_click()
            await asyncio.sleep(0.15)

    await _escape(page)
    await asyncio.sleep(0.2)
    print(f"[{'+' if ok_all else '!'}] {label}: {options}")
    return ok_all


async def fill_field(page, label: str, value, index: int = 0) -> bool:
    """Auto-detect the control type behind `label` and fill it."""
    cid = await find_control_id(page, label, index, exact=True) \
          or await find_control_id(page, label, index)
    if not cid:
        print(f"[!] No field labelled {label!r}")
        return False

    kind = await _control_kind(page, cid)
    if kind == "multi":
        if value == ALL:
            return await multi_select_all(page, cid)

        if isinstance(value, str):
            return await multi_select_by_text(page, cid, value)

        vals = value if isinstance(value, (list, tuple)) else [value]
        return await multi_select_by_label(page, label, list(vals), index)

    if kind == "select":
        return await select_by_label(page, label, str(value), index)
    if value == TODAY:
        return await pick_today(page, cid)

    # text / number / date input
    el = await page.query_selector(f"#{cid}")
    await el.scroll_into_view()
    await el.click()
    await el.send_keys(str(value))
    await el.apply("(e) => e.blur()")  # commits datepicker values and marks the control touched
    got = await el.apply("(e) => e.value")
    ok = bool(got)
    print(f"[{'+' if ok else '!'}] {label}: wanted {value!r}, got {got!r}")
    return ok


async def fill_form(page, data: dict, participants: list | None = None,
                    retries: int = 2) -> bool:
    async def safe(label, value, index=0):
        try:
            return await fill_field(page, label, value, index)
        except Exception as e:
            print(f"[x] {label}: {type(e).__name__}: {e}")
            return False

    rows = list(data.items())
    for row in participants or []:
        rows += list(row.items())

    for label, value in rows:
        await safe(label, value)

    # Verify: re-read every simple field; refill whatever got cleared by a re-render.
    # (ALL / multi-selects are skipped: they have no single text value to compare.)
    for attempt in range(1, retries + 1):
        await asyncio.sleep(1.0)
        bad = []
        for label, value in rows:
            if value in (ALL,) or isinstance(value, (list, tuple)):
                continue
            if not await read_value(page, label):
                bad.append((label, value))
        if not bad:
            print("[+] verify: all fields hold their values")
            return True
        print(f"[!] verify pass {attempt}: empty fields {[l for l, _ in bad]}, refilling")
        for label, value in bad:
            await safe(label, value)

    return False


async def fill_map_coords(page, lat: str = "24.7136", lng: str = "46.6753",
                          timeout: float = 10) -> bool:
    """Switch the map to coordinates mode, type lat/lng, click 'تحديد الموقع'."""
    loop = asyncio.get_event_loop()

    # 1. Click the "البحث بالإحداثيات" toggle (inside app-shared-map only)
    switched = await page.evaluate("""
        (() => {
            const b = [...document.querySelectorAll('app-shared-map .map-mode-btn')]
                .find(b => (b.innerText || '').includes('البحث بالإحداثيات'));
            if (!b) return false;
            b.scrollIntoView({block: 'center'});
            b.click();
            return true;
        })()
    """)
    if not switched:
        print("[!] coordinates mode button not found")
        return False

    # 2. Wait for the two coord inputs to appear
    inputs = []
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        inputs = await page.query_selector_all("app-shared-map input.coord-input")
        if len(inputs) >= 2:
            break
        await asyncio.sleep(0.2)
    else:
        print("[!] coordinate inputs never appeared")
        return False

    # 3. Type latitude then longitude (real key events so Angular sees them)
    for el, val in zip(inputs[:2], (lat, lng)):
        await el.scroll_into_view()
        await el.click()
        await el.send_keys(val)
        await asyncio.sleep(0.2)
    await inputs[1].apply("(e) => e.blur()")

    # 4. Wait for 'تحديد الموقع' to become enabled, then click it
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        clicked = await page.evaluate("""
            (() => {
                const b = document.querySelector('app-shared-map button.coord-locate-btn');
                if (!b || b.disabled) return false;
                b.scrollIntoView({block: 'center'});
                b.click();
                return true;
            })()
        """)
        if clicked:
            await asyncio.sleep(2)  # let the map place the marker
            vals = [await i.apply("(e) => e.value") for i in inputs[:2]]
            print(f"[+] map location set: lat/lng = {vals}")
            return True
        await asyncio.sleep(0.2)

    print("[!] 'تحديد الموقع' stayed disabled (coords rejected?)")
    return False


async def fill_asset_form(page, data: dict, lat: str = "24.7136", lng: str = "46.6753") -> bool:
    ok = await fill_form(page, data)
    ok &= await fill_map_coords(page, lat, lng)
    await report_invalid(page)
    return ok

async def click_button_scoped(page, texts: list, timeout: float = 15) -> str | None:
    """Click the first enabled button whose normalised text contains any of `texts`.
    Searches the topmost dialog first, then falls back to the whole document."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    js = f"""
    (() => {{
        const vis = e => e.getBoundingClientRect().width > 0;
        const norm = s => (s || '')
            .normalize('NFKC')
            .replace(/[\\u200b-\\u200f\\u202a-\\u202e\\u064B-\\u065F\\u0640]/g, '')
            .replace(/[أإآ]/g, 'ا').replace(/ى/g, 'ي').replace(/ة/g, 'ه')
            .replace(/\\s+/g, ' ').trim();
        const wanted = {json.dumps(texts, ensure_ascii=False)}.map(norm);

        const dialogs = [...document.querySelectorAll('mat-dialog-container, .modal.show')].filter(vis);
        const roots = dialogs.length ? [dialogs[dialogs.length - 1], document] : [document];

        for (const root of roots) {{
            const btns = [...root.querySelectorAll('button')].filter(b => vis(b) && !b.disabled);
            for (let i = 0; i < wanted.length; i++) {{
                const b = btns.find(b => norm(b.innerText).includes(wanted[i]));
                if (b) {{ b.scrollIntoView({{block: 'center'}}); b.click(); return {json.dumps(texts, ensure_ascii=False)}[i]; }}
            }}
        }}
        return null;
    }})()
    """
    while loop.time() < deadline:
        hit = await page.evaluate(js)
        if hit:
            print(f"[+] Clicked button {hit!r}")
            return hit
        await asyncio.sleep(0.3)

    info = await page.evaluate("""JSON.stringify([...document.querySelectorAll('button')]
        .filter(b => b.getBoundingClientRect().width > 0)
        .map(b => ({t: (b.innerText || '').trim(), dis: b.disabled,
                    dlg: !!b.closest('mat-dialog-container, .modal.show')})))""")
    print(f"[!] none of {texts} clickable. Visible buttons: {info}")
    wanted_codes = [[hex(ord(c)) for c in t] for t in texts]
    print(f"[i] wanted code points: {wanted_codes}")
    return None

async def report_invalid(page):
    """Print which visible controls are still ng-invalid (why a continue button may not work)."""
    bad = await page.evaluate("""JSON.stringify([...document.querySelectorAll(
        'input.ng-invalid, mat-select.ng-invalid')]
        .filter(e => e.getBoundingClientRect().width > 0)
        .map(e => (e.closest('app-dynamic-field')?.querySelector('mat-label')?.innerText || e.id).trim()))""")
    bad = json.loads(bad)
    print(f"[{'!' if bad else '+'}] invalid controls: {bad}")
    return bad
