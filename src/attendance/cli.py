"""Command line entry point."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from datetime import date, datetime

from attendance.browser import (
    DATE_FMT,
    FILTER_ACADEMIC_YEAR,
    FILTER_REPORT_TYPE,
    FILTER_SEMESTER,
    NAV_ATTENDANCE_TAB,
    PORTAL_URL,
    FillError,
    FormNotReady,
    LoginTimeout,
    SessionExpired,
    WindowClosed,
    attach_recorders,
    attendance_frame,
    captcha_image,
    check_alive,
    click_submit,
    fill_dates,
    goto_attendance,
    inspect,
    login,
    login_succeeded,
    open_login,
    save_inspection,
    select_filter,
    submit_captcha,
    wait_for_form,
    window_blocked,
)
from playwright.sync_api import TimeoutError as PlaywrightTimeout

import webbrowser

from attendance.analysis import analyse
from attendance.dashboard import write_dashboard
from attendance.parser import ParseError, parse_pdf
from attendance.report import format_report, format_text
from attendance.telegram import (
    TelegramError,
    get_updates,
    next_offset,
    recent_chats,
    send_message,
    send_photo,
    wait_for_text,
)

SEMESTER_START = "12.06.2026"  # classes began 12 June 2026


def _in_window(now: datetime | None = None) -> bool:
    """True if the local time is inside the portal's 6 PM - 7 AM window."""
    hour = (now or datetime.now()).hour
    return hour >= 18 or hour < 7


def _classes_per_day() -> int:
    load_dotenv()
    raw = (os.environ.get("CLASSES_PER_DAY") or "6").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 6


def _telegram_creds() -> tuple[str, str] | None:
    load_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    return (token, chat) if token and chat else None


def _deliver(pdf_path: Path, *, when: str = "", send: bool = True) -> None:
    """Parse a captured PDF, show the short text, and send it to Telegram."""
    try:
        subjects = parse_pdf(pdf_path)
    except ParseError as exc:
        print(f"\n  Could not read the attendance PDF: {exc}")
        return

    report = analyse(subjects)
    text = format_text(report, when=when)
    print("\n  Message:\n")
    for line in text.splitlines():
        print(f"    {line}")

    if not send:
        return

    creds = _telegram_creds()
    if not creds:
        print("\n  (Telegram not configured — set TELEGRAM_BOT_TOKEN and "
              "TELEGRAM_CHAT_ID in .env to auto-send. See `attendance telegram-setup`.)")
        return
    try:
        send_message(*creds, text)
        print("\n  Sent to Telegram.")
    except TelegramError as exc:
        print(f"\n  Telegram send failed: {exc}")


