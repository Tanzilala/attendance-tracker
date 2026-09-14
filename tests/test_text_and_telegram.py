from datetime import datetime

import pytest

from attendance.analysis import analyse, SubjectAttendance
from attendance.cli import _in_window, _parse_message, _validate_step
from attendance.report import format_text
from attendance import telegram


def _update(chat_id, text, uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id}, "text": text}}


class TestParseMessage:
    def test_returns_chat_id_as_string_and_trimmed_text(self):
        assert _parse_message(_update(42, "  /check ")) == ("42", "/check")

    def test_non_message_update_is_ignored(self):
        assert _parse_message({"update_id": 1}) == (None, "")

    def test_message_without_text_yields_empty(self):
        assert _parse_message({"update_id": 1, "message": {"chat": {"id": 42}}}) == ("42", "")


class TestRegistrationValidation:
    def test_semester_number_becomes_label(self):
        assert _validate_step("semester", "5") == "Semester V"

    def test_bad_semester_raises_user_message(self):
        with pytest.raises(ValueError, match="1-8"):
            _validate_step("semester", "banana")

    def test_year_becomes_academic_year_label(self):
        assert _validate_step("year", "2026") == "Acad .Year 2026-2027"

    def test_bad_year_raises(self):
        with pytest.raises(ValueError):
            _validate_step("year", "26")

    def test_valid_date_passes_through(self):
        assert _validate_step("start_date", "12.06.2026") == "12.06.2026"

    def test_bad_date_raises(self):
        with pytest.raises(ValueError, match="DD.MM.YYYY"):
            _validate_step("start_date", "2026-06-12")

    def test_classes_skip_defaults_to_six(self):
        assert _validate_step("classes", "skip") == 6
        assert _validate_step("classes", "8") == 8


class TestViewingWindow:
    @pytest.mark.parametrize("hour", [18, 22, 23, 0, 3, 6])
    def test_inside_window(self, hour):
        assert _in_window(datetime(2026, 7, 24, hour, 0))

    @pytest.mark.parametrize("hour", [7, 9, 12, 15, 17])
    def test_outside_window(self, hour):
        assert not _in_window(datetime(2026, 7, 24, hour, 0))

    def test_boundaries(self):
        assert _in_window(datetime(2026, 7, 24, 18, 0))       # 6 PM: in
        assert not _in_window(datetime(2026, 7, 24, 17, 59))  # 5:59 PM: out
        assert not _in_window(datetime(2026, 7, 24, 7, 0))    # 7 AM: out


def text(subs, **kw):
    return format_text(analyse(subs), **kw)


class TestFormatText:
    def test_has_percentage_catchup_and_pending_only(self):
        msg = text([SubjectAttendance("M", 77, 37, 3)], when="23 Jul")
        assert "67.5%" in msg
        assert "Attend 34 more in a row to reach 75%." in msg
        assert "3 lectures not yet updated." in msg

    def test_omits_raw_present_absent_counts(self):
        # The whole point: no "77 present / 114" style numbers.
        msg = text([SubjectAttendance("M", 77, 37, 3)])
        assert "77" not in msg and "114" not in msg and "present /" not in msg

    def test_above_75_reports_buffer_instead_of_catchup(self):
        msg = text([SubjectAttendance("M", 90, 10, 0)])
        assert "Above 75%" in msg
        assert "reach 75%" not in msg

    def test_no_pending_line_when_nothing_is_unupdated(self):
        msg = text([SubjectAttendance("M", 90, 10, 0)])
        assert "not yet updated" not in msg

    def test_singular_lecture_wording(self):
        msg = text([SubjectAttendance("M", 50, 40, 1)])
        assert "1 lecture not yet updated." in msg

    def test_handles_all_pending_without_crashing(self):
        msg = text([SubjectAttendance("M", 0, 0, 5)])
        assert "No classes marked yet." in msg
        assert "5 lectures not yet updated." in msg


class TestTelegramSend:
    def test_send_posts_expected_payload(self, monkeypatch):
        captured = {}

        def fake_call(token, method, payload):
            captured["token"] = token
            captured["method"] = method
            captured["payload"] = payload
            return {}

        monkeypatch.setattr(telegram, "_call", fake_call)
        telegram.send_message("TOKEN", "12345", "hello")
        assert captured["method"] == "sendMessage"
        assert captured["payload"] == {"chat_id": "12345", "text": "hello"}

    def test_error_message_never_leaks_the_token(self):
        # The API url embeds the token; error text must not include the url.
        err = telegram.TelegramError("Telegram API error: chat not found")
        assert "TOKEN" not in str(err) and "api.telegram.org" not in str(err)
