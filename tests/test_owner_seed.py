"""The owner's record follows the semester constants in code across restarts."""

import pytest
from cryptography.fernet import Fernet

from attendance import cli, users


@pytest.fixture
def owner_env(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "STORE_PATH", tmp_path / "users.json")
    monkeypatch.setattr(users, "KEY_PATH", tmp_path / ".secret_key")
    monkeypatch.setenv("ATTENDANCE_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "777")
    monkeypatch.setenv("SAP_USERNAME", "45612240031")
    monkeypatch.setenv("SAP_PASSWORD", "ownerpw")
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)


def test_seed_uses_current_semester(owner_env):
    cli._seed_owner_if_absent("TOK")
    u = users.get_user("777")
    assert u.semester == "Semester VI"
    assert u.start_date == "16.11.2026"
    assert u.academic_year == "Acad .Year 2026-2027"


def test_stale_owner_record_is_brought_up_to_date(owner_env):
    # Simulate the record the VPS already holds from last semester.
    users.put_user(users.User(
        chat_id="777", sap_username="45612240031",
        academic_year="Acad .Year 2026-2027", semester="Semester V",
        start_date="12.06.2026", classes_per_day=6,
        sap_password_enc=users.encrypt_password("ownerpw"),
    ))
    cli._seed_owner_if_absent("TOK")
    u = users.get_user("777")
    assert (u.semester, u.start_date) == ("Semester VI", "16.11.2026")
    assert u.password() == "ownerpw"  # credentials untouched