def _student_name(pdf_path: Path) -> str:
    """Best-effort student name from the PDF header, for the dashboard title."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for line in (pdf.pages[0].extract_text() or "").splitlines():
                if line.startswith("Student Name"):
                    return line.replace("Student Name", "").strip()
    except Exception:
        pass
    return ""


def _credentials() -> tuple[str, str]:
    load_dotenv()
    # Stray trailing whitespace in .env is invisible and would be sent verbatim.
    username = (os.environ.get("SAP_USERNAME") or "").strip()
    password = (os.environ.get("SAP_PASSWORD") or "").strip()
    if not username or not password:
        sys.exit("SAP_USERNAME and SAP_PASSWORD must be set in .env (see .env.example).")
    return username, password


def discover() -> None:
    """Log in, then dump the structure of whatever page you navigate to.

    Interactive on purpose: you drive to the attendance screen, we read it.
    """
    username, password = _credentials()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        downloads = attach_recorders(context, Path("downloads"))
        page = context.new_page()

        try:
            login(page, username, password)
        except LoginTimeout as exc:
            sys.exit(str(exc))

        snapshot = 0

        def capture(tag: str) -> None:
            nonlocal snapshot
            snapshot += 1
            stem = f"discovery-{snapshot}-{tag}"
            save_inspection(inspect(page), Path(f"{stem}.txt"))
            page.screenshot(path=f"{stem}.png", full_page=True)
            print(f"    saved {stem}.txt + {stem}.png")

        try:
            print("  Navigating to the attendance form...")
            goto_attendance(page)
            print("  Waiting for the app to finish rendering...")
            wait_for_form(page)
            print("  Form ready.")
            capture("form")
        except (SessionExpired, FormNotReady) as exc:
            capture("failed")  # capture the failure too — that's the useful bit
            browser.close()
            sys.exit(f"\n  {exc}")

        print("\n  Set your filters and click SUBMIT. Work quickly — the iView")
        print("  expires after a short idle and the portal then serves stale content.\n")

        while True:
            answer = input("  Enter = capture, q = quit: ").strip().lower()
            if answer == "q":
                break
            try:
                check_alive(page)
            except SessionExpired as exc:
                print(f"    {exc}")
                break
            capture("result")

        browser.close()
        if snapshot:
            print(f"\n  {snapshot} snapshot(s) written. Send me the .txt files.")
        if downloads:
            print(f"  {len(downloads)} file(s) downloaded to downloads/:")
            for path in downloads:
                print(f"    {path.name}")
        else:
            print("  No downloads captured.")


def _pipeline_after_login(page, downloads, end_date, capture, *, send: bool) -> None:
    """Everything from the portal desktop to delivery. Shared by run and remote.

    No per-step debug snapshots on the happy path: each was a full-page
    screenshot plus a whole-DOM scan (seconds apiece) that also widened the gap
    between steps and let the iView time out. Snapshots now happen only on
    failure (in _run_session's handler) or when no PDF comes back.
    """
    t0 = time.monotonic()

    def lap(label: str) -> None:
        print(f"  [{time.monotonic() - t0:5.1f}s] {label}", flush=True)

    lap("navigating to form")
    goto_attendance(page)
    frame = wait_for_form(page)  # the exact frame the form was found in
    lap("form ready; filling filters")

    select_filter(frame, 0, FILTER_ACADEMIC_YEAR)
    select_filter(frame, 1, FILTER_SEMESTER)
    select_filter(frame, 2, FILTER_REPORT_TYPE)
    lap("dropdowns set; filling dates")

    fill_dates(frame, SEMESTER_START, end_date)
    lap("submitting")
    click_submit(frame)

    # The Adobe form streams the PDF a beat after submit; poll for it,
    # but bail early if the portal shows the out-of-window message.
    blocked = False
    for _ in range(30):  # ~15s
        page.wait_for_timeout(500)
        if downloads:
            break
        if window_blocked(page):
            blocked = True
            break
    lap("result stage done")

    if downloads:
        print(f"  Done. PDF captured: {downloads[-1].name}", flush=True)
        _deliver(downloads[-1], when=end_date, send=send)
    elif blocked:
        raise WindowClosed(
            "The portal only serves attendance between 6 PM and 7 AM, and "
            "refused the report right now. Try again inside that window."
        )
    else:
        capture("noresult")  # keep evidence when the PDF didn't come back
        print("  Submitted, but no PDF was captured.", flush=True)


def _run_session(login_fn, *, headless: bool, pause_at_end: bool,
                 on_error=None) -> None:
    """Drive one browser session: login (via login_fn) then the shared pipeline."""
    username, password = _credentials()
    end_date = date.today().strftime(DATE_FMT)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        downloads = attach_recorders(context, Path("downloads"))
        page = context.new_page()

        step = 0

        def capture(tag: str) -> None:
            nonlocal step
            step += 1
            stem = f"run-{step}-{tag}"
            save_inspection(inspect(page), Path(f"{stem}.txt"))
            page.screenshot(path=f"{stem}.png", full_page=True)
            print(f"    snapshot: {stem}")

        try:
            login_fn(page, username, password)
            # Save the authenticated session so `test-fill` can iterate on the
            # form without re-solving a CAPTCHA (works while the session lives).
            try:
                context.storage_state(path="auth_state.json")
            except Exception:
                pass
            _pipeline_after_login(page, downloads, end_date, capture, send=True)
        except (LoginTimeout, SessionExpired, FormNotReady, FillError, WindowClosed) as exc:
            capture("failed")
            print(f"\n  Stopped: {exc}")
            if on_error:
                on_error(str(exc))
        except Exception as exc:  # unknown shape — still grab evidence
            capture("error")
            print(f"\n  Unexpected error ({type(exc).__name__}): {exc}")
            if on_error:
                on_error(f"Unexpected error: {exc}")

        if pause_at_end:
            input("\n  Press Enter to close the browser... ")
        browser.close()


def test_fill() -> None:
    """Reuse the saved login session and run the form fill only. No CAPTCHA.

    A debugging aid: solve the CAPTCHA once with `remote`, then iterate on the
    dropdown/date/submit path here as often as needed while the SAP session
    lives. Prints the result instead of sending it to Telegram.
    """
    if not Path("auth_state.json").exists():
        sys.exit("No saved session. Run `attendance remote` once to log in first.")

    end_date = date.today().strftime(DATE_FMT)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True, storage_state="auth_state.json")
        downloads = attach_recorders(context, Path("downloads"))
        page = context.new_page()

        step = 0

        def capture(tag: str) -> None:
            nonlocal step
            step += 1
            stem = f"run-{step}-{tag}"
            save_inspection(inspect(page), Path(f"{stem}.txt"))
            page.screenshot(path=f"{stem}.png", full_page=True)
            print(f"    snapshot: {stem}")

        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(f"text={NAV_ATTENDANCE_TAB}", timeout=15_000)
            except PlaywrightTimeout:
                sys.exit("Saved session expired. Run `attendance remote` to log in again.")
            _pipeline_after_login(page, downloads, end_date, capture, send=False)
        except (SessionExpired, FormNotReady, FillError, WindowClosed) as exc:
            capture("failed")
            print(f"\n  Stopped: {exc}")
        except Exception as exc:
            capture("error")
            print(f"\n  Unexpected error ({type(exc).__name__}): {exc}")

        input("\n  Press Enter to close the browser... ")
        browser.close()


def _confirm_window() -> bool:
    """Warn (and ask) if we're outside the portal's viewing window."""
    if _in_window():
        return True
    print("  Heads up: it's outside the portal's 6 PM - 7 AM window, so it")
    print("  will likely refuse the report. You can still try.")
    return input("  Continue anyway? [y/N] ").strip().lower() in ("y", "yes")


def run() -> None:
    """Local flow: you solve the CAPTCHA in the browser window on this machine."""
    if not _confirm_window():
        print("  Stopped.")
        return
    _run_session(login, headless=False, pause_at_end=True)


def remote() -> None:
    """Phone flow: the bot sends you the CAPTCHA, you reply, it finishes.

    Lets the check run without you at the machine. You still solve every CAPTCHA
    yourself — it is relayed to Telegram, never solved automatically.
    """
    creds = _telegram_creds()
    if not creds:
        sys.exit("Telegram not configured. Set TELEGRAM_BOT_TOKEN and "
                 "TELEGRAM_CHAT_ID in .env (see `attendance telegram-setup`).")
    _run_remote_check(*creds)


def _run_remote_check(token: str, chat_id: str) -> None:
    """Run one phone-relay check: CAPTCHA to Telegram, then the full pipeline.

    Shared by `remote` (one-shot) and `listen` (on /check). Never exits the
    process — reports outcomes over Telegram so the listener keeps running.
    """
    if not _in_window():
        try:
            send_message(token, chat_id,
                         "Skipped - outside the 6 PM to 7 AM window. Try again tonight.")
        except TelegramError:
            pass
        print("  Outside the 6 PM - 7 AM window; nothing to do.")
        return

    # Headed by default: this portal's IE-emulation codepath fails to render the
    # WebDynpro app in headless Chromium (the app frame never loads). You don't
    # interact with the window — the CAPTCHA is solved via Telegram — it just has
    # to be compositing. On a no-display server, run under xvfb (see deploy/).
    headless = (os.environ.get("HEADLESS") or "").strip() in ("1", "true", "yes")

    def telegram_login(page, username, password) -> None:
        _solve_captcha_by_phone(page, username, password, token, chat_id)

    def notify_error(msg: str) -> None:
        # Messages are self-contained (the CAPTCHA-timeout text already reads as
        # a full sentence); prefix generic failures with a warning sign.
        text = msg if msg.startswith(("⏱️", "⚠️")) else f"⚠️ Couldn't check attendance: {msg}"
        try:
            send_message(token, chat_id, text)
        except TelegramError:
            pass

    _run_session(telegram_login, headless=headless, pause_at_end=False,
                 on_error=notify_error)


def _command_from_update(update: dict, chat_id: str) -> str | None:
    """Extract a normalized command from an update, or None.

    Returns None for messages from any chat other than the configured one — the
    security gate that stops anyone else who finds the bot from triggering it.
    """
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if str(chat.get("id")) != str(chat_id):
        return None
    text = (msg.get("text") or "").strip().lower().lstrip("/")
    return text or None


def listen() -> None:
    """Run continuously; on a /check message from you, run one attendance check.

    This is the always-on mode meant for the VPS: message the bot instead of
    running a command. Only messages from TELEGRAM_CHAT_ID are honoured.
    """
    creds = _telegram_creds()
    if not creds:
        sys.exit("Telegram not configured. Set TELEGRAM_BOT_TOKEN and "
                 "TELEGRAM_CHAT_ID in .env (see `attendance telegram-setup`).")
    token, chat_id = creds

    # Drain stale messages FIRST, then announce. If we announced first and
    # drained after, a /check sent in that gap would be swept up and ignored.
    offset = next_offset(token)
    try:
        send_message(token, chat_id,
                     "Attendance bot online. Send /check to check your attendance "
                     "(portal only works 6 PM - 7 AM).")
    except TelegramError as exc:
        sys.exit(f"Could not reach Telegram: {exc}")

    print("Listening for /check ... (Ctrl+C to stop)")
    while True:
        try:
            updates = get_updates(token, offset, timeout_s=25)
        except TelegramError as exc:
            print(f"  poll error: {exc}; retrying in 5s")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            command = _command_from_update(update, chat_id)
            if command in ("check", "attendance"):
                try:
                    send_message(token, chat_id, "On it - checking your attendance...")
                    _run_remote_check(token, chat_id)
                except Exception as exc:  # keep the listener alive no matter what
                    print(f"  check error: {exc}")
                    try:
                        send_message(token, chat_id, f"Check crashed: {exc}")
                    except TelegramError:
                        pass
                # Skip anything sent during the check (e.g. the CAPTCHA reply) so
                # a queued message can't re-trigger or be misread as a command.
                offset = next_offset(token)
            elif command in ("start", "help"):
                try:
                    send_message(token, chat_id,
                                 "Send /check to check your attendance. It works only "
                                 "during the portal's 6 PM - 7 AM window.")
                except TelegramError:
                    pass


def _solve_captcha_by_phone(page, username, password, token, chat_id,
                            attempts: int = 3, reply_timeout_s: int = 120) -> None:
    """Relay the CAPTCHA to Telegram and apply the reply, retrying on rejection."""
    mins = reply_timeout_s // 60
    for attempt in range(1, attempts + 1):
        open_login(page, username, password)
        image = captcha_image(page)

        offset = next_offset(token)  # ignore anything sent before this prompt
        caption = (f"Reply with the CAPTCHA text (case-sensitive). {mins} min to reply."
                   if attempt == 1 else
                   f"That didn't work. New CAPTCHA - reply again (try {attempt}/{attempts}).")
        send_photo(token, chat_id, image, caption=caption)
        print(f"  CAPTCHA sent to Telegram (attempt {attempt}/{attempts}). Waiting for reply...")

        answer, offset = wait_for_text(token, chat_id, offset, reply_timeout_s)
        if answer is None:
            # notify_error relays this to Telegram, so make it self-contained.
            raise LoginTimeout(
                f"⏱️ CAPTCHA timed out - no reply in {mins} min. "
                "Send /check when you're ready to try again."
            )

        submit_captcha(page, answer.strip())
        if login_succeeded(page):
            print("  Logged in.\n")
            return

    raise LoginTimeout(f"CAPTCHA rejected {attempts} times; giving up.")


def _last_pdf() -> Path:
    pdf = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("downloads/attendance-1.pdf")
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}  (run `attendance run` first, or pass a path)")
    return pdf


def send_only() -> None:
    """Show and send the short text from the last PDF. No login."""
    _deliver(_last_pdf(), when=date.today().strftime(DATE_FMT), send=True)


def report_only() -> None:
    """Print the full terminal report from the last PDF. No login, no send."""
    try:
        report = analyse(parse_pdf(_last_pdf()))
    except ParseError as exc:
        sys.exit(f"Could not read the attendance PDF: {exc}")
    print(format_report(report, classes_per_day=_classes_per_day()))


def dashboard_only() -> None:
    """Build and open the HTML dashboard from the last PDF. No login."""
    pdf = _last_pdf()
    report = analyse(parse_pdf(pdf))
    out = write_dashboard(
        report, Path("dashboard.html"),
        student=_student_name(pdf),
        generated=date.today().strftime(DATE_FMT),
        classes_per_day=_classes_per_day(),
    )
    print(f"Dashboard: {out}")
    webbrowser.open(out.as_uri())


def telegram_setup() -> None:
    """List chat ids that have messaged the bot, for one-time .env setup."""
    load_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env first (get one from @BotFather).")
    try:
        chats = recent_chats(token)
    except TelegramError as exc:
        sys.exit(str(exc))
    if not chats:
        sys.exit("No chats found. Message your bot once, then rerun this.")
    print("Recent chats (put the id in TELEGRAM_CHAT_ID):")
    for chat_id, label in chats:
        print(f"  {chat_id}   {label}")


def main() -> None:
    commands = {
        "run": run,                      # local: solve CAPTCHA at the machine
        "remote": remote,                # phone: bot relays the CAPTCHA to you (one-shot)
        "listen": listen,                # always-on: message /check to trigger
        "test-fill": test_fill,          # reuse saved session, form fill only
        "send": send_only,               # resend text from last PDF
        "report": report_only,           # full terminal report
        "dashboard": dashboard_only,     # build + open HTML dashboard
        "telegram-setup": telegram_setup,
        "discover": discover,            # dev: dump portal structure
    }
    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: attendance <{'|'.join(commands)}> [pdf-path]")
    commands[sys.argv[1]]()
