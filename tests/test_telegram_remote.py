"""Tests for the phone-relay Telegram helpers (send_photo, reply polling)."""

from attendance import telegram


def test_multipart_encodes_fields_and_file():
    body, boundary = telegram._multipart(
        {"chat_id": "42", "caption": "hi"},
        {"photo": ("captcha.png", b"\x89PNG-bytes", "image/png")},
    )
    text = body.decode("latin-1")
    assert boundary in text
    assert 'name="chat_id"' in text and "42" in text
    assert 'name="caption"' in text and "hi" in text
    assert 'filename="captcha.png"' in text
    assert "\x89PNG-bytes" in text
    assert text.strip().endswith(f"--{boundary}--")


def test_send_photo_posts_multipart(monkeypatch):
    captured = {}

    def fake_open(req, timeout):
        captured["url"] = req.full_url
        captured["ctype"] = req.headers.get("Content-type", "")
        captured["data"] = req.data
        return {}

    monkeypatch.setattr(telegram, "_open", fake_open)
    telegram.send_photo("TOK", "42", b"IMGDATA", caption="solve me")

    assert "sendPhoto" in captured["url"]
    assert captured["ctype"].startswith("multipart/form-data; boundary=")
    assert b"IMGDATA" in captured["data"]
    assert b"solve me" in captured["data"]


def test_next_offset_is_one_past_highest(monkeypatch):
    monkeypatch.setattr(telegram, "_call",
                        lambda *a, **k: [{"update_id": 5}, {"update_id": 9}])
    assert telegram.next_offset("TOK") == 10


def test_next_offset_zero_when_empty(monkeypatch):
    monkeypatch.setattr(telegram, "_call", lambda *a, **k: [])
    assert telegram.next_offset("TOK") == 0


def test_wait_for_text_returns_matching_chat_reply(monkeypatch):
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 999}, "text": "wrong chat"}},
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "Yr#I9k"}},
    ]
    monkeypatch.setattr(telegram, "_call", lambda *a, **k: updates)
    text, offset = telegram.wait_for_text("TOK", "42", 0, timeout_s=5)
    assert text == "Yr#I9k"      # case preserved
    assert offset == 12          # advanced past the consumed update


def test_wait_for_text_times_out_to_none(monkeypatch):
    monkeypatch.setattr(telegram, "_call", lambda *a, **k: [])
    # timeout_s=0 → deadline already passed → returns without blocking
    text, offset = telegram.wait_for_text("TOK", "42", 7, timeout_s=0)
    assert text is None
    assert offset == 7
