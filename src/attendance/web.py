"""Website front end: the same attendance check as the Telegram bot, in a browser.

A visitor registers once (invite code + SAP login + semester), then presses
"Check attendance". The CAPTCHA image is shown on the page, they type it, and
the result appears — the CAPTCHA is always solved by the person, never by code.

Security posture (this site takes SAP passwords, so it is deliberately strict):
  * stdlib only; binds 127.0.0.1 by default and must sit behind an HTTPS proxy.
  * login = roll number + SAP password, checked against the encrypted store;
    wrong guesses are throttled per (IP, roll no) and per IP.
  * server-side, revocable sessions in an HttpOnly + SameSite=Strict cookie
    (Secure unless WEB_COOKIE_SECURE=0 for local http testing).
  * JSON-only POSTs with an Origin check; strict CSP, no inline script or style.
  * a registered roll number can't be re-registered by someone else, and a job's
    CAPTCHA/result are only readable by the session that owns it.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import re
import secrets
import threading
import time
from dataclasses import replace
from datetime import datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from attendance import users
from attendance.browser import (
    DATE_FMT,
    LoginTimeout,
    captcha_image,
    login_succeeded,
    open_login,
    submit_captcha,
)

STATIC_DIR = Path(__file__).parent / "web_static"
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}

SESSION_COOKIE = "att_session"
SESSION_TTL_S = 7 * 24 * 3600
MAX_BODY = 8 * 1024
CAPTCHA_REPLY_S = 120
JOB_KEEP_S = 30 * 60

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


# --------------------------------------------------------------------------- #
# Validation (mirrors the Telegram registration steps, web-worded)
# --------------------------------------------------------------------------- #

class InputError(ValueError):
    """A user-facing validation message."""


def parse_settings(body: dict) -> dict:
    """Validate + normalise semester / academic year / start date / classes."""
    try:
        semester = users.semester_label(int(body.get("semester")))
    except (TypeError, ValueError):
        raise InputError("Semester must be a number from 1 to 8.")
    try:
        year = int(body.get("year"))
        if not 2000 <= year <= 2100:
            raise ValueError
        academic_year = users.academic_year_label(year)
    except (TypeError, ValueError):
        raise InputError("Academic year must be its start year, e.g. 2026 for 2026-2027.")
    start = str(body.get("start_date") or "").strip()
    try:
        datetime.strptime(start, DATE_FMT)
    except ValueError:
        raise InputError("Start date must look like 16.11.2026 (DD.MM.YYYY).")
    raw = body.get("classes")
    try:
        classes = 6 if raw in (None, "") else max(1, int(raw))
    except (TypeError, ValueError):
        raise InputError("Classes per day must be a number.")
    return {"semester": semester, "academic_year": academic_year,
            "start_date": start, "classes_per_day": classes}


def _username(raw) -> str:
    name = str(raw or "").strip()
    if not _USERNAME_RE.match(name):
        raise InputError("Roll number must be 3-40 letters, digits, '.', '_' or '-'.")
    return name


# --------------------------------------------------------------------------- #
# Throttle + sessions
# --------------------------------------------------------------------------- #

class Throttle:
    """Sliding-window failure counter. Throttle the guess, not the owner: the
    login key includes the client IP, so a stranger's wrong guesses can't lock a
    real student out from their own connection."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(key, []) if now - t < self.window_s]
        self._hits[key] = hits
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._live(key, time.monotonic())) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._live(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


LOGIN_THROTTLE = Throttle(limit=5, window_s=15 * 60)      # per (ip, roll no)
IP_THROTTLE = Throttle(limit=30, window_s=15 * 60)        # per ip, all rolls
REGISTER_THROTTLE = Throttle(limit=10, window_s=60 * 60)  # per ip (invite guessing)

_SESSIONS: dict[str, tuple[str, float]] = {}  # token -> (roll no, expiry)
_SESSIONS_LOCK = threading.Lock()


def new_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = (username, time.time() + SESSION_TTL_S)
    return token


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    with _SESSIONS_LOCK:
        entry = _SESSIONS.get(token)
        if not entry:
            return None
        if entry[1] < time.time():
            _SESSIONS.pop(token, None)
            return None
        return entry[0]


def end_session(token: str | None) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token or "", None)


def end_sessions_for(username: str) -> None:
    with _SESSIONS_LOCK:
        for tok in [t for t, (u, _) in _SESSIONS.items() if u == username]:
            _SESSIONS.pop(tok, None)


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #

def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_login(username: str, password: str) -> users.User | None:
    """The stored record if (roll no, SAP password) match; else None. Always does a
    comparison so a missing account and a wrong password take the same time."""
    user = users.find_by_username(username)
    if user is None:
        _same(password, "x" * 12)
        return None
    try:
        stored = user.password()
    except Exception:
        return None
    return user if _same(password, stored) else None


