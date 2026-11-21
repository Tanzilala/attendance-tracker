"""Multi-user registry: each Telegram chat maps to one student's SAP login.

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
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from cryptography.fernet import Fernet

STORE_PATH = Path("users.json")
KEY_PATH = Path(".secret_key")


@dataclass
class User:
    chat_id: str
    sap_username: str
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
    if not row:
        return None
    # Records saved before semester/dates became shared carry extra keys; ignore them.
    known = {f.name for f in fields(User)}
    return User(**{k: v for k, v in row.items() if k in known})


def put_user(user: User) -> None:
    data = _load_raw()
    data[str(user.chat_id)] = asdict(user)
    _save_raw(data)
