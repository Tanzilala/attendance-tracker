"""Website front end: accounts, throttling, session handling, and the check job
flow — against a real server on an ephemeral port, with the browser mocked."""

import http.client
import json
import threading
import time

import pytest
from cryptography.fernet import Fernet

from attendance import cli, users, web


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "STORE_PATH", tmp_path / "users.json")
    monkeypatch.setattr(users, "KEY_PATH", tmp_path / ".secret_key")
    monkeypatch.setenv("ATTENDANCE_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ATTENDANCE_INVITE_CODE", "friends123")
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "0")  # ephemeral
    monkeypatch.setenv("WEB_COOKIE_SECURE", "0")
    monkeypatch.setattr(web, "_SESSIONS", {})
    monkeypatch.setattr(web, "_JOBS", {})
    for t in (web.LOGIN_THROTTLE, web.IP_THROTTLE, web.REGISTER_THROTTLE):
        monkeypatch.setattr(t, "_hits", {})
    srv = web.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


class Client:
    def __init__(self, server):
        self.port = server.server_address[1]
        self.cookie = None

    def call(self, method, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body)
            hdrs.setdefault("Content-Type", "application/json")
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        for k, v in resp.getheaders():
            if k.lower() == "set-cookie":
                pair = v.split(";", 1)[0]
                self.cookie = pair if not pair.endswith("=") else None
        conn.close()
        if raw:
            return resp.status, payload, resp
        return resp.status, json.loads(payload or b"{}")


REG = {"invite": "friends123", "username": "45612240099", "password": "sappw",
       "semester": 6, "year": 2026, "start_date": "16.11.2026", "classes": 6}


class TestAccounts:
    def test_register_then_me(self, server):
        c = Client(server)
        status, body = c.call("POST", "/api/register", REG)
        assert status == 200 and body["user"]["semester"] == "Semester VI"
        assert "password" not in json.dumps(body)
        status, me = c.call("GET", "/api/me")
        assert status == 200 and me["user"]["username"] == "45612240099"

    def test_password_is_stored_encrypted(self, server):
        Client(server).call("POST", "/api/register", REG)
        assert "sappw" not in users.STORE_PATH.read_text()

    def test_wrong_invite_refused(self, server):
        status, body = Client(server).call("POST", "/api/register", {**REG, "invite": "nope"})
        assert status == 400 and "invite" in body["error"]
        assert users.find_by_username("45612240099") is None

    def test_cannot_reregister_existing_roll_number(self, server):
        Client(server).call("POST", "/api/register", REG)
        status, body = Client(server).call("POST", "/api/register", {**REG, "password": "other"})
        assert status == 400 and "already registered" in body["error"]
        assert users.find_by_username("45612240099").password() == "sappw"

    def test_bad_semester_rejected(self, server):
        status, body = Client(server).call("POST", "/api/register", {**REG, "semester": 9})
        assert status == 400 and "Semester" in body["error"]

    def test_login_logout(self, server):
        Client(server).call("POST", "/api/register", REG)
        c = Client(server)
        assert c.call("POST", "/api/login", {"username": "45612240099", "password": "bad"})[0] == 401
        assert c.call("POST", "/api/login", {"username": "45612240099", "password": "sappw"})[0] == 200
        assert c.call("GET", "/api/me")[0] == 200
        old_cookie = c.cookie
        c.call("POST", "/api/logout", {})
        c.cookie = old_cookie  # the server must have revoked the token itself
        assert c.call("GET", "/api/me")[0] == 401

    def test_telegram_registered_friend_can_log_in(self, server):
        users.put_user(users.User(
            chat_id="5551", sap_username="45612240023", academic_year="Acad .Year 2026-2027",
            semester="Semester V", start_date="12.06.2026",
            sap_password_enc=users.encrypt_password("tgpw")))
        c = Client(server)
        status, body = c.call("POST", "/api/login", {"username": "45612240023", "password": "tgpw"})
        assert status == 200 and body["user"]["semester"] == "Semester V"

    def test_settings_update(self, server):
        c = Client(server)
        c.call("POST", "/api/register", {**REG, "semester": 5})
        status, body = c.call("POST", "/api/settings",
                              {"semester": 6, "year": 2026, "start_date": "16.11.2026"})
        assert status == 200 and body["user"]["semester"] == "Semester VI"
        assert users.find_by_username("45612240099").password() == "sappw"  # untouched

    def test_password_change_rotates_session(self, server):
        c = Client(server)
        c.call("POST", "/api/register", REG)
        old = c.cookie
        c.call("POST", "/api/settings", {"semester": 6, "year": 2026,
                                          "start_date": "16.11.2026", "new_password": "newpw"})
        assert users.find_by_username("45612240099").password() == "newpw"
        stale = Client(server)
        stale.cookie = old
        assert stale.call("GET", "/api/me")[0] == 401


