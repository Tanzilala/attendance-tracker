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


class TestLabels:
    def test_semester_label(self):
        assert users.semester_label(5) == "Semester V"
        assert users.semester_label(1) == "Semester I"

    def test_semester_label_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            users.semester_label(0)

    def test_academic_year_label(self):
        assert users.academic_year_label(2026) == "Acad .Year 2026-2027"


class TestEncryption:
    def test_password_roundtrips(self, store):
        token = users.encrypt_password("hunter2!")
        assert token != "hunter2!"  # actually encrypted
        u = users.User(chat_id="1", sap_username="x", academic_year="y",
                       semester="z", start_date="d", sap_password_enc=token)
        assert u.password() == "hunter2!"

    def test_ciphertext_differs_from_plaintext_in_store(self, store):
        u = users.User(chat_id="7", sap_username="roll", academic_year="Acad .Year 2026-2027",
                       semester="Semester V", start_date="12.06.2026",
                       sap_password_enc=users.encrypt_password("secretpw"))
        users.put_user(u)
        # The raw file must never contain the plaintext password.
        assert "secretpw" not in (store / "users.json").read_text()


class TestStore:
    def test_put_then_get_roundtrips(self, store):
        u = users.User(chat_id="42", sap_username="45612240031",
                       academic_year="Acad .Year 2026-2027", semester="Semester V",
                       start_date="12.06.2026", classes_per_day=6,
                       sap_password_enc=users.encrypt_password("pw"))
        users.put_user(u)
        got = users.get_user("42")
        assert got is not None
        assert got.sap_username == "45612240031"
        assert got.semester == "Semester V"
        assert got.password() == "pw"

    def test_get_unknown_is_none(self, store):
        assert users.get_user("nope") is None

    def test_all_chat_ids(self, store):
        users.put_user(users.User(chat_id="1", sap_username="a", academic_year="y",
                                  semester="z", start_date="d",
                                  sap_password_enc=users.encrypt_password("p")))
        users.put_user(users.User(chat_id="2", sap_username="b", academic_year="y",
                                  semester="z", start_date="d",
                                  sap_password_enc=users.encrypt_password("p")))
        assert set(users.all_chat_ids()) == {"1", "2"}
