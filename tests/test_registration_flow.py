"""Drive the registration state machine end-to-end with the network mocked."""

import pytest
from cryptography.fernet import Fernet

from attendance import cli, users


@pytest.fixture
def bot(tmp_path, monkeypatch):
    """Isolated store + key, an invite code, and a captured send_message."""
    monkeypatch.setattr(users, "STORE_PATH", tmp_path / "users.json")
    monkeypatch.setattr(users, "KEY_PATH", tmp_path / ".secret_key")
    monkeypatch.setenv("ATTENDANCE_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ATTENDANCE_INVITE_CODE", "friends123")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "send_message", lambda tok, cid, text: sent.append((cid, text)))
    return sent


def _feed(messages, reg=None):
    reg = reg if reg is not None else {}
    for m in messages:
        cli._handle_message("TOK", "42", m, reg)
    return reg


class TestRegistrationWithInvite:
    def test_full_flow_registers_the_user(self, bot):
        _feed(["/register", "friends123", "45612240031", "mypw"])
        u = users.get_user("42")
        assert u is not None
        assert u.sap_username == "45612240031"
        assert u.password() == "mypw"          # decrypts back
        assert "✅" in bot[-1][1]               # confirmation was sent

    def test_wrong_invite_blocks_and_reprompts(self, bot):
        reg = _feed(["/register", "wrongcode"])
        assert users.get_user("42") is None     # not registered
        assert "42" in reg                        # still mid-registration
        assert "invite code isn't right" in bot[-1][1]

    def test_registration_asks_only_login_questions(self, bot):
        _feed(["/register", "friends123", "45612240031"])
        assert "password" in bot[-1][1].lower()
        assert not any("semester" in t.lower() for _, t in bot)


class TestCheckBeforeRegister:
    def test_check_prompts_to_register(self, bot):
        ran = cli._handle_message("TOK", "99", "/check", {})
        assert ran is False
        assert users.get_user("99") is None
        assert "register" in bot[-1][1].lower()
