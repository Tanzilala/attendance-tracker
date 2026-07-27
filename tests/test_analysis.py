from attendance.analysis import REQUIREMENT, Report, Scenario, SubjectAttendance, analyse


def sub(name="Subject", p=0, a=0, nu=0):
    return SubjectAttendance(name=name, present=p, absent=a, not_updated=nu)


class TestCurrentPercentage:
    def test_nu_is_excluded_from_the_denominator(self):
        # 30P/10A is exactly 75% — the 20 NU must not drag it down.
        report = analyse([sub(p=30, a=10, nu=20)])
        assert report.current.percentage == 0.75
        assert report.current.held == 40

    def test_percentage_is_none_when_nothing_has_settled(self):
        # All pending: a percentage would be a divide by zero, not 0%.
        report = analyse([sub(p=0, a=0, nu=12)])
        assert report.current.percentage is None
        assert report.current.meets_requirement is False

    def test_totals_roll_up_across_subjects(self):
        report = analyse([sub("Maths", 20, 5, 2), sub("Physics", 10, 5, 3)])
        assert (report.present, report.absent, report.not_updated) == (30, 10, 5)


class TestRequirementBoundary:
    def test_exactly_seventy_five_percent_passes(self):
        s = Scenario("t", present=75, absent=25)
        assert s.percentage == REQUIREMENT
        assert s.meets_requirement is True
        # Right on the line: nothing owed, but no slack either.
        assert s.classes_needed == 0
        assert s.classes_can_miss == 0

    def test_a_hair_under_fails(self):
        s = Scenario("t", present=74, absent=26)
        assert s.meets_requirement is False


class TestClassesNeeded:
    def test_zero_when_already_above(self):
        assert Scenario("t", present=90, absent=10).classes_needed == 0

    def test_attending_exactly_that_many_reaches_the_line(self):
        s = Scenario("t", present=70, absent=30)
        x = s.classes_needed
        assert x == 20
        assert (s.present + x) / (s.held + x) >= REQUIREMENT

    def test_one_fewer_falls_short(self):
        s = Scenario("t", present=70, absent=30)
        x = s.classes_needed - 1
        assert (s.present + x) / (s.held + x) < REQUIREMENT

    def test_small_numbers(self):
        s = Scenario("t", present=1, absent=1)
        assert s.classes_needed == 2
        assert (1 + 2) / (2 + 2) == REQUIREMENT


class TestClassesCanMiss:
    def test_zero_when_below_the_line(self):
        assert Scenario("t", present=50, absent=50).classes_can_miss == 0

    def test_missing_exactly_that_many_stays_above(self):
        s = Scenario("t", present=80, absent=20)
        y = s.classes_can_miss
        assert y == 6
        assert s.present / (s.held + y) >= REQUIREMENT

    def test_missing_one_more_drops_below(self):
        s = Scenario("t", present=80, absent=20)
        y = s.classes_can_miss + 1
        assert s.present / (s.held + y) < REQUIREMENT


class TestScenarios:
    def test_best_case_resolves_pending_as_present(self):
        report = analyse([sub(p=30, a=10, nu=20)])
        assert (report.best_case.present, report.best_case.absent) == (50, 10)
        assert report.best_case.percentage == 50 / 60

    def test_worst_case_resolves_pending_as_absent(self):
        report = analyse([sub(p=30, a=10, nu=20)])
        assert (report.worst_case.present, report.worst_case.absent) == (30, 30)
        assert report.worst_case.percentage == 0.5

    def test_pending_entries_can_flip_the_verdict(self):
        # Sitting on 75% now, but enough pending marks to sink it.
        report = analyse([sub(p=30, a=10, nu=20)])
        assert report.current.meets_requirement is True
        assert report.best_case.meets_requirement is True
        assert report.worst_case.meets_requirement is False

    def test_scenarios_are_identical_when_nothing_is_pending(self):
        report = analyse([sub(p=30, a=10, nu=0)])
        assert report.best_case.percentage == report.worst_case.percentage


class TestCumulativeNotPerSubject:
    def test_a_failing_subject_does_not_fail_the_overall_check(self):
        # Physics alone is 50%, but the cumulative figure clears 75%.
        report = analyse([sub("Maths", 90, 5), sub("Physics", 5, 5)])
        assert report.current.percentage == 95 / 105
        assert report.current.meets_requirement is True


class TestDays:
    def test_converts_classes_to_days(self):
        report = analyse([sub(p=1, a=1)])
        assert report.days(12, classes_per_day=6) == 2.0

    def test_guards_against_a_zero_divisor(self):
        report = analyse([sub(p=1, a=1)])
        assert report.days(12, classes_per_day=0) is None
