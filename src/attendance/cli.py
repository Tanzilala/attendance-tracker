"""Command line entry point."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from datetime import date, datetime

from attendance import users

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
    start_pdf_interception,
    stop_pdf_interception,
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

SEMESTER_START = "16.11.2026"  # Semester VI classes began 16 November 2026


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


def _deliver(pdf_path: Path, ctx: CheckCtx, *, when: str = "") -> None:
    """Parse a captured PDF, show the short text, and send it to the ctx's chat."""
    try:
        subjects = parse_pdf(pdf_path)
    except ParseError as exc:
        print(f"\n  Could not read the attendance PDF: {exc}")
        if ctx.send and ctx.token and ctx.chat_id:
            try:
                send_message(ctx.token, ctx.chat_id,
                             f"⚠️ Got the report but couldn't read it: {exc}")
            except TelegramError:
                pass
        return

    report = analyse(subjects)
    text = format_text(report, when=when)
    print("\n  Message:\n")
    for line in text.splitlines():
        print(f"    {line}")

    if not (ctx.send and ctx.token and ctx.chat_id):
        return
    try:
        send_message(ctx.token, ctx.chat_id, text)
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


@dataclass
class CheckCtx:
    """One check's login and Telegram chat (where the CAPTCHA goes and the result
    is sent). Semester and start date are the same for everyone, so they are the
    module constants, not per-user."""
    token: str
    chat_id: str
    username: str
    password: str
    send: bool = True


def _owner_ctx(*, send: bool) -> CheckCtx:
    """Context for the owner's own commands, from .env credentials."""
    username, password = _credentials()
    creds = _telegram_creds()
    token, chat_id = creds if creds else ("", "")
    return CheckCtx(token=token, chat_id=chat_id, username=username,
                    password=password, send=send)


def _user_ctx(token: str, user: users.User) -> CheckCtx:
    """Context for a registered friend."""
    return CheckCtx(token=token, chat_id=str(user.chat_id),
                    username=user.sap_username, password=user.password())


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


def _pipeline_after_login(page, downloads, ctx: CheckCtx, capture) -> None:
    """Everything from the portal desktop to delivery, using ctx's filters.

    No per-step debug snapshots on the happy path: each was a full-page
    screenshot plus a whole-DOM scan (seconds apiece) that also widened the gap
    between steps and let the iView time out. Snapshots now happen only on
    failure (in _run_check's handler) or when no PDF comes back.
    """
    end_date = date.today().strftime(DATE_FMT)
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

    # Intercept the PDF at the network layer before Chromium's viewer eats it.
    cdp = start_pdf_interception(page, Path("downloads"), downloads)
    try:
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
    finally:
        stop_pdf_interception(cdp)
    lap("result stage done")

    if downloads:
        print(f"  Done. PDF captured: {downloads[-1].name}", flush=True)
        _deliver(downloads[-1], ctx, when=end_date)
    elif blocked:
        raise WindowClosed(
            "The portal only serves attendance between 6 PM and 7 AM, and "
            "refused the report right now. Try again inside that window."
        )
    else:
        capture("noresult")  # keep evidence when the PDF didn't come back
        print("  Submitted, but no PDF was captured.", flush=True)


def _run_check(ctx: CheckCtx, login_fn, *, headless: bool, pause_at_end: bool = False,
               on_error=None, save_session: bool = False) -> None:
    """Drive one browser session: login (via login_fn) then the shared pipeline.

    login_fn(page) closes over whatever it needs (ctx credentials, or the
    interactive local login).
    """
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
            login_fn(page)
            # Save the authenticated session so `test-fill` can iterate without a
            # fresh CAPTCHA. Only for the owner's own runs — never persist a
            # friend's session cookies to disk.
            if save_session:
                try:
                    context.storage_state(path="auth_state.json")
                except Exception:
                    pass
            _pipeline_after_login(page, downloads, ctx, capture)
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

    ctx = _owner_ctx(send=False)
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
            _pipeline_after_login(page, downloads, ctx, capture)
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
    ctx = _owner_ctx(send=True)
    _run_check(ctx, lambda page: login(page, ctx.username, ctx.password),
               headless=False, pause_at_end=True, save_session=True)


def remote() -> None:
    """Phone flow: the bot sends you the CAPTCHA, you reply, it finishes.

    Lets the check run without you at the machine. You still solve every CAPTCHA
    yourself — it is relayed to Telegram, never solved automatically.
    """
    if not _telegram_creds():
        sys.exit("Telegram not configured. Set TELEGRAM_BOT_TOKEN and "
                 "TELEGRAM_CHAT_ID in .env (see `attendance telegram-setup`).")
    _run_remote_check(_owner_ctx(send=True), save_session=True)