class TestThrottle:
    def test_repeated_wrong_guesses_get_blocked(self, server):
        Client(server).call("POST", "/api/register", REG)
        c = Client(server)
        bad = {"username": "45612240099", "password": "guess"}
        statuses = [c.call("POST", "/api/login", bad)[0] for _ in range(6)]
        assert statuses[:5] == [401] * 5 and statuses[5] == 429
        # even the correct password is refused while blocked from this address
        good = {"username": "45612240099", "password": "sappw"}
        assert c.call("POST", "/api/login", good)[0] == 429

    def test_invite_guessing_is_throttled(self, server):
        c = Client(server)
        statuses = [c.call("POST", "/api/register", {**REG, "invite": f"g{i}"})[0]
                    for i in range(11)]
        assert statuses[-1] == 429


class TestRequestHardening:
    def test_form_posts_refused(self, server):
        status, _ = Client(server).call(
            "POST", "/api/login", body=None,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert status == 415

    def test_cross_origin_refused(self, server):
        status, _ = Client(server).call(
            "POST", "/api/login", {"username": "a", "password": "b"},
            headers={"Origin": "https://evil.example"})
        assert status == 403

    def test_static_page_has_csp_and_no_inline_script(self, server):
        status, payload, resp = Client(server).call("GET", "/", raw=True)
        assert status == 200
        assert "script-src 'self'" in resp.getheader("Content-Security-Policy")
        assert b"<script>" not in payload  # only <script src=...>
        assert resp.getheader("X-Frame-Options") == "DENY"

    def test_cookie_flags(self, server, monkeypatch):
        monkeypatch.setenv("WEB_COOKIE_SECURE", "1")
        _, _, resp = Client(server).call("POST", "/api/register", REG, raw=True)
        cookie = resp.getheader("Set-Cookie")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie


class TestCheckJob:
    @staticmethod
    def fake_check(monkeypatch, outcome="result"):
        """Stand in for the browser: ask for a CAPTCHA, then deliver or fail."""
        monkeypatch.setattr(cli, "_in_window", lambda *a, **k: True)

        def fake(ctx, login_fn, *, on_start=None, on_error=None, **kw):
            job = ctx.sink.__self__
            if on_start:
                on_start()
            job.show_captcha(b"\x89PNG-fake", 1, 3, 30)
            answer = job.wait_answer(5)
            if outcome == "result" and answer == "AbC12":
                ctx.sink("result", "Attendance: 80%")
            elif on_error:
                on_error("CAPTCHA didn't match")
        monkeypatch.setattr(cli, "_run_check", fake)

    def _wait_state(self, c, job_id, state, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            _, view = c.call("GET", f"/api/job/{job_id}")
            if view["state"] == state:
                return view
            time.sleep(0.05)
        raise AssertionError(f"job never reached {state}; last={view}")

    def test_full_flow(self, server, monkeypatch):
        self.fake_check(monkeypatch)
        c = Client(server)
        c.call("POST", "/api/register", REG)
        _, started = c.call("POST", "/api/check", {})
        jid = started["job"]

        view = self._wait_state(c, jid, "captcha")
        assert view["captcha_version"] == 1 and view["seconds_left"] > 0
        status, png, _ = c.call("GET", f"/api/job/{jid}/captcha.png", raw=True)
        assert status == 200 and png == b"\x89PNG-fake"

        assert c.call("POST", f"/api/job/{jid}/captcha", {"answer": "AbC12"})[0] == 200
        done = self._wait_state(c, jid, "done")
        assert done["result"] == "Attendance: 80%"
        assert c.call("GET", f"/api/job/{jid}/captcha.png", raw=True)[0] == 404  # image dropped

    def test_failure_surfaces_as_error(self, server, monkeypatch):
        self.fake_check(monkeypatch, outcome="fail")
        c = Client(server)
        c.call("POST", "/api/register", REG)
        _, started = c.call("POST", "/api/check", {})
        self._wait_state(c, started["job"], "captcha")
        c.call("POST", f"/api/job/{started['job']}/captcha", {"answer": "wrong"})
        err = self._wait_state(c, started["job"], "error")
        assert "didn't match" in err["message"]

    def test_double_click_joins_running_check(self, server, monkeypatch):
        self.fake_check(monkeypatch)
        c = Client(server)
        c.call("POST", "/api/register", REG)
        first = c.call("POST", "/api/check", {})[1]["job"]
        second = c.call("POST", "/api/check", {})[1]["job"]
        assert first == second

    def test_other_users_cannot_see_a_job(self, server, monkeypatch):
        self.fake_check(monkeypatch)
        a, b = Client(server), Client(server)
        a.call("POST", "/api/register", REG)
        b.call("POST", "/api/register", {**REG, "username": "45612240100"})
        jid = a.call("POST", "/api/check", {})[1]["job"]
        self._wait_state(a, jid, "captcha")
        assert b.call("GET", f"/api/job/{jid}")[0] == 404
        assert b.call("GET", f"/api/job/{jid}/captcha.png", raw=True)[0] == 404
        assert b.call("POST", f"/api/job/{jid}/captcha", {"answer": "AbC12"})[0] == 404

    def test_outside_window_is_refused(self, server, monkeypatch):
        monkeypatch.setattr(cli, "_in_window", lambda *a, **k: False)
        c = Client(server)
        c.call("POST", "/api/register", REG)
        status, body = c.call("POST", "/api/check", {})
        assert status == 400 and "6 PM" in body["error"]

    def test_check_requires_login(self, server):
        assert Client(server).call("POST", "/api/check", {})[0] == 401
