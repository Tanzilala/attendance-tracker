"""Turn the portal's attendance PDF into per-subject tallies.

The PDF is a per-class ledger — one row per period, columns:
    Sr No. | Course Name | Date | Start Time | End Time | Attendance
with a single P / A / NU mark per row. Per-subject P/A/NU counts (what the email
breakdown needs) are computed by grouping rows on Course Name; the cumulative
figures then fall out of analysis.analyse().
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pdfplumber

from attendance.analysis import SubjectAttendance

# The only marks the portal emits. Anything else is a parsing surprise we refuse
# to guess at — miscounting attendance is worse than failing the run.
PRESENT, ABSENT, NOT_UPDATED = "P", "A", "NU"
KNOWN_MARKS = {PRESENT, ABSENT, NOT_UPDATED}

ATTENDANCE_COLUMNS = 6  # Sr, Course, Date, Start, End, Attendance


class ParseError(RuntimeError):
    """The PDF didn't look the way the parser expects."""


def _is_data_row(row: list[str | None]) -> bool:
    """True for a real class row, False for headers repeated on every page."""
    if not row or len(row) < ATTENDANCE_COLUMNS:
        return False
    first = (row[0] or "").strip()
    return first.isdigit()


def parse_rows(rows: list[list[str | None]]) -> list[SubjectAttendance]:
    """Group raw table rows into per-subject tallies. Pure and testable.

    Preserves first-seen course order so the email breakdown reads in the same
    order as the PDF rather than alphabetised.
    """
    tallies: dict[str, dict[str, int]] = defaultdict(lambda: {PRESENT: 0, ABSENT: 0, NOT_UPDATED: 0})
    order: list[str] = []
    unknown: dict[str, int] = defaultdict(int)

    for row in rows:
        if not _is_data_row(row):
            continue

        course = " ".join((row[1] or "").split())  # collapse wrapped whitespace
        mark = (row[ATTENDANCE_COLUMNS - 1] or "").strip().upper()

        if mark not in KNOWN_MARKS:
            unknown[mark or "<blank>"] += 1
            continue

        if course not in tallies:
            order.append(course)
        tallies[course][mark] += 1

    if unknown:
        detail = ", ".join(f"{m}×{n}" for m, n in unknown.items())
        raise ParseError(
            f"Unexpected attendance mark(s): {detail}. Refusing to compute a "
            "percentage that might be wrong — the PDF format may have changed."
        )

    if not order:
        raise ParseError(
            "No attendance rows found in the PDF. Either the report was empty or "
            "the table layout changed."
        )

    return [
        SubjectAttendance(
            name=course,
            present=tallies[course][PRESENT],
            absent=tallies[course][ABSENT],
            not_updated=tallies[course][NOT_UPDATED],
        )
        for course in order
    ]


def parse_pdf(path: str | Path) -> list[SubjectAttendance]:
    """Extract per-subject tallies from the attendance PDF at ``path``."""
    path = Path(path)
    if not path.exists():
        raise ParseError(f"PDF not found: {path}")

    rows: list[list[str | None]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                rows.extend(table)

    if not rows:
        raise ParseError("No tables found in the PDF at all — is it the right file?")

    return parse_rows(rows)
