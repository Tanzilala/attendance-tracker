"""Multi-user registry: each Telegram chat maps to one student's config.

Passwords are encrypted at rest with Fernet. The key comes from
ATTENDANCE_SECRET_KEY if set, else a locally-generated .secret_key file (chmod
600, gitignored) so the VPS "just works" without a manual key step. Encryption
protects the store against casual leaks/backups/an accidental commit; it is not
a defence against full compromise of the box (the key lives on the same box).

Being custodian of friends' SAP logins is a real responsibility — see DEPLOY.md.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet

STORE_PATH = Path("users.json")
KEY_PATH = Path(".secret_key")
_STORE_LOCK = threading.Lock()

# Portal dropdown text is identical for every SVKM student, so we can build the
# exact option strings from friendly inputs rather than make friends type them.
_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}


def semester_label(number: int) -> str:
    """1 -> 'Semester I'. Matches the portal's Trimester/Semester option text."""
    if number not in _ROMAN:
        raise ValueError(f"semester must be 1-8, got {number}")
    return f"Semester {_ROMAN[number]}"


def academic_year_label(start_year: int) -> str:
    """2026 -> 'Acad .Year 2026-2027' (note the portal's exact odd spacing)."""
    return f"Acad .Year {start_year}-{start_year + 1}"


@dataclass
class User:
    chat_id: str
    sap_username: str
    academic_year: str      # "Acad .Year 2026-2027"
    semester: str           # "Semester V"
    start_date: str         # "12.06.2026"
    classes_per_day: int = 6
    name: str = ""
    sap_password_enc: str = ""  # Fernet token; never the plaintext

    def password(self) -> str:
        return _fernet().decrypt(self.sap_password_enc.encode()).decode()


def _get_key() -> bytes:
    env = (os.environ.get("ATTENDANCE_SECRET_KEY") or "").strip()
    if env:
        return env.encode()
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    # First run with no key configured: make one and lock it down.
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    try:
        KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    except Exception:
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(_get_key())


def encrypt_password(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _load_raw() -> dict[str, dict]:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_raw(data: dict[str, dict]) -> None:
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        STORE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600 — holds credentials
    except Exception:
        pass


def get_user(chat_id: str) -> User | None:
    row = _load_raw().get(str(chat_id))
    return User(**row) if row else None


def find_by_username(sap_username: str) -> User | None:
    """The record for a SAP roll number, whichever front end created it — lets a
    friend who registered on Telegram log in on the website with the same login."""
    wanted = sap_username.strip().lower()
    for row in _load_raw().values():
        if str(row.get("sap_username", "")).strip().lower() == wanted:
            return User(**row)
    return None


def put_user(user: User) -> None:
    # Telegram loop and web threads both write; serialise the read-modify-write
    # so one never overwrites the other's registration.
    with _STORE_LOCK:
        data = _load_raw()
        data[str(user.chat_id)] = asdict(user)
        _save_raw(data)


def all_chat_ids() -> list[str]:
    return list(_load_raw().keys())