def _run_remote_check(ctx: CheckCtx, *, save_session: bool = False) -> None:
    """Run one phone-relay check for ctx: CAPTCHA to their chat, then the pipeline.

    Shared by `remote` (owner, one-shot) and `listen` (any registered friend).
    Never exits the process — reports outcomes over Telegram so listen keeps going.
    """
    if not _in_window():
        try:
            send_message(ctx.token, ctx.chat_id,
                         "Skipped - outside the 6 PM to 7 AM window. Try again tonight.")
        except TelegramError:
            pass
        print("  Outside the 6 PM - 7 AM window; nothing to do.")
        return

    # Headed by default: this portal's IE-emulation codepath fails to render the
    # WebDynpro app in headless Chromium (the app frame never loads). On a
    # no-display server, run under xvfb (see deploy/).
    headless = (os.environ.get("HEADLESS") or "").strip() in ("1", "true", "yes")

    def notify_error(msg: str) -> None:
        # Messages are self-contained (the CAPTCHA-timeout text already reads as
        # a full sentence); prefix generic failures with a warning sign.
        text = msg if msg.startswith(("⏱️", "⚠️")) else f"⚠️ Couldn't check attendance: {msg}"
        try:
            send_message(ctx.token, ctx.chat_id, text)
        except TelegramError:
            pass

    _run_check(ctx, lambda page: _solve_captcha_by_phone(page, ctx),
               headless=headless, on_error=notify_error, save_session=save_session)


