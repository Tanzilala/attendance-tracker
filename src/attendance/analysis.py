"""Cumulative attendance maths.

The 75% requirement is a single figure across ALL subjects combined. Per-subject
numbers exist only for the breakdown shown in the email; every pass/fail and
projection here is computed from the cumulative totals.

NU ("not yet updated") entries are pending, not absences. They are excluded from
the current percentage entirely, and modelled two ways in the projections:
best case they all resolve Present, worst case they all resolve Absent.
"""

from __future__ import annotations

from dataclasses import dataclass

REQUIREMENT = 0.75


@dataclass(frozen=True)
class SubjectAttendance:
    """One subject's raw marks, as read off the portal PDF."""

    name: str
    present: int
    absent: int
    not_updated: int

    @property
    def held(self) -> int:
        """Classes with a settled mark. NU is excluded — it isn't resolved yet."""
        return self.present + self.absent


@dataclass(frozen=True)
class Scenario:
    """Where you stand if the pending NU entries resolve a particular way."""

    label: str
    present: int
    absent: int

    @property
    def held(self) -> int:
        return self.present + self.absent

    @property
    def percentage(self) -> float | None:
        """None when no class has a settled mark — a percentage is undefined."""
        if self.held == 0:
            return None
        return self.present / self.held

    @property
    def meets_requirement(self) -> bool:
        pct = self.percentage
        return pct is not None and pct >= REQUIREMENT

    @property
    def classes_needed(self) -> int:
        """Consecutive classes to attend to reach 75%. Zero if already there.

        From (P + x) / (P + A + x) >= 0.75  =>  x >= 3A - P.
        """
        return max(0, 3 * self.absent - self.present)

    @property
    def classes_can_miss(self) -> int:
        """Classes you could miss and still hold 75%. Zero if already below.

        From P / (P + A + y) >= 0.75  =>  y <= (P - 3A) / 3.
        """
        return max(0, self.present // 3 - self.absent)


@dataclass(frozen=True)
class Report:
    subjects: tuple[SubjectAttendance, ...]
    present: int
    absent: int
    not_updated: int
    current: Scenario
    best_case: Scenario
    worst_case: Scenario

    def days(self, classes: int, classes_per_day: int) -> float | None:
        """Convert a class count to days. None if the divisor isn't usable.

        Deliberately separate from the class counts: the class figure is exact,
        the day figure is an estimate that assumes a full timetable every day.
        """
        if classes_per_day <= 0:
            return None
        return classes / classes_per_day


def analyse(subjects: list[SubjectAttendance]) -> Report:
    """Roll per-subject marks up into the cumulative picture."""
    present = sum(s.present for s in subjects)
    absent = sum(s.absent for s in subjects)
    not_updated = sum(s.not_updated for s in subjects)

    return Report(
        subjects=tuple(subjects),
        present=present,
        absent=absent,
        not_updated=not_updated,
        # NU excluded from the denominator — those classes aren't resolved.
        current=Scenario("current", present, absent),
        # Every pending entry lands as Present.
        best_case=Scenario("best case", present + not_updated, absent),
        # Every pending entry lands as Absent.
        worst_case=Scenario("worst case", present, absent + not_updated),
    )
