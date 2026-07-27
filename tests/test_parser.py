from pathlib import Path

import pytest

from attendance.analysis import analyse
from attendance.parser import ParseError, parse_pdf, parse_rows

HEADER = ["Sr\nNo.", "Course Name", "Date", "Start Time", "End Time", "Attenda\nnce"]


def row(sr, course, mark):
    return [str(sr), course, "Jun 12, 2026", "8:56:00 AM", "9:55:00 AM", mark]


class TestParseRows:
    def test_groups_by_course_and_counts_marks(self):
        rows = [
            HEADER,
            row(1, "Maths", "P"),
            row(2, "Maths", "A"),
            row(3, "Maths", "NU"),
            row(4, "Physics", "P"),
        ]
        subs = {s.name: s for s in parse_rows(rows)}
        assert (subs["Maths"].present, subs["Maths"].absent, subs["Maths"].not_updated) == (1, 1, 1)
        assert (subs["Physics"].present, subs["Physics"].absent) == (1, 0)

    def test_skips_header_rows_repeated_each_page(self):
        # Headers reappear on every page; they must never be counted.
        rows = [HEADER, row(1, "Maths", "P"), HEADER, row(2, "Maths", "P")]
        subs = parse_rows(rows)
        assert len(subs) == 1
        assert subs[0].present == 2

    def test_preserves_first_seen_order(self):
        rows = [row(1, "Zebra", "P"), row(2, "Alpha", "P")]
        assert [s.name for s in parse_rows(rows)] == ["Zebra", "Alpha"]

    def test_collapses_wrapped_course_names(self):
        rows = [row(1, "Strategic Corporate\nAccountingTA", "P")]
        assert parse_rows(rows)[0].name == "Strategic Corporate AccountingTA"

    def test_mark_is_case_and_space_insensitive(self):
        rows = [row(1, "Maths", " p "), row(2, "Maths", "nu")]
        s = parse_rows(rows)[0]
        assert (s.present, s.not_updated) == (1, 1)

    def test_unexpected_mark_raises_rather_than_miscounts(self):
        rows = [row(1, "Maths", "P"), row(2, "Maths", "X")]
        with pytest.raises(ParseError, match="Unexpected attendance mark"):
            parse_rows(rows)

    def test_blank_mark_is_treated_as_unexpected(self):
        # A blank could be a subtotal row; refuse rather than assume.
        rows = [row(1, "Maths", "P"), row(2, "Maths", "")]
        with pytest.raises(ParseError):
            parse_rows(rows)

    def test_no_data_rows_raises(self):
        with pytest.raises(ParseError, match="No attendance rows"):
            parse_rows([HEADER])


# Runs only on the machine that has the real capture; skipped in CI.
REAL_PDF = Path(__file__).parent.parent / "downloads" / "attendance-1.pdf"


@pytest.mark.skipif(not REAL_PDF.exists(), reason="real PDF not present")
class TestRealPdf:
    """Invariants that hold for any capture — no frozen values, since the PDF
    is refreshed every time attendance is pulled."""

    def test_parses_a_nonempty_report(self):
        subs = parse_pdf(REAL_PDF)
        assert subs, "expected at least one subject"
        assert all(s.present + s.absent + s.not_updated > 0 for s in subs)

    def test_cumulative_totals_equal_sum_of_subjects(self):
        subs = parse_pdf(REAL_PDF)
        report = analyse(subs)
        assert report.present == sum(s.present for s in subs)
        assert report.absent == sum(s.absent for s in subs)
        assert report.not_updated == sum(s.not_updated for s in subs)

    def test_percentage_is_well_defined(self):
        report = analyse(parse_pdf(REAL_PDF))
        pct = report.current.percentage
        assert pct is None or 0.0 <= pct <= 1.0
