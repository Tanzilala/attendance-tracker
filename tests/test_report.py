from attendance.analysis import analyse, SubjectAttendance
from attendance.report import format_report


def render(subs, **kw):
    kw.setdefault("classes_per_day", 6)
    return format_report(analyse(subs), **kw)


def test_headline_is_cumulative_and_flags_below():
    text = render([SubjectAttendance("Maths", 77, 37, 3)])
    assert "67.54%" in text
    assert "BELOW 75%" in text


def test_flags_above_when_cumulative_passes():
    text = render([SubjectAttendance("Maths", 90, 10, 0)])
    assert "ABOVE 75%" in text


def test_nu_projection_lines_appear_only_when_pending_exists():
    with_nu = render([SubjectAttendance("Maths", 30, 10, 20)])
    assert "best case" in with_nu and "worst case" in with_nu

    without_nu = render([SubjectAttendance("Maths", 30, 10, 0)])
    assert "best case" not in without_nu


def test_every_subject_appears_in_breakdown():
    text = render([SubjectAttendance("Alpha", 5, 1, 0), SubjectAttendance("Beta", 3, 2, 1)])
    assert "Alpha" in text and "Beta" in text


def test_output_is_ascii_safe_for_windows_console():
    # The Windows terminal is cp1252; non-ASCII renders as mojibake.
    text = render([SubjectAttendance("Maths", 77, 37, 3)])
    text.encode("ascii")  # raises if any stray unicode slipped in


def test_day_estimate_states_its_divisor():
    text = render([SubjectAttendance("Maths", 77, 37, 3)], classes_per_day=8)
    assert "8 classes/day" in text
