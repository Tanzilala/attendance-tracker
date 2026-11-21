"use strict";

// All text goes through textContent — nothing from the server is ever parsed as HTML.
const $ = (id) => document.getElementById(id);
const show = (el, on) => { el.hidden = !on; };

async function api(path, body) {
  const opts = body === undefined
    ? { credentials: "same-origin" }
    : {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) {
    const err = new Error(data.error || "Something went wrong (" + res.status + ").");
    err.status = res.status;
    throw err;
  }
  return data;
}

const formData = (form) => Object.fromEntries(new FormData(form).entries());
const setError = (form, msg) => { form.querySelector("[data-error]").textContent = msg || ""; };

// ---- signed out / signed in --------------------------------------------- //

function showAuth(tab) {
  show($("auth"), true);
  show($("app"), false);
  const login = tab === "login";
  show($("login-form"), login);
  show($("register-form"), !login);
  $("tab-login").setAttribute("aria-selected", String(login));
  $("tab-register").setAttribute("aria-selected", String(!login));
}

function showApp(user, jobId) {
  show($("auth"), false);
  show($("app"), true);
  $("who-name").textContent = user.username;
  $("who-meta").textContent = user.semester + " · from " + user.start_date;

  const form = $("settings-form");
  form.elements.semester.value = String(["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
    .indexOf(user.semester.replace("Semester ", "")) + 1);
  form.elements.year.value = (user.academic_year.match(/(\d{4})-/) || [])[1] || "";
  form.elements.start_date.value = user.start_date;
  form.elements.classes.value = user.classes_per_day;
  form.elements.new_password.value = "";

  resetCheckUi();
  if (jobId) startPolling(jobId);
}

$("tab-login").addEventListener("click", () => showAuth("login"));
$("tab-register").addEventListener("click", () => showAuth("register"));

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  setError(e.target, "");
  try {
    const { user } = await api("/api/login", formData(e.target));
    e.target.reset();
    showApp(user, null);
  } catch (err) { setError(e.target, err.message); }
});

$("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  setError(e.target, "");
  try {
    const { user } = await api("/api/register", formData(e.target));
    e.target.reset();
    showApp(user, null);
  } catch (err) { setError(e.target, err.message); }
});

$("logout").addEventListener("click", async () => {
  stopPolling();
  try { await api("/api/logout", {}); } catch (_) { /* cookie is cleared server-side anyway */ }
  showAuth("login");
});

$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  setError(e.target, "");
  $("saved").textContent = "";
  const d = formData(e.target);
  try {
    const { user } = await api("/api/settings", d);
    $("who-meta").textContent = user.semester + " · from " + user.start_date;
    e.target.elements.new_password.value = "";
    $("saved").textContent = "Saved.";
  } catch (err) { setError(e.target, err.message); }
});

// ---- a check ------------------------------------------------------------ //

let pollTimer = null;
let tickTimer = null;
let shownCaptcha = 0;
let currentJob = null;
let deadline = 0;

function stopPolling() {
  clearTimeout(pollTimer); clearInterval(tickTimer);
  pollTimer = tickTimer = null; currentJob = null;
}

function resetCheckUi() {
  stopPolling();
  shownCaptcha = 0;
  show($("idle"), true); show($("progress"), false); show($("result"), false);
  $("check").disabled = false;
  $("check-error").textContent = "";
  $("job-error").textContent = "";
}

$("check").addEventListener("click", async () => {
  $("check").disabled = true;
  $("check-error").textContent = "";
  try {
    const { job } = await api("/api/check", {});
    startPolling(job);
  } catch (err) {
    $("check-error").textContent = err.message;
    $("check").disabled = false;
    if (err.status === 401) showAuth("login");
  }
});

$("again").addEventListener("click", resetCheckUi);

function startPolling(jobId) {
  stopPolling();
  currentJob = jobId;
  shownCaptcha = 0;
  show($("idle"), false); show($("progress"), true); show($("result"), false);
  $("job-error").textContent = "";
  $("status").textContent = "Starting...";
  show($("captcha-form"), false);
  tickTimer = setInterval(tick, 500);
  poll();
}

async function poll() {
  if (!currentJob) return;
  let job;
  try {
    job = await api("/api/job/" + encodeURIComponent(currentJob));
  } catch (err) {
    if (err.status === 401) { stopPolling(); showAuth("login"); return; }
    if (err.status === 404) { resetCheckUi(); return; }
    pollTimer = setTimeout(poll, 3000);  // transient network blip: keep trying
    return;
  }
  render(job);
  if (job.state !== "done" && job.state !== "error") {
    pollTimer = setTimeout(poll, 1500);
  }
}

function render(job) {
  const form = $("captcha-form");
  if (job.state === "done") {
    stopPolling();
    show($("progress"), false);
    $("result-text").textContent = job.result;
    show($("result"), true);
    return;
  }
  if (job.state === "error") {
    stopPolling();
    $("status").textContent = "Couldn't finish this check.";
    $("job-error").textContent = job.message;
    show(form, false);
    show($("idle"), true);
    $("check").disabled = false;
    return;
  }
  $("status").textContent = job.message || "Working...";
  if (job.state === "captcha" && job.captcha_version) {
    if (job.captcha_version !== shownCaptcha) {
      shownCaptcha = job.captcha_version;
      $("captcha-img").src = "/api/job/" + encodeURIComponent(currentJob) +
        "/captcha.png?v=" + job.captcha_version;
      form.elements.answer.value = "";
      form.querySelector("button").disabled = false;
    }
    deadline = Date.now() + job.seconds_left * 1000;
    show(form, true);
    form.elements.answer.focus({ preventScroll: true });
  } else {
    show(form, false);
  }
}

function tick() {
  const el = $("countdown");
  if ($("captcha-form").hidden) { el.textContent = ""; return; }
  const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
  el.textContent = left > 0
    ? "Time left to answer: " + Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0")
    : "Time's up — this check is closing.";
}

$("captcha-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("job-error").textContent = "";
  try {
    await api("/api/job/" + encodeURIComponent(currentJob) + "/captcha",
              { answer: e.target.elements.answer.value });
    show(e.target, false);
    $("status").textContent = "Logging in...";
  } catch (err) {
    $("job-error").textContent = err.message;
    btn.disabled = false;
  }
});

// ---- boot --------------------------------------------------------------- //

(async () => {
  try {
    const { user, job } = await api("/api/me");
    showApp(user, job);
  } catch (_) {
    showAuth("login");
  }
})();
