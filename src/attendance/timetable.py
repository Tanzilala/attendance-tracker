"""The student's real weekly timetable, used for week-planning forecasts.

Encoded from the TYBFM Sem V timetable with the student's corrections applied:
  - Sports moved from Saturday 11:05-13:05 to Wednesday 11:05-13:05
  - Thursday 11:05 Sports kept as-is
  - All Drama / Music / Yoga / NSS / Dance slots removed (not enrolled)

Subject keys map to the course names as they appear in the attendance PDF, so a
planned class can be tied back to its subject tally. Sports is left as a generic
key because the PDF splits it into two batches (IVP / IVT) that the timetable
doesn't distinguish.
"""

from __future__ import annotations

# PDF course-name ↔ timetable abbreviation.
SUBJECT_BY_CODE = {
    "FD": "Financial DerivativesTA",
    "IDT": "Indirect TaxTA",
    "RM": "Risk ManagementTA",
    "ER": "Equity ResearchTA",
    "SCA": "Strategic Corporate AccountingTA",
    "CM": "Compliance ManagementTA",
    "SPORTS": "Sports",  # generic; PDF has IVP + IVT batches
}

# Each day is a list of (time_label, subject_code) in chronological order.
WEEK: dict[str, list[tuple[str, str]]] = {
    "Monday": [
        ("08:55", "FD"),
        ("09:55", "FD"),
    ],
    "Tuesday": [
        ("06:45", "IDT"),
        ("07:45", "IDT"),
        ("08:55", "RM"),
        ("09:55", "RM"),
    ],
    "Wednesday": [
        ("06:45", "ER"),
        ("07:45", "ER"),
        ("08:55", "SCA"),
        ("09:55", "CM"),
        ("11:05", "SPORTS"),  # moved here from Saturday
        ("12:05", "SPORTS"),
    ],
    "Thursday": [
        ("06:45", "RM"),
        ("07:45", "RM"),
        ("08:55", "FD"),
        ("09:55", "FD"),
        ("11:05", "SPORTS"),  # kept
    ],
    "Friday": [
        ("08:55", "CM"),
        ("09:55", "SCA"),
    ],
    "Saturday": [
        ("08:55", "ER"),
        ("09:55", "ER"),
    ],
}


def weekly_counts() -> dict[str, int]:
    """Classes per subject code in a normal week."""
    counts: dict[str, int] = {}
    for day in WEEK.values():
        for _, code in day:
            counts[code] = counts.get(code, 0) + 1
    return counts