def _parse_message(update: dict) -> tuple[str | None, str]:
    """Return (chat_id, text) for a message update, or (None, "") otherwise."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    cid = chat.get("id")
    if cid is None:
        return None, ""
    return str(cid), (msg.get("text") or "").strip()


_HELP = (
    "📋 Attendance bot\n"
    "/check - check your attendance (works 6 PM - 7 AM)\n"
    "/register - set up, or update your SAP login\n"
    "/help - this message"
)


def _begin_registration(token: str, chat_id: str, reg: dict) -> None:
    """Start (or restart) registration for a chat."""
    need_invite = bool((os.environ.get("ATTENDANCE_INVITE_CODE") or "").strip())
    first = "invite" if need_invite else "username"
    reg[chat_id] = {"step": first, "data": {}}
    prompts = {
        "invite": "Let's set you up. First, the invite code your friend gave you:",
        "username": "Your SAP username (roll number):",
    }
    send_message(token, chat_id, prompts[first])


# (prompt, validator/transform) per step. Validator raises ValueError with a
# user-facing message on bad input; otherwise returns the value to store.
def _validate_step(step: str, text: str) -> object:
    if step == "invite":
        want = (os.environ.get("ATTENDANCE_INVITE_CODE") or "").strip()
        if text != want:
            raise ValueError("That invite code isn't right. Try again:")
        return text
    if step == "username":
        if not text:
            raise ValueError("Please send your SAP username (roll number):")
        return text
    if step == "password":
        if not text:
            raise ValueError("Please send your SAP password:")
        return text
    raise ValueError("Unknown step")


_NEXT_STEP = {"invite": "username", "username": "password"}
_STEP_PROMPT = {
    # "username" is here too: with an invite code, invite -> username is a
    # mid-flow transition, so this prompt is needed (not just the opening one).
    "username": "Your SAP username (roll number):",
    "password": "Your SAP password (stored encrypted; needed to log in for you):",
}


def _advance_registration(token: str, chat_id: str, text: str, reg: dict) -> None:
    """Feed one message into an in-progress registration."""
    state = reg[chat_id]
    step = state["step"]
    try:
        value = _validate_step(step, text)
    except ValueError as exc:
        send_message(token, chat_id, str(exc))  # re-prompt, stay on step
        return

    state["data"][step] = value
    nxt = _NEXT_STEP.get(step)
    if nxt is None:  # was the last step ("password")
        _finish_registration(token, chat_id, reg)
        return
    state["step"] = nxt
    send_message(token, chat_id, _STEP_PROMPT[nxt])


def _finish_registration(token: str, chat_id: str, reg: dict) -> None:
    data = reg.pop(chat_id)["data"]
    user = users.User(
        chat_id=chat_id,
        sap_username=data["username"],
        sap_password_enc=users.encrypt_password(data["password"]),
    )
    users.put_user(user)
    send_message(
        token, chat_id,
        "✅ You're registered!\n"
        f"Roll no: {user.sap_username}\n\n"
        "Send /check any evening (6 PM - 7 AM) to get your attendance.",
    )


def _handle_message(token: str, chat_id: str, text: str, reg: dict) -> bool:
    """Route one message. Returns True if an attendance check ran (so the caller
    resyncs the update offset past any CAPTCHA replies it consumed)."""
    if chat_id in reg:
        _advance_registration(token, chat_id, text, reg)
        return False

    command = (text.lower().lstrip("/").split() or [""])[0]
    if command in ("register", "start", "settings", "reregister", "setup"):
        _begin_registration(token, chat_id, reg)
        return False
    if command in ("check", "attendance"):
        user = users.get_user(chat_id)
        if user is None:
            send_message(token, chat_id,
                         "You're not set up yet. Send /register to get started.")
            return False
        send_message(token, chat_id, "On it - checking your attendance...")
        _run_remote_check(_user_ctx(token, user))
        return True
    send_message(token, chat_id, _HELP)
    return False


def _seed_owner_if_absent(token: str) -> None:
    """Register the owner from .env once, so the owner is just another user and
    needs no manual /register."""
    creds = _telegram_creds()
    if not creds:
        return
    _, owner_chat = creds
    if not owner_chat or users.get_user(owner_chat):
        return
    username = (os.environ.get("SAP_USERNAME") or "").strip()
    password = (os.environ.get("SAP_PASSWORD") or "").strip()
    if not (username and password):
        return
    try:
        users.put_user(users.User(
            chat_id=owner_chat, sap_username=username,
            sap_password_enc=users.encrypt_password(password),
        ))
        print(f"  seeded owner {owner_chat} from .env", flush=True)
    except Exception as exc:
        print(f"  owner seed failed ({exc}); continuing", flush=True)


def listen() -> None:
    """Run continuously; anyone registered can /check. New friends /register.

    Always-on mode for the VPS. Multi-user: each message is routed by the sender's
    chat, and each check uses that person's stored credentials.
    """
    load_dotenv()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env (see `attendance telegram-setup`).")

    _seed_owner_if_absent(token)

    print("Listening ... (Ctrl+C to stop)", flush=True)
    try:
        offset = next_offset(token)  # drain messages sent before we came up
    except Exception as exc:  # a startup blip must not kill the service
        print(f"  startup drain failed ({exc}); continuing", flush=True)
        offset = 0

    reg: dict[str, dict] = {}  # chat_id -> in-progress registration
    while True:
        try:
            updates = get_updates(token, offset, timeout_s=25)
        except Exception as exc:
            # ANY error (dropped long-poll, socket reset, JSON glitch) only
            # pause-and-retries, never crashes the process.
            print(f"  poll error ({type(exc).__name__}: {exc}); retrying in 5s", flush=True)
            time.sleep(5)
            continue

        for update in updates:
            offset = update.get("update_id", offset - 1) + 1
            sender, text = _parse_message(update)
            if sender is None or not text:
                continue
            try:
                ran_check = _handle_message(token, sender, text, reg)
            except Exception as exc:  # one bad message must not kill the bot
                print(f"  handler error for {sender}: {exc}", flush=True)
                ran_check = True  # be safe and resync
            if ran_check:
                # A check consumed CAPTCHA replies; resync so we don't reprocess
                # them. (Cost: a message another user sent *during* a check is
                # skipped and they'd resend — fine at friends scale.)
                try:
                    offset = next_offset(token)
                except Exception:
                    pass


def _solve_captcha_by_phone(page, ctx: CheckCtx,
                            attempts: int = 3, reply_timeout_s: int = 120) -> None:
    """Relay the CAPTCHA to the ctx's chat and apply the reply, retrying on reject."""
    token, chat_id = ctx.token, ctx.chat_id
    mins = reply_timeout_s // 60
    t0 = time.monotonic()
    for attempt in range(1, attempts + 1):
        open_login(page, ctx.username, ctx.password)
        print(f"  [{time.monotonic()-t0:5.1f}s] login page loaded", flush=True)
        image = captcha_image(page)
        print(f"  [{time.monotonic()-t0:5.1f}s] captcha screenshotted", flush=True)

        offset = next_offset(token)  # ignore anything sent before this prompt
        print(f"  [{time.monotonic()-t0:5.1f}s] telegram drained; uploading photo", flush=True)
        caption = (f"Reply with the CAPTCHA text (case-sensitive). {mins} min to reply."
                   if attempt == 1 else
                   f"❌ That CAPTCHA didn't match. Here's a new one - reply again "
                   f"(try {attempt}/{attempts}, case-sensitive).")
        send_photo(token, chat_id, image, caption=caption)
        print(f"  [{time.monotonic()-t0:5.1f}s] CAPTCHA sent (attempt {attempt}/{attempts}); waiting for reply", flush=True)

        answer, offset = wait_for_text(token, chat_id, offset, reply_timeout_s)
        if answer is None:
            # notify_error relays this to Telegram, so make it self-contained.
            raise LoginTimeout(
                f"⏱️ CAPTCHA timed out - no reply in {mins} min. "
                "Send /check when you're ready to try again."
            )

        print(f"  [{time.monotonic()-t0:5.1f}s] got reply; submitting", flush=True)
        submit_captcha(page, answer.strip())
        if login_succeeded(page):
            print(f"  [{time.monotonic()-t0:5.1f}s] logged in", flush=True)
            return
        print(f"  [{time.monotonic()-t0:5.1f}s] CAPTCHA rejected, retrying", flush=True)

    raise LoginTimeout(
        "❌ CAPTCHA didn't match after 3 tries. Send /check to start over "
        "(watch case, and 0 vs O)."
    )


def _last_pdf() -> Path:
    pdf = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("downloads/attendance-1.pdf")
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}  (run `attendance run` first, or pass a path)")
    return pdf


def send_only() -> None:
    """Show and send the short text from the last PDF (owner's chat). No login."""
    _deliver(_last_pdf(), _owner_ctx(send=True), when=date.today().strftime(DATE_FMT))


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
