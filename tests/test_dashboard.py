import json
import re

from attendance.analysis import analyse, SubjectAttendance
from attendance.dashboard import render_dashboard


def build(subs, **kw):
    return render_dashboard(analyse(subs), **kw)


def _embedded_data(html):
    m = re.search(r"const DATA = (\{.*?\});", html, re.S)
    assert m, "embedded DATA object not found"
    return json.loads(m.group(1))


def test_is_self_contained_no_external_refs():
    html = build([SubjectAttendance("Maths", 10, 2, 1)])
    # No network dependencies — must work offline with personal data.
    assert "http://" not in html and "https://" not in html
    assert "src=" not in html

def test_embeds_subject_data_verbatim():
    html = build([SubjectAttendance("Maths", 10, 2, 1)], student="A B")
    data = _embedded_data(html)
    assert data["student"] == "A B"
    assert data["subjects"] == [{"name": "Maths", "p": 10, "a": 2, "nu": 1}]


def test_includes_corrected_timetable():
    data = _embedded_data(build([SubjectAttendance("Maths", 10, 2, 0)]))
    tt = data["timetable"]
    # Sports must be on Wednesday (moved) and Thursday (kept), never Saturday.
    sat = [c for c in tt if c["day"] == "Saturday"]
    assert all(c["code"] != "SPORTS" for c in sat)
    assert any(c["day"] == "Wednesday" and c["code"] == "SPORTS" for c in tt)
    assert any(c["day"] == "Thursday" and c["code"] == "SPORTS" for c in tt)
    # No dropped activities leak in.
    assert not any(c["code"] in {"DRAMA", "YOGA", "NSS", "DANCE", "MUSIC"} for c in tt)


def test_data_is_valid_json_even_with_awkward_names():
    # A quote or backslash in a course name must not break the embedded JSON.
    html = build([SubjectAttendance('Weird "Name" \\x', 1, 0, 0)])
    _embedded_data(html)  # raises if the JSON is malformed
