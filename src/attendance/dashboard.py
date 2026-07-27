"""Generate a self-contained interactive attendance dashboard (single HTML file).

All data is embedded and every projection is recomputed client-side in JS, so the
file works offline with no server and no network calls — your attendance data
never leaves the machine. The JS math mirrors analysis.py exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

from attendance.analysis import Report
from attendance.timetable import SUBJECT_BY_CODE, WEEK


def _payload(report: Report, *, student: str, duration: str, generated: str,
            classes_per_day: int) -> dict:
    timetable = [
        {"day": day, "time": time, "code": code,
         "subject": SUBJECT_BY_CODE.get(code, code)}
        for day, slots in WEEK.items()
        for time, code in slots
    ]
    return {
        "student": student,
        "duration": duration,
        "generated": generated,
        "classesPerDay": classes_per_day,
        "subjects": [
            {"name": s.name, "p": s.present, "a": s.absent, "nu": s.not_updated}
            for s in report.subjects
        ],
        "timetable": timetable,
    }


def render_dashboard(report: Report, *, student: str = "", duration: str = "",
                    generated: str = "", classes_per_day: int = 6) -> str:
    data = _payload(report, student=student, duration=duration,
                    generated=generated, classes_per_day=classes_per_day)
    return _TEMPLATE.replace("/*__DATA__*/", json.dumps(data))


def write_dashboard(report: Report, path: Path, **meta) -> Path:
    path.write_text(render_dashboard(report, **meta), encoding="utf-8")
    return path.resolve()


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Attendance Dashboard</title>
<style>
  :root {
    --bg: #f4f5fb; --card: #ffffff; --ink: #1e2233; --muted: #6b7280;
    --line: #e7e8f0; --accent: #6366f1; --accent-soft: #eef0fe;
    --green: #10b981; --amber: #f59e0b; --red: #ef4444;
    --shadow: 0 1px 3px rgba(20,20,50,.06), 0 8px 24px rgba(20,20,50,.05);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0e0f17; --card: #191b26; --ink: #e8e9f0; --muted: #9aa0b0;
      --line: #262838; --accent: #818cf8; --accent-soft: #23263a;
      --shadow: 0 1px 3px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 24px 16px 64px;
  }
  .wrap { max-width: 880px; margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 18px;
          padding: 22px; box-shadow: var(--shadow); }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .between { justify-content: space-between; }
  .muted { color: var(--muted); }
  .label { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  h1 { font-size: 15px; margin: 0; }
  select, input[type=range] { accent-color: var(--accent); }
  select { background: var(--card); color: var(--ink); border: 1px solid var(--line);
           border-radius: 9px; padding: 6px 10px; font: inherit; }
  .pill { background: var(--accent-soft); color: var(--accent); border-radius: 999px;
          padding: 6px 14px; font-weight: 600; display: inline-flex; gap: 8px; align-items: center; }
  .big { font-size: 64px; font-weight: 800; line-height: 1; letter-spacing: -.02em; margin: 6px 0; }
  .status { font-weight: 700; font-size: 13px; padding: 4px 12px; border-radius: 999px; }
  .ok { background: rgba(16,185,129,.14); color: var(--green); }
  .bad { background: rgba(239,68,68,.14); color: var(--red); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; }
  .ring-card { text-align: center; padding: 16px 8px; }
  .ring-name { font-size: 13px; font-weight: 600; margin-top: 8px; }
  .ring-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .forecast { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
  .fbox { border: 1px solid var(--line); border-radius: 12px; padding: 14px; text-align: center; }
  .fbox .v { font-size: 26px; font-weight: 800; margin-top: 4px; }
  .g { color: var(--green); } .r { color: var(--red); } .a { color: var(--amber); }
  input[type=range] { width: 100%; }
  .val { font-variant-numeric: tabular-nums; font-weight: 700; min-width: 40px; text-align: right; }
  .week { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }
  .day { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .day > .dname { width: 92px; font-weight: 600; font-size: 13px; color: var(--muted); }
  .chip { border: 1px solid var(--line); border-radius: 8px; padding: 5px 10px; font-size: 12px;
          cursor: pointer; user-select: none; background: var(--card); transition: .12s; }
  .chip.on { background: rgba(16,185,129,.14); border-color: var(--green); color: var(--green); }
  .chip.off { background: rgba(239,68,68,.10); border-color: var(--red); color: var(--red); text-decoration: line-through; }
  .note { font-size: 12px; color: var(--muted); margin-top: 10px; }
  details summary { cursor: pointer; font-weight: 700; }
</style>
</head>
<body>
<div class="wrap">
  <h2 class="sr-only">Interactive attendance dashboard showing overall percentage, forecasts, and per-subject breakdown.</h2>

  <div class="card row between">
    <div>
      <h1>Attendance Dashboard</h1>
      <div class="muted" id="meta"></div>
    </div>
    <div class="pill" id="student"></div>
  </div>

  <div class="card">
    <div class="row between">
      <div>
        <div class="label">NU (not-yet-updated) classes</div>
        <div class="muted"><span id="nuCount"></span> pending &mdash; count them as</div>
      </div>
      <select id="nuMode">
        <option value="ignored">Ignored (excluded)</option>
        <option value="present">Present</option>
        <option value="absent">Absent</option>
      </select>
    </div>
  </div>

  <div class="card">
    <div class="label">Overall attendance</div>
    <div class="big" id="overall">--</div>
    <div class="row">
      <span class="status" id="status"></span>
      <span class="muted" id="fraction"></span>
    </div>
    <div id="verdict" class="note"></div>
  </div>

  <div class="card">
    <div class="label">Forecast &mdash; next classes</div>
    <div class="row" style="margin-top:10px">
      <input type="range" id="fcast" min="1" max="20" value="4">
      <span class="val" id="fcastVal">4</span>
    </div>
    <div class="forecast">
      <div class="fbox"><div class="muted">If attended</div><div class="v g" id="ifAttend">--</div></div>
      <div class="fbox"><div class="muted">If missed</div><div class="v r" id="ifMiss">--</div></div>
    </div>

    <div class="label" style="margin-top:20px">Target goal</div>
    <div class="row" style="margin-top:10px">
      <input type="range" id="target" min="40" max="100" value="75">
      <span class="val"><span id="targetVal">75</span>%</span>
    </div>
    <div class="note" id="targetMsg"></div>
  </div>

  <div class="card">
    <details open>
      <summary>Plan your week</summary>
      <div class="note">Tap a class to toggle attend / skip. Your current % above stays as-is; this projects where you'd land.</div>
      <div class="week" id="week"></div>
      <div class="fbox" style="margin-top:12px">
        <div class="muted">If you follow this plan, end of week:</div>
        <div class="v" id="planResult">--</div>
        <div class="note" id="planDetail"></div>
      </div>
    </details>
  </div>

  <div class="card">
    <div class="label" style="margin-bottom:14px">Subjects (<span id="subjCount"></span>)</div>
    <div class="grid" id="rings"></div>
    <div class="note">Rings are per subject. The 75% check is cumulative across all subjects, not per subject.</div>
  </div>
</div>

<script>
const DATA = /*__DATA__*/;

// ---- math (mirrors analysis.py) ----
function base(sub, mode) {
  let p = sub.p, a = sub.a;
  if (mode === "present") p += sub.nu;
  else if (mode === "absent") a += sub.nu;
  return { p, a };            // "ignored" leaves NU out of both
}
function pct(p, a) { const h = p + a; return h ? p / h : null; }
function needed(p, a, r) {    // consecutive classes to reach ratio r
  if (r >= 1) return Infinity;
  return Math.max(0, Math.ceil((r * (p + a) - p) / (1 - r)));
}
function canMiss(p, a, r) {   // classes you can skip and still hold r
  if (r <= 0) return Infinity;
  return Math.max(0, Math.floor(p / r - p - a));
}
function totals(mode) {
  let p = 0, a = 0;
  for (const s of DATA.subjects) { const b = base(s, mode); p += b.p; a += b.a; }
  return { p, a };
}
const fmt = x => x === null ? "n/a" : (x * 100).toFixed(2) + "%";
const color = r => r === null ? "var(--muted)" : r >= .75 ? "var(--green)" : r >= .5 ? "var(--amber)" : "var(--red)";

// ---- week plan state ----
const plan = DATA.timetable.map(() => true);   // true = will attend

// ---- render ----
function render() {
  const mode = document.getElementById("nuMode").value;
  const t = totals(mode);
  const r = pct(t.p, t.a);

  document.getElementById("overall").textContent = fmt(r);
  document.getElementById("overall").style.color = color(r);
  const meets = r !== null && r >= .75;
  const st = document.getElementById("status");
  st.textContent = meets ? "ABOVE 75%" : "BELOW 75%";
  st.className = "status " + (meets ? "ok" : "bad");
  document.getElementById("fraction").textContent = `${t.p} present / ${t.p + t.a} counted`;

  const v = document.getElementById("verdict");
  if (meets) {
    const y = canMiss(t.p, t.a, .75);
    v.textContent = `You can miss ${y} more class(es) and stay at or above 75%.`;
  } else {
    const x = needed(t.p, t.a, .75);
    const days = (x / DATA.classesPerDay).toFixed(1);
    v.textContent = `Attend ${x} classes in a row to reach 75% (~${days} days at ${DATA.classesPerDay}/day).`;
  }

  // forecast
  const n = +document.getElementById("fcast").value;
  document.getElementById("fcastVal").textContent = n;
  const at = pct(t.p + n, t.a), ms = pct(t.p, t.a + n);
  document.getElementById("ifAttend").textContent = fmt(at);
  document.getElementById("ifMiss").textContent = fmt(ms);

  // target
  const tg = +document.getElementById("target").value;
  document.getElementById("targetVal").textContent = tg;
  const req = tg / 100;
  const tm = document.getElementById("targetMsg");
  if (r !== null && r >= req) {
    tm.textContent = `Already at ${tg}%. You can miss ${canMiss(t.p, t.a, req)} more class(es).`;
  } else {
    tm.innerHTML = `Attend <b>${needed(t.p, t.a, req)}</b> classes to hit ${tg}%.`;
  }

  renderRings(mode);
  renderPlanResult();   // keep the week projection in sync with NU mode too
}

function renderPlanResult() {
  // Project forward from actual current standing: each planned class adds a
  // present (attend) or an absent (skip). The headline % is never touched.
  const mode = document.getElementById("nuMode").value;
  const t = totals(mode);
  let attend = 0, skip = 0;
  plan.forEach(on => on ? attend++ : skip++);
  const p = t.p + attend, a = t.a + skip;
  const r = pct(p, a);
  const el = document.getElementById("planResult");
  el.textContent = fmt(r);
  el.style.color = color(r);
  const delta = r - pct(t.p, t.a);
  const sign = delta >= 0 ? "+" : "";
  document.getElementById("planDetail").textContent =
    `attending ${attend}, skipping ${skip} of ${plan.length} classes this week ` +
    `(${sign}${(delta * 100).toFixed(2)} pts vs now)`;
}

function ring(r) {
  const R = 34, C = 2 * Math.PI * R, frac = r === null ? 0 : r;
  const off = C * (1 - frac);
  return `<svg width="86" height="86" viewBox="0 0 86 86">
    <circle cx="43" cy="43" r="${R}" fill="none" stroke="var(--line)" stroke-width="8"/>
    <circle cx="43" cy="43" r="${R}" fill="none" stroke="${color(r)}" stroke-width="8"
      stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${off}"
      transform="rotate(-90 43 43)"/>
    <text x="43" y="48" text-anchor="middle" font-size="18" font-weight="800"
      fill="${color(r)}">${r === null ? "--" : Math.round(r * 100) + "%"}</text>
  </svg>`;
}
function renderRings(mode) {
  const el = document.getElementById("rings");
  el.innerHTML = "";
  for (const s of DATA.subjects) {
    const b = base(s, mode), r = pct(b.p, b.a);
    const d = document.createElement("div");
    d.className = "ring-card";
    d.innerHTML = ring(r) +
      `<div class="ring-name">${s.name}</div>` +
      `<div class="ring-sub">P ${b.p} &middot; A ${b.a}${s.nu ? " &middot; NU " + s.nu : ""}</div>`;
    el.appendChild(d);
  }
}

function renderWeek() {
  const el = document.getElementById("week");
  const byDay = {};
  DATA.timetable.forEach((c, i) => { (byDay[c.day] ||= []).push(i); });
  el.innerHTML = "";
  for (const day of Object.keys(byDay)) {
    const wrap = document.createElement("div");
    wrap.className = "day";
    wrap.innerHTML = `<div class="dname">${day}</div>`;
    for (const i of byDay[day]) {
      const c = DATA.timetable[i];
      const chip = document.createElement("span");
      chip.className = "chip on";
      chip.textContent = `${c.time} ${c.code}`;
      chip.title = c.subject;
      chip.onclick = () => {
        plan[i] = !plan[i];
        chip.className = "chip " + (plan[i] ? "on" : "off");
        renderPlanResult();   // headline stays put; only the projection moves
      };
      wrap.appendChild(chip);
    }
    el.appendChild(wrap);
  }
}

// ---- init ----
document.getElementById("student").textContent = DATA.student || "Student";
document.getElementById("meta").textContent =
  [DATA.duration && ("Duration: " + DATA.duration), DATA.generated && ("Generated " + DATA.generated)]
  .filter(Boolean).join("  ·  ");
document.getElementById("nuCount").textContent =
  DATA.subjects.reduce((n, s) => n + s.nu, 0);
document.getElementById("subjCount").textContent = DATA.subjects.length;
["nuMode", "fcast", "target"].forEach(id =>
  document.getElementById(id).addEventListener("input", render));
renderWeek();
render();
</script>
</body>
</html>
"""