def register(body: dict) -> users.User:
    invite = (os.environ.get("ATTENDANCE_INVITE_CODE") or "").strip()
    if invite and not _same(str(body.get("invite") or "").strip(), invite):
        raise InputError("That invite code isn't right.")
    username = _username(body.get("username"))
    password = str(body.get("password") or "")
    if not password:
        raise InputError("Enter your SAP password.")
    if users.find_by_username(username):
        raise InputError("That roll number is already registered — log in instead.")
    settings = parse_settings(body)
    user = users.User(
        chat_id=f"web:{username}", sap_username=username,
        sap_password_enc=users.encrypt_password(password), **settings,
    )
    users.put_user(user)
    return user


def update_settings(user: users.User, body: dict) -> users.User:
    """Semester / dates (and optionally a new SAP password) for a logged-in user."""
    updated = replace(user, **parse_settings(body))
    new_pw = str(body.get("new_password") or "")
    if new_pw:
        updated = replace(updated, sap_password_enc=users.encrypt_password(new_pw))
    users.put_user(updated)
    return updated


def public_view(user: users.User) -> dict:
    """What the browser may see about an account — never the password."""
    return {
        "username": user.sap_username, "semester": user.semester,
        "academic_year": user.academic_year, "start_date": user.start_date,
        "classes_per_day": user.classes_per_day,
    }


# --------------------------------------------------------------------------- #
# Check jobs
# --------------------------------------------------------------------------- #

class Job:
    """One attendance check, from the web's point of view.

    States: queued -> starting -> captcha (waiting for the person) -> running
    -> done | error. The worker thread drives it; HTTP handlers only read it and
    push the typed CAPTCHA answer in.
    """

    def __init__(self, username: str):
        self.id = secrets.token_urlsafe(12)
        self.username = username
        self.created = time.time()
        self.state = "queued"
        self.message = "Waiting for another check to finish..."
        self.result = ""
        self.captcha: bytes | None = None
        self.captcha_version = 0
        self.attempt = 0
        self.attempts = 3
        self.deadline = 0.0  # epoch seconds the current CAPTCHA expires
        self.answers: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()

    @property
    def finished(self) -> bool:
        return self.state in ("done", "error")

    def set_state(self, state: str, message: str = "") -> None:
        with self._lock:
            self.state, self.message = state, message
            if state != "captcha":
                self.captcha = None

    def show_captcha(self, image: bytes, attempt: int, attempts: int, timeout_s: int) -> None:
        with self._lock:
            while not self.answers.empty():  # drop a stale answer from a prior try
                self.answers.get_nowait()
            self.captcha = image
            self.captcha_version += 1
            self.attempt, self.attempts = attempt, attempts
            self.deadline = time.time() + timeout_s
            self.state = "captcha"
            self.message = ("Type the letters you see (case-sensitive)." if attempt == 1 else
                            "That didn't match - here's a new one. Type it again.")

    def wait_answer(self, timeout_s: float) -> str | None:
        try:
            return self.answers.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def submit_answer(self, answer: str) -> bool:
        with self._lock:
            if self.state != "captcha":
                return False
            self.answers.put(answer)
            self.state, self.message, self.captcha = "running", "Logging in...", None
            return True

    def sink(self, kind: str, text: str) -> None:
        if kind == "result":
            self.result = text
            self.set_state("done", "")
        else:
            self.set_state("error", text)

    def view(self) -> dict:
        with self._lock:
            return {
                "state": self.state, "message": self.message, "result": self.result,
                "captcha_version": self.captcha_version if self.captcha else 0,
                "attempt": self.attempt, "attempts": self.attempts,
                "seconds_left": max(0, int(self.deadline - time.time()))
                if self.state == "captcha" else 0,
            }


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def _prune_jobs() -> None:
    cutoff = time.time() - JOB_KEEP_S
    for jid in [j for j, job in _JOBS.items() if job.finished and job.created < cutoff]:
        _JOBS.pop(jid, None)


def active_job(username: str) -> Job | None:
    with _JOBS_LOCK:
        return next((j for j in _JOBS.values()
                     if j.username == username and not j.finished), None)


