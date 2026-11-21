import json

import pytest
from cryptography.fernet import Fernet

from attendance import users


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated store + a fixed encryption key for each test."""
    monkeypatch.setattr(users, "STORE_PATH", tmp_path / "users.json")
    monkeypatch.setattr(users, "KEY_PATH", tmp_path / ".secret_key")
    monkeypatch.setenv("ATTENDANCE_SECRET_KEY", Fernet.generate_key().decode())
    return tmp_path


class TestEncryption:
    def test_password_roundtrips(self, store):
        token = users.encrypt_password("hunter2!")
        assert token != "hunter2!"  # actually encrypted
        u = users.User(chat_id="1", sap_username="x", sap_password_enc=token)
        assert u.password() == "hunter2!"

    def test_ciphertext_differs_from_plaintext_in_store(self, store):
        users.put_user(users.User(chat_id="7", sap_username="roll",
                                  sap_password_enc=users.encrypt_password("secretpw")))
        # The raw file must never contain the plaintext password.
        assert "secretpw" not in (store / "users.json").read_text()


class TestStore:
    def test_put_then_get_roundtrips(self, store):
        users.put_user(users.User(chat_id="42", sap_username="45612240031",
                                  sap_password_enc=users.encrypt_password("pw")))
        got = users.get_user("42")
        assert got is not None
        assert got.sap_username == "45612240031"
        assert got.password() == "pw"

    def test_get_unknown_is_none(self, store):
        assert users.get_user("nope") is None

    def test_old_records_with_semester_fields_still_load(self, store):
        """users.json written before semester/dates became shared has extra keys."""
        (store / "users.json").write_text(json.dumps({"5": {
            "chat_id": "5", "sap_username": "45612240023",
            "academic_year": "Acad .Year 2026-2027", "semester": "Semester V",
            "start_date": "12.06.2026", "classes_per_day": 6, "name": "",
            "sap_password_enc": users.encrypt_password("oldpw"),
        }}))
        got = users.get_user("5")
        assert got.sap_username == "45612240023"
        assert got.password() == "oldpw"
