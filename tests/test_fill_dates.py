"""fill_dates must wait for WebDynpro's postback to render the date fields.

Regression guard for the friend-portal failure: 'Expected two date fields after
selecting Detail Report, found 0'. The two date inputs appear only after the
'Detail Report' postback finishes, which can lag behind the dropdown closing.
fill_dates polls for them instead of checking once.
"""

import pytest

from attendance.browser import FillError, fill_dates


class FakeBox:
    """A date input that only reports visible once the shared poll count reaches
    `visible_after` — modelling fields that render a few beats after selection."""

    def __init__(self, visible_after: int):
        self._visible_after = visible_after
        self._poll = [0]  # replaced with the frame's shared counter
        self.filled: list[str] = []

    def is_visible(self):
        if self._visible_after is None:
            return False  # never renders
        return self._poll[0] >= self._visible_after

    def is_editable(self):
        return True

    def click(self):
        pass

    def fill(self, value):
        self.filled.append(value)


class FakeLocator:
    def __init__(self, boxes):
        self._boxes = boxes

    def count(self):
        return len(self._boxes)

    def nth(self, i):
        return self._boxes[i]


class FakeFrame:
    def __init__(self, boxes):
        self._boxes = boxes
        self.poll = [0]
        for b in boxes:
            b._poll = self.poll  # share the counter so wait_for_timeout advances them

    def locator(self, selector):
        return FakeLocator(self._boxes)

    def wait_for_timeout(self, ms):
        self.poll[0] += 1


def test_fill_dates_waits_for_delayed_postback():
    # Both date boxes only become visible after two polls (the postback lag).
    boxes = [FakeBox(visible_after=2), FakeBox(visible_after=2)]
    frame = FakeFrame(boxes)

    fill_dates(frame, "12.06.2026", "22.09.2026")

    assert boxes[0].filled == ["", "12.06.2026"]  # clear then start date
    assert boxes[1].filled == ["", "22.09.2026"]  # clear then end date
    assert frame.poll[0] >= 2  # it actually had to wait


def test_fill_dates_raises_when_fields_never_render():
    boxes = [FakeBox(visible_after=None)]  # only one, and it never shows
    frame = FakeFrame(boxes)

    with pytest.raises(FillError, match="found 0"):
        fill_dates(frame, "12.06.2026", "22.09.2026", timeout_ms=100)
