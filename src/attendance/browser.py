"""Portal login and page inspection.

The logon page carries a CAPTCHA, so login is deliberately semi-automatic:
this fills the username and password, then hands the browser to you for the
CAPTCHA. Everything after login runs unattended.

Credentials pass straight from the environment into Playwright's fill() and are
never logged, echoed, or written to disk. Keep it that way when editing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout

PORTAL_URL = "https://sdc-sppap1.svkm.ac.in:50001/irj/portal"

USERNAME_FIELD = "#logonuidfield"  # name="j_username"
PASSWORD_FIELD = "#logonpassfield"  # name="j_password"
CAPTCHA_FIELD = "#txtInput"  # no name attribute — id only
LOGON_BUTTON = "#Button1"  # type="button", JS-driven; must be clicked


class LoginTimeout(RuntimeError):
    """The CAPTCHA went unanswered long enough that we gave up."""


def login(page: Page, username: str, password: str, wait_seconds: int = 180) -> None:
    """Fill credentials, then block until the CAPTCHA is solved and login lands.

    Success is detected by the logon field detaching from the DOM, which happens
    on any successful login regardless of where the portal redirects afterwards.
    """
    page.goto(PORTAL_URL, wait_until="domcontentloaded")

    page.fill(USERNAME_FIELD, username)
    page.fill(PASSWORD_FIELD, password)

    # Park the cursor on the CAPTCHA box so the only thing left is to type it.
    page.click(CAPTCHA_FIELD)

    print(f"\n  Type the CAPTCHA in the browser window, then click Log On.")
    print(f"  Waiting up to {wait_seconds // 60} minutes...\n")

    try:
        page.wait_for_selector(USERNAME_FIELD, state="detached", timeout=wait_seconds * 1000)
    except PlaywrightTimeout as exc:
        raise LoginTimeout(
            "Login did not complete in time. If the CAPTCHA was rejected, the page "
            "reloads with a fresh one — rerun and try again."
        ) from exc

    # NOT networkidle: this SAP EP shell long-polls forever, so the network never
    # goes quiet and networkidle always times out. Confirm login by a real signal
    # instead — the attendance tab in the masthead only exists once we're in.
    try:
        page.wait_for_selector(f"text={NAV_ATTENDANCE_TAB}", timeout=30_000)
    except PlaywrightTimeout as exc:
        raise LoginTimeout(
            "Logged in, but the portal desktop did not appear. The login may have "
            "been rejected, or the portal is slow — rerun and try again."
        ) from exc

    print("  Logged in.\n")


def open_login(page: Page, username: str, password: str) -> None:
    """Load the login page and fill username + password, ready for a CAPTCHA.

    Called fresh on each attempt: a rejected CAPTCHA reloads the page and clears
    these fields, so re-loading is the reliable way to get a clean known state
    (and a fresh CAPTCHA image).
    """
    page.goto(PORTAL_URL, wait_until="domcontentloaded")
    page.fill(USERNAME_FIELD, username)
    page.fill(PASSWORD_FIELD, password)
    page.click(CAPTCHA_FIELD)


def captcha_image(page: Page) -> bytes:
    """PNG bytes of the login box, for the human to read the CAPTCHA from.

    Screenshots the whole login form rather than a lone image element: the
    CAPTCHA is rendered without a stable id, so capturing the surrounding box is
    both robust and gives the reader helpful context.
    """
    form = page.locator("form[name=logonForm]")
    target = form if form.count() else page.locator(f"{CAPTCHA_FIELD} >> xpath=ancestor::table[1]")
    try:
        return target.first.screenshot()
    except Exception:
        return page.screenshot()  # last resort: whole viewport


def submit_captcha(page: Page, answer: str) -> None:
    """Type the CAPTCHA answer and click Log On. Case is preserved deliberately —
    the portal states the CAPTCHA is case-sensitive."""
    page.fill(CAPTCHA_FIELD, answer)
    page.click(LOGON_BUTTON)


def login_succeeded(page: Page, timeout_ms: int = 20_000) -> bool:
    """True if we reached the portal desktop, False if the login was rejected."""
    try:
        page.wait_for_selector(f"text={NAV_ATTENDANCE_TAB}", timeout=timeout_ms)
        return True
    except PlaywrightTimeout:
        return False


# Chrome-shell frames that never hold content. Skipping them keeps the dump readable.
_NOISE = ("emptyhover.html", "EmptyDocument.html", "about:blank")

# WebDynpro renders semantics into ct/lsdata rather than sensible tags, and the
# attendance marks live in table cells, so both are worth capturing.
CONTROL_SELECTOR = (
    "a, button, input, select, textarea, "
    "[role=button], [role=link], [role=tab], [role=combobox], [ct]"
)

_DESCRIBE_JS = """els => {
    const seen = new Set();
    const out = [];
    for (const e of els) {
        const label = (e.innerText || e.value || e.getAttribute('title')
            || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
        const id = e.id ? '#' + e.id : '';
        const name = e.name ? `[name=${e.name}]` : '';
        const ct = e.getAttribute('ct') ? ` ct=${e.getAttribute('ct')}` : '';
        const line = `${e.tagName.toLowerCase()}${id}${name}${ct} ${label}`.trim();
        // WebDynpro emits hundreds of near-identical hidden inputs; collapse them.
        if (seen.has(line)) continue;
        seen.add(line);
        out.push(line);
        if (out.length >= 120) break;
    }
    return out;
}"""


def _is_noise(url: str) -> bool:
    return any(marker in url for marker in _NOISE)


@dataclass
class FrameInfo:
    path: str
    url: str
    controls: list[str]


def inspect(page: Page) -> list[FrameInfo]:
    """Dump the frame tree and interactive controls of the current page.

    WebDynpro nests content in iframes and generates unstable ids, so knowing
    which frame holds which control is the thing worth capturing before writing
    any real navigation code.
    """
    found: list[FrameInfo] = []

    for frame in page.frames:
        if _is_noise(frame.url):
            continue

        try:
            # Let a mid-navigation frame settle before reading it.
            frame.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass  # best effort; read whatever is there

        try:
            controls = frame.eval_on_selector_all(CONTROL_SELECTOR, _DESCRIBE_JS)
        except Exception as exc:
            # Report the real reason — "Error" told us nothing last time.
            controls = [f"<unreadable: {type(exc).__name__}: {exc}>".replace("\n", " ")[:300]]

        if not controls:
            continue  # empty frame, nothing to learn from it

        found.append(FrameInfo(path=frame.name or "<main>", url=frame.url, controls=controls))

    return found


# Portal navigation, always by visible text — the navNodeAnchor ids are
# positional (the sub-tab id is shared with 'Personal Data') so they can't be
# trusted for clicks.
NAV_ATTENDANCE_TAB = "Attendance Display for Students"
NAV_STUDENT_ATTENDANCE = "Student Attendance"

# The attendance app itself, inside the cross-origin work area frame.
ATTENDANCE_APP = "ZSVKM_STUDENT_ATTENDANC"
SUBMIT_LABEL = "SUBMIT"

# Portal message bars announce a dead session rather than failing loudly.
TIMEOUT_MARKERS = ("iView has timed out", "no cached content")

# The portal only serves the report between 6 PM and 7 AM; outside that it shows
# this instead of a PDF. Match loosely — times/formatting vary by SAP version.
WINDOW_MARKERS = ("Please view attendance between", "PM to 07:00", "06:00 PM to")


class WindowClosed(RuntimeError):
    """Outside the portal's 6 PM - 7 AM attendance viewing window."""


def window_blocked(page: Page) -> bool:
    """True if the attendance app is showing the out-of-window message."""
    frame = attendance_frame(page)
    if frame is None:
        return False
    try:
        text = frame.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    return any(marker in text for marker in WINDOW_MARKERS)


class SessionExpired(RuntimeError):
    """The portal iView timed out — anything read after this point is stale."""


class FormNotReady(RuntimeError):
    """The attendance app never finished rendering."""


def check_alive(page: Page) -> None:
    """Fail loudly on a timed-out iView.

    Scoped to the portal shell's *visible* message bars only. The shell keeps
    stale hidden bars around from previously-visited iViews, and an earlier
    version of this read the whole page and aborted healthy runs because of them.
    """
    bars = page.locator("div[ct=MB]:visible")
    for i in range(bars.count()):
        text = bars.nth(i).inner_text()
        if any(marker in text for marker in TIMEOUT_MARKERS):
            raise SessionExpired(
                "The portal session timed out. Rerun — and don't leave the browser "
                "idle between steps, the iView expires in a couple of minutes."
            )


def wait_for_form(page: Page, timeout_ms: int = 60_000):
    """Block until the attendance app has actually finished rendering.

    networkidle on the outer page proves nothing: the app lives in a cross-origin
    iframe that keeps painting well after the shell goes quiet. The three filter
    dropdowns plus SUBMIT are the real 'ready' signal — the date inputs are NOT,
    because they only exist after Monthly/Detailed is set to Detail Report. An
    earlier version waited on the date inputs and hung for 60s on the blank form.
    """
    deadline = time.monotonic() + timeout_ms / 1000

    i = 0
    while time.monotonic() < deadline:
        # The popup is already cleared before we get here; only re-check for a
        # stray one occasionally, since dismiss_popups scans every frame and that
        # overhead per poll noticeably slowed the form-wait.
        if i % 6 == 0:
            dismiss_popups(page)
        i += 1
        # Search EVERY frame for the form, not just the one whose URL matches —
        # frame-URL matching proved unreliable, and the form is defined by its
        # controls (SUBMIT + 3 dropdowns) wherever it lives.
        for frame in page.frames:
            try:
                if (frame.locator(f"div[ct=B]:has-text('{SUBMIT_LABEL}')").count()
                        and frame.locator("input[ct=CB]").count() >= 3):
                    return frame
            except Exception:
                continue  # frame detached/cross-origin mid-poll; skip it
        page.wait_for_timeout(300)

    _dump_form_diag(page)
    raise FormNotReady(
        "The attendance form did not finish rendering within "
        f"{timeout_ms // 1000}s. Wrote form-diagnostic.txt with each frame's controls."
    )


def _dump_form_diag(page: Page) -> None:
    """On a form-wait timeout, record every frame's SUBMIT/dropdown counts."""
    lines = ["wait_for_form timed out. Per-frame control counts:"]
    for frame in page.frames:
        try:
            submit = frame.locator(f"div[ct=B]:has-text('{SUBMIT_LABEL}')").count()
            combos = frame.locator("input[ct=CB]").count()
            lines.append(f"  frame url={frame.url[:90]!r}")
            lines.append(f"      SUBMIT(div[ct=B])={submit}  CB inputs={combos}")
        except Exception as e:
            lines.append(f"  frame url={frame.url[:90]!r}  <read error: {e}>")
    try:
        Path("form-diagnostic.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def dismiss_popups(page: Page) -> int:
    """Close any WebDynpro popup, e.g. the post-login RE-CONFIRMATION dialog.

    The portal lands on the address page behind a confirmation popup that blocks
    all navigation. Its close button id ends in '-close' no matter what the
    generated window id is (WDWL1-close, WDWL2-close, ...), so match on that
    across every frame. Returns how many popups were closed.
    """
    closed = 0
    for frame in page.frames:
        try:
            buttons = frame.locator("button[id$='-close']")
            count = buttons.count()
        except Exception:
            continue  # frame detached mid-scan
        for i in range(count):
            button = buttons.nth(i)
            try:
                if button.is_visible():
                    button.click(timeout=2000)
                    closed += 1
                    page.wait_for_timeout(400)
            except Exception:
                pass  # already gone or not clickable; keep going
    return closed


def clear_login_popup(page: Page, timeout_ms: int = 20_000) -> bool:
    """Wait for the post-login RE-CONFIRMATION popup and close it.

    login_succeeded() returns as soon as the attendance tab appears, but the
    address app and its blocking popup load a few seconds later. Polling for it
    (rather than a fixed pause) is what makes navigation reliable. Returns True
    if a popup was closed.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if dismiss_popups(page):
            page.wait_for_timeout(300)  # let the modal tear down
            return True
        page.wait_for_timeout(300)
    return False


def goto_attendance(page: Page, timeout_ms: int = 40_000) -> None:
    """Drive from the portal desktop to the attendance filter form.

    Retries until the attendance app frame actually loads. Both tabs are clicked
    by their unique visible text, never by the navNodeAnchor ids: those are
    positional (navNodeAnchor_2_0 is 'Personal Data' until the Attendance tab is
    active, then becomes 'Student Attendance'), so an id-based click races.

    No check_alive() here: the portal shell shows stale "iView has timed out"
    bars for iviews we navigated away from even while the attendance app is
    alive, which caused false SessionExpired failures. A genuinely dead session
    is caught by wait_for_form() (no form renders) instead.
    """

    # The popup appears a beat after login and swallows clicks — wait it out.
    clear_login_popup(page)

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        dismiss_popups(page)  # a stray popup would eat these clicks
        try:
            # Top tab first; its sub-tab only appears once it's active.
            page.get_by_role("link", name=NAV_ATTENDANCE_TAB, exact=True).first.click(timeout=5000)
            page.get_by_role("link", name=NAV_STUDENT_ATTENDANCE, exact=True).first.click(timeout=8000)
        except PlaywrightTimeout:
            page.wait_for_timeout(800)
            continue

        # Give the app frame a chance to load before deciding to retry.
        for _ in range(12):
            if attendance_frame(page) is not None:
                return
            page.wait_for_timeout(300)

    # Fell through without the app frame — wait_for_form() will raise a clear
    # FormNotReady with a snapshot.


def attendance_frame(page: Page):
    """The frame running the attendance app, or None if it hasn't loaded."""
    for frame in page.frames:
        if ATTENDANCE_APP in frame.url:
            return frame
    return None


# The three filter dropdowns, in the visual/DOM order they appear. Targeted by
# position among ct=CB inputs rather than id, because the WDxx ids regenerate
# every session. Option text must match the portal exactly.
FILTER_ACADEMIC_YEAR = "Acad .Year 2026-2027"
FILTER_SEMESTER = "Semester V"
FILTER_REPORT_TYPE = "Detail Report"

DATE_FMT = "%d.%m.%Y"  # portal shows 12.06.2026


class FillError(RuntimeError):
    """A filter control could not be driven — the DOM shape wasn't what we expected."""


def select_filter(frame, index: int, option_text: str, timeout_ms: int = 20_000) -> None:
    """Open the index-th dropdown and choose an option by its exact text.

    WebDynpro's ct=CB opens a floating listbox of ct=LIB_I items. The items are
    in the DOM whether the box is open or not, so 'open?' is decided by
    VISIBILITY, not presence — and the combo is clicked ONLY when nothing is
    showing. Re-clicking an open combo toggles it shut, which an earlier version
    did every loop and thrashed the list closed just as it rendered.

    Exact, anchored text match on purpose: 'Semester V' is a substring of
    'Semester VI', so a loose match could pick the wrong semester.
    """
    pattern = re.compile(rf"^\s*{re.escape(option_text)}\s*$")
    item = frame.locator("div[ct=LIB_I]").filter(has_text=pattern).first
    combo = frame.locator("input[ct=CB]").nth(index)

    deadline = time.monotonic() + timeout_ms / 1000
    last_err: Exception | None = None
    last_open = 0.0
    while time.monotonic() < deadline:
        try:
            if item.is_visible():
                item.scroll_into_view_if_needed()
                item.click()
                frame.wait_for_timeout(500)  # let WebDynpro post back
                if not item.is_visible():     # listbox closed => selection took
                    return
                continue
            # Our option isn't showing. Open the box only if none is open AND we
            # haven't just opened it — a mid-render list reports nothing visible,
            # and re-clicking then would toggle it shut (the thrash bug).
            now = time.monotonic()
            box_open = frame.locator("div[ct=LIB_I]:visible").count() > 0
            if not box_open and now - last_open > 1.5:
                combo.click()
                last_open = now
        except Exception as exc:
            last_err = exc
        frame.wait_for_timeout(250)

    _dump_fill_diag(frame, index, option_text, last_err)
    raise FillError(
        f"Could not select '{option_text}' in dropdown #{index + 1} within "
        f"{timeout_ms // 1000}s. Wrote fill-diagnostic.txt with the listbox state."
    )


def _dump_fill_diag(frame, index: int, option_text: str, err) -> None:
    """On a fill failure, record every listbox item and its visibility.

    Turns a blind retry timeout into a precise picture of what the dropdown
    actually contained, so the next fix targets reality instead of a guess.
    """
    lines = [
        f"select_filter FAILED: dropdown #{index + 1}, wanted {option_text!r}",
        f"last exception: {type(err).__name__ if err else None}: {err}",
    ]
    try:
        items = frame.locator("div[ct=LIB_I]")
        n = items.count()
        lines.append(f"ct=LIB_I items in DOM: {n}")
        for i in range(min(n, 60)):
            it = items.nth(i)
            try:
                txt = it.inner_text().strip().replace("\n", " ")
                vis = it.is_visible()
            except Exception as e:
                txt, vis = f"<read error: {e}>", "?"
            lines.append(f"  [{i}] visible={vis}  text={txt!r}")
        lines.append(f"ct=CB combos in DOM: {frame.locator('input[ct=CB]').count()}")
    except Exception as e:
        lines.append(f"diagnostic scan failed: {e}")
    try:
        Path("fill-diagnostic.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def fill_dates(frame, start: str, end: str) -> None:
    """Type the start/end dates. These inputs exist only after Detail Report."""
    date_inputs = frame.locator("input[ct=IN], input[ct=DP], input[ct=I]")
    # Filter to the visible, editable date boxes — the frame has hidden inputs too.
    editable = [
        date_inputs.nth(i)
        for i in range(date_inputs.count())
        if date_inputs.nth(i).is_visible() and date_inputs.nth(i).is_editable()
    ]
    if len(editable) < 2:
        raise FillError(
            f"Expected two date fields after selecting Detail Report, found "
            f"{len(editable)}. Did the report-type selection take effect?"
        )
    for box, value in ((editable[-2], start), (editable[-1], end)):
        box.click()
        box.fill("")
        box.fill(value)


def click_submit(frame) -> None:
    frame.locator(f"div[ct=B]:has-text('{SUBMIT_LABEL}')").first.click()


def attach_recorders(context: BrowserContext, download_dir: Path) -> list[Path]:
    """Capture downloads and popups instead of letting them vanish.

    Playwright's Chromium has no built-in PDF viewer, so a PDF that would render
    in a normal browser arrives here as a download event. With no listener that
    event is dropped and the click looks like it simply did nothing.
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    def on_download(download) -> None:
        # A download can fire while the browser is tearing down; save_as then
        # raises TargetClosedError inside an async callback, which surfaces as an
        # ugly unhandled traceback. Guard it — a late download is nothing to crash on.
        try:
            target = download_dir / (download.suggested_filename or "download.pdf")
            download.save_as(target)
            saved.append(target)
            print(f"\n    [download] {target.name} -> {target}")
        except Exception as exc:
            print(f"\n    [download skipped: {type(exc).__name__}]")

    def on_response(response) -> None:
        # The SAP Adobe form renders the PDF inline via an <object> — it never
        # fires a download event. Catch it by content-type instead, which works
        # regardless of how the page embeds it.
        try:
            ctype = (response.headers or {}).get("content-type", "")
            looks_pdf = "application/pdf" in ctype or response.url.lower().split("?")[0].endswith(".pdf")
            if not looks_pdf:
                return
            body = response.body()
            if not body.startswith(b"%PDF"):
                return  # content-type lied; not really a PDF
            target = download_dir / f"attendance-{len(saved) + 1}.pdf"
            target.write_bytes(body)
            saved.append(target)
            print(f"\n    [pdf] {len(body):,} bytes -> {target.name}")
        except Exception as exc:  # a missed PDF is worth a note, not a crash
            print(f"\n    [pdf capture failed: {type(exc).__name__}]")

    def wire(target_page) -> None:
        target_page.on("download", on_download)
        target_page.on("response", on_response)

    def on_page(new_page) -> None:
        print(f"\n    [popup] {new_page.url or '<blank>'}")
        wire(new_page)

    context.on("page", on_page)
    for page in context.pages:
        wire(page)

    return saved


def format_inspection(frames: list[FrameInfo]) -> str:
    lines: list[str] = []
    for frame in frames:
        lines.append(f"\n{'=' * 70}\nFRAME {frame.path}\n  {frame.url}\n{'=' * 70}")
        lines.extend(f"  {control}" for control in frame.controls)
    return "\n".join(lines)


def save_inspection(frames: list[FrameInfo], path: Path) -> Path:
    """Write the dump to disk. Terminal scrollback is too easy to lose."""
    path.write_text(format_inspection(frames), encoding="utf-8")
    return path.resolve()
