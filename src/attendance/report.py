"""Render the cumulative analysis into a human-readable report.

Kept transport-agnostic on purpose: it returns plain text, so the same output
can go to the terminal now and into an email/Telegram body later.
"""

from __future__ import annotations

from attendance.analysis import REQUIREMENT, Report, Scenario


def format_text(report: Report, *, when: str = "") -> str:
    """A short, phone-friendly message: percentage, catch-up, pending count.

    Deliberately omits raw present/absent counts — the ask was the percentage,
    how many more to reach 75%, and how many are not yet updated. Nothing else.
    """
    cur = report.current
    lines: list[str] = []
    lines.append(f"Attendance{f' - {when}' if when else ''}")

    pct = cur.percentage
    if pct is None:
        lines.append("No classes marked yet.")
    else:
        lines.append(f"Present: {pct * 100:.1f}%")
        if cur.meets_requirement:
            lines.append(f"Above 75% — you can skip {cur.classes_can_miss} and stay there.")
        else:
            lines.append(f"Attend {cur.classes_needed} more in a row to reach 75%.")

    if report.not_updated:
        s = "s" if report.not_updated != 1 else ""
        lines.append(f"{report.not_updated} lecture{s} not yet updated.")

    return "\n".join(lines)


def _pct(scenario: Scenario) -> str:
    p = scenario.percentage
    return "n/a" if p is None else f"{p * 100:.2f}%"


def _scenario_line(report: Report, scenario: Scenario, classes_per_day: int) -> str:
    """One line describing where a scenario stands and what it takes to fix it."""
    verdict = "PASS" if scenario.meets_requirement else "SHORT"
    head = f"  {scenario.label:<11} {_pct(scenario):>7}  [{verdict}]"

    if scenario.meets_requirement:
        buffer = scenario.classes_can_miss
        days = report.days(buffer, classes_per_day)
        tail = f"can miss {buffer} more class(es)" + (f" (~{days:.1f} days)" if days else "")
    else:
        need = scenario.classes_needed
        days = report.days(need, classes_per_day)
        tail = f"attend {need} in a row to reach 75%" + (f" (~{days:.1f} days)" if days else "")
    return f"{head}  - {tail}"


def format_report(
    report: Report,
    *,
    classes_per_day: int,
    duration: str = "",
    generated_at: str = "",
) -> str:
    total_marked = report.present + report.absent
    lines: list[str] = []

    lines.append("=" * 60)
    lines.append("  ATTENDANCE REPORT")
    if duration:
        lines.append(f"  Duration: {duration}")
    if generated_at:
        lines.append(f"  Generated: {generated_at}")
    lines.append("=" * 60)

    # The headline is the cumulative figure — the whole point of the tool.
    current_pct = _pct(report.current)
    status = "ABOVE 75%" if report.current.meets_requirement else "BELOW 75%"
    lines.append("")
    lines.append(f"  OVERALL: {current_pct}   ({status})")
    lines.append(f"  Cumulative across all subjects: {report.present} present / "
                 f"{total_marked} marked classes")
    if report.not_updated:
        lines.append(f"  ({report.not_updated} class(es) not yet updated by staff - "
                     "excluded from the % above)")

    lines.append("")
    lines.append("  Projection (NU = not-yet-updated classes):")
    # current excludes NU; best/worst fold the NU either way, per the spec.
    lines.append(_scenario_line(report, report.current, classes_per_day))
    if report.not_updated:
        lines.append(_scenario_line(report, report.best_case, classes_per_day))
        lines.append(_scenario_line(report, report.worst_case, classes_per_day))

    lines.append("")
    lines.append("  Per-subject breakdown (for visibility only - the 75% check")
    lines.append("  above is cumulative, not per subject):")
    lines.append("")
    name_w = max((len(s.name) for s in report.subjects), default=10)
    lines.append(f"    {'Subject':<{name_w}}   P    A   NU     %")
    lines.append(f"    {'-' * name_w}  ---  ---  ---  ------")
    for s in report.subjects:
        held = s.present + s.absent
        pct = f"{s.present / held * 100:5.1f}%" if held else "   n/a"
        lines.append(f"    {s.name:<{name_w}}  {s.present:>3}  {s.absent:>3}  "
                     f"{s.not_updated:>3}  {pct}")

    lines.append("")
    lines.append(f"  Note: day estimates assume {classes_per_day} classes/day and a full")
    lines.append("  timetable. Class counts are exact; day figures are approximate.")
    lines.append("=" * 60)
    return "\n".join(lines)