def get_job(job_id: str, username: str) -> Job | None:
    """A job, only if it belongs to this user (jobs hold their CAPTCHA + result)."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    return job if job and job.username == username else None


def solve_captcha_by_web(page, ctx, job: Job, attempts: int = 3,
                         reply_timeout_s: int = CAPTCHA_REPLY_S) -> None:
    """Show the CAPTCHA on the page and apply the typed answer, retrying on reject."""
    mins = reply_timeout_s // 60
    for attempt in range(1, attempts + 1):
        open_login(page, ctx.username, ctx.password)
        job.show_captcha(captcha_image(page), attempt, attempts, reply_timeout_s)
        answer = job.wait_answer(reply_timeout_s)
        if answer is None:
            raise LoginTimeout(
                f"CAPTCHA timed out - no answer in {mins} min. Start a new check when ready.")
        submit_captcha(page, answer.strip())
        if login_succeeded(page):
            job.set_state("running", "Logged in - fetching your attendance...")
            return
    raise LoginTimeout("The CAPTCHA didn't match after 3 tries. Start a new check "
                       "(watch letter case, and 0 vs O).")


def _run_job(job: Job, user: users.User) -> None:
    from attendance import cli  # late: cli starts this module, not the reverse

    ctx = replace(cli._user_ctx("", user), sink=job.sink)
    headless = (os.environ.get("HEADLESS") or "").strip() in ("1", "true", "yes")

    def on_error(msg: str) -> None:
        job.set_state("error", msg.replace("⏱️ ", "").replace("❌ ", ""))

    try:
        cli._run_check(
            ctx, lambda page: solve_captcha_by_web(page, ctx, job),
            on_start=lambda: job.set_state("starting", "Opening the portal..."),
            headless=headless, on_error=on_error,
        )
    except Exception as exc:  # the worker must always leave the job in a final state
        job.set_state("error", f"Unexpected error: {exc}")
    if not job.finished:
        job.set_state("error", "No attendance report came back. Please try again.")


def start_check(user: users.User) -> Job:
    from attendance import cli

    if not cli._in_window():
        raise InputError("The portal only serves attendance between 6 PM and 7 AM. "
                         "Try again this evening.")
    existing = active_job(user.sap_username)
    if existing:
        return existing  # a double click or a second tab joins the running check
    job = Job(user.sap_username)
    with _JOBS_LOCK:
        _prune_jobs()
        _JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job, user), daemon=True,
                     name=f"check-{user.sap_username}").start()
    return job


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _cookie_secure() -> bool:
    return (os.environ.get("WEB_COOKIE_SECURE") or "1").strip() not in ("0", "false", "no")


class Handler(BaseHTTPRequestHandler):
    server_version = "attendance"
    sys_version = ""
    # HTTP/1.0 (the default): one request per connection, so an early error reply
    # can never leave an unread body to corrupt a kept-alive connection.

    # -- plumbing ----------------------------------------------------------- #

    def log_message(self, fmt, *args):  # request lines can carry job ids; keep quiet
        pass

    def _client_ip(self) -> str:
        if (os.environ.get("WEB_TRUST_PROXY") or "").strip() in ("1", "true", "yes"):
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:  # rightmost = the hop our own proxy appended; earlier is spoofable
                return fwd.split(",")[-1].strip()
        return self.client_address[0]

    def _security_headers(self) -> None:
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "img-src 'self'; connect-src 'self'; form-action 'self'; "
                         "base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, body: bytes, ctype: str, extra: list[tuple[str, str]] = ()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict, extra: list[tuple[str, str]] = ()):
        self._send(status, json.dumps(payload).encode(), "application/json", extra)

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _token(self) -> str | None:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _current_user(self) -> users.User | None:
        name = session_user(self._token())
        return users.find_by_username(name) if name else None

    def _session_cookie(self, token: str, max_age: int) -> tuple[str, str]:
        parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly",
                 "SameSite=Strict", f"Max-Age={max_age}"]
        if _cookie_secure():
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def _body(self) -> dict | None:
        """The JSON body, or None after having already replied with an error."""
        if not self._origin_ok():
            self._error(403, "Cross-site request refused.")
            return None
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._error(415, "Expected JSON.")
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._error(413, "Request too large.")
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._error(400, "Invalid JSON.")
            return None
        if not isinstance(data, dict):
            self._error(400, "Invalid JSON.")
            return None
        return data

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # same-origin fetches from older browsers omit it
        return urlparse(origin).netloc == self.headers.get("Host", "")

    # -- routes ------------------------------------------------------------- #

    def do_GET(self):
        path = urlparse(self.path).path
        if path in _STATIC_FILES:
            name, ctype = _STATIC_FILES[path]
            try:
                self._send(200, (STATIC_DIR / name).read_bytes(), ctype)
            except OSError:
                self._error(404, "Not found.")
            return
        if path == "/api/me":
            user = self._current_user()
            if not user:
                return self._error(401, "Not signed in.")
            job = active_job(user.sap_username)
            return self._json(200, {"user": public_view(user),
                                    "job": job.id if job else None})
        m = re.fullmatch(r"/api/job/([\w-]+)(/captcha\.png)?", path)
        if m:
            return self._job_get(m.group(1), bool(m.group(2)))
        self._error(404, "Not found.")

    def _job_get(self, job_id: str, want_image: bool):
        user = self._current_user()
        if not user:
            return self._error(401, "Not signed in.")
        job = get_job(job_id, user.sap_username)
        if not job:
            return self._error(404, "No such check.")
        if not want_image:
            return self._json(200, job.view())
        image = job.captcha
        if image is None:
            return self._error(404, "No CAPTCHA right now.")
        self._send(200, image, "image/png")

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        if body is None:
            return
        handlers = {
            "/api/register": self._post_register,
            "/api/login": self._post_login,
            "/api/logout": self._post_logout,
            "/api/settings": self._post_settings,
            "/api/check": self._post_check,
        }
        if path in handlers:
            return handlers[path](body)
        m = re.fullmatch(r"/api/job/([\w-]+)/captcha", path)
        if m:
            return self._post_captcha(m.group(1), body)
        self._error(404, "Not found.")

    def _post_register(self, body: dict):
        ip = self._client_ip()
        if REGISTER_THROTTLE.blocked(ip):
            return self._error(429, "Too many attempts. Try again later.")
        try:
            user = register(body)
        except InputError as exc:
            REGISTER_THROTTLE.fail(ip)
            return self._error(400, str(exc))
        token = new_session(user.sap_username)
        self._json(200, {"user": public_view(user)},
                   [self._session_cookie(token, SESSION_TTL_S)])

    def _post_login(self, body: dict):
        ip = self._client_ip()
        username = str(body.get("username") or "").strip().lower()
        password = str(body.get("password") or "")
        pair = f"{ip}|{username}"
        if LOGIN_THROTTLE.blocked(pair) or IP_THROTTLE.blocked(ip):
            return self._error(429, "Too many attempts. Wait a few minutes and try again.")
        user = verify_login(username, password)
        if not user:
            LOGIN_THROTTLE.fail(pair)
            IP_THROTTLE.fail(ip)
            return self._error(401, "Wrong roll number or password.")
        LOGIN_THROTTLE.clear(pair)
        token = new_session(user.sap_username)
        self._json(200, {"user": public_view(user)},
                   [self._session_cookie(token, SESSION_TTL_S)])

    def _post_logout(self, body: dict):
        end_session(self._token())
        self._json(200, {"ok": True}, [self._session_cookie("", 0)])

    def _post_settings(self, body: dict):
        user = self._current_user()
        if not user:
            return self._error(401, "Not signed in.")
        try:
            updated = update_settings(user, body)
        except InputError as exc:
            return self._error(400, str(exc))
        if body.get("new_password"):  # old sessions shouldn't outlive a credential change
            end_sessions_for(user.sap_username)
            token = new_session(user.sap_username)
            return self._json(200, {"user": public_view(updated)},
                              [self._session_cookie(token, SESSION_TTL_S)])
        self._json(200, {"user": public_view(updated)})

    def _post_check(self, body: dict):
        user = self._current_user()
        if not user:
            return self._error(401, "Not signed in.")
        try:
            job = start_check(user)
        except InputError as exc:
            return self._error(400, str(exc))
        self._json(200, {"job": job.id})

    def _post_captcha(self, job_id: str, body: dict):
        user = self._current_user()
        if not user:
            return self._error(401, "Not signed in.")
        job = get_job(job_id, user.sap_username)
        if not job:
            return self._error(404, "No such check.")
        answer = str(body.get("answer") or "").strip()
        if not answer or len(answer) > 20:
            return self._error(400, "Type the letters shown in the picture.")
        if not job.submit_answer(answer):
            return self._error(409, "That check isn't waiting for a CAPTCHA.")
        self._json(200, {"ok": True})


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #

def make_server() -> ThreadingHTTPServer:
    host = (os.environ.get("WEB_HOST") or "127.0.0.1").strip()
    port = int((os.environ.get("WEB_PORT") or "8080").strip())
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve_in_background() -> threading.Thread:
    server = make_server()
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="web")
    thread.start()
    print(f"  website on http://{host}:{port} (put HTTPS in front of it)", flush=True)
    return thread


def serve_forever() -> None:
    server = make_server()
    host, port = server.server_address[:2]
    print(f"Website on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    if _cookie_secure() and host in ("127.0.0.1", "localhost"):
        print("  (cookies are Secure; browsers accept that on localhost. "
              "Set WEB_COOKIE_SECURE=0 only if login doesn't stick.)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
