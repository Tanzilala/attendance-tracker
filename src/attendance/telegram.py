"""Send the attendance text via a Telegram bot.

Uses the stdlib only (no extra dependency). The bot token is a credential: it is
read from the environment, sent only to api.telegram.org, and never logged.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    """The Telegram API rejected the request or couldn't be reached."""


def _open(req: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Telegram returns a JSON description even on error — surface it, but
        # never include the URL (it carries the token).
        try:
            detail = json.loads(exc.read()).get("description", str(exc.code))
        except Exception:
            detail = str(exc.code)
        raise TelegramError(f"Telegram API error: {detail}") from None
    except Exception as exc:
        # URLError, socket resets, connection drops mid-read, JSON glitches —
        # wrap them ALL as TelegramError so no raw exception ever escapes and
        # crashes the long-running listener.
        raise TelegramError(f"Could not reach Telegram: {exc}") from None

    if not body.get("ok"):
        raise TelegramError(f"Telegram rejected the request: {body.get('description')}")
    return body["result"]


def _call(token: str, method: str, payload: dict, timeout: int = 20) -> dict:
    url = API.format(token=token, method=method)
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    return _open(req, timeout)


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----attendance" + os.urandom(8).hex()
    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for name, (filename, content, ctype) in files.items():
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), boundary


def send_message(token: str, chat_id: str, text: str) -> None:
    """Post ``text`` to ``chat_id``. Raises TelegramError on any failure."""
    _call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_photo(token: str, chat_id: str, image: bytes, caption: str = "") -> None:
    """Upload an image (e.g. the CAPTCHA) to the chat."""
    body, boundary = _multipart(
        {"chat_id": chat_id, "caption": caption},
        {"photo": ("captcha.png", image, "image/png")},
    )
    url = API.format(token=token, method="sendPhoto")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    _open(req, timeout=30)


def get_updates(token: str, offset: int, timeout_s: int = 25) -> list[dict]:
    """Long-poll for updates from ``offset``. Blocks up to timeout_s server-side."""
    return _call(token, "getUpdates", {"offset": offset, "timeout": timeout_s},
                 timeout=timeout_s + 10)


def next_offset(token: str) -> int:
    """Drain pending updates and return the next offset.

    Called before prompting so we only ever read the *reply* to that prompt,
    never a stale message the user sent earlier.
    """
    updates = _call(token, "getUpdates", {})
    return (max(u["update_id"] for u in updates) + 1) if updates else 0


def wait_for_text(token: str, chat_id: str, offset: int, timeout_s: int,
                  poll_s: int = 25) -> tuple[str | None, int]:
    """Long-poll for the next text message from ``chat_id``.

    Returns (text, new_offset). text is None if nothing arrived before the
    deadline. Uses Telegram's server-side long-poll so it waits efficiently.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        updates = _call(token, "getUpdates",
                        {"offset": offset, "timeout": poll_s}, timeout=poll_s + 10)
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id")) == str(chat_id) and msg.get("text"):
                return msg["text"], offset
    return None, offset


def recent_chats(token: str) -> list[tuple[str, str]]:
    """Return (chat_id, label) pairs from recent updates, for one-time setup.

    Telegram won't tell a bot its chat id until you message the bot first, so
    this reads getUpdates and lists whoever has messaged it recently.
    """
    updates = _call(token, "getUpdates", {})
    seen: dict[str, str] = {}
    for upd in updates:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat")
        if not chat:
            continue
        label = chat.get("username") or chat.get("first_name") or chat.get("title") or chat.get("type", "")
        seen[str(chat["id"])] = label
    return list(seen.items())
