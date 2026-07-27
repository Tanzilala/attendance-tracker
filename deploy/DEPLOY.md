# Deploying the attendance bot to a VPS (laptop-off operation)

Goal: run `attendance listen` 24/7 on your Hostinger VPS so you can message the
bot `/check` from anywhere — laptop off — solve the CAPTCHA on your phone, and
get your attendance back.

**Why a virtual display?** The SVKM portal serves an IE-emulation codepath that
only renders in a *visible* browser; headless Chromium fails. On a server with
no screen we run a headed browser inside `xvfb` (X virtual framebuffer). You
never see it — the CAPTCHA still comes to your phone.

Assumes Ubuntu/Debian (typical Hostinger). Run as a non-root user where possible;
adjust paths if you use root.

---

## 1. System packages

```bash
sudo apt update
sudo apt install -y xvfb git curl
```

## 2. Get the code onto the VPS

Either clone your repo, or copy the folder up with `scp`. Target `/opt/attendance-tracker`:

```bash
sudo mkdir -p /opt/attendance-tracker
sudo chown "$USER" /opt/attendance-tracker
# from your laptop:  scp -r attendance-tracker/* user@vps:/opt/attendance-tracker/
```

Do **not** copy `.env`, `auth_state.json`, `downloads/`, or `run-*`/`*-diagnostic`
files — they are gitignored and machine-specific. You'll make a fresh `.env` in step 5.

## 3. Install uv (if not already present)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# reopen the shell, or:  source ~/.local/bin/env
which uv    # note this path — used by run-listen.sh's PATH
```

## 4. Install dependencies + the browser

```bash
cd /opt/attendance-tracker
uv sync
uv run playwright install --with-deps chromium   # installs Chromium + its OS libs
```

## 5. Create the .env

```bash
cp .env.example .env
nano .env
```

Fill in `SAP_USERNAME`, `SAP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`CLASSES_PER_DAY`. Then lock it down so only you can read it:

```bash
chmod 600 .env
```

## 6. Smoke test by hand (inside the window, 6 PM - 7 AM)

```bash
chmod +x deploy/run-listen.sh
./deploy/run-listen.sh
```

You should get "Attendance bot online" on Telegram. Send `/check`, solve the
CAPTCHA on your phone, and confirm the result comes back. Ctrl+C to stop.

If the browser fails to launch, you're missing a library: rerun
`uv run playwright install --with-deps chromium`.

## 7. Install as a service (runs on boot, restarts on crash)

Edit `deploy/attendance-bot.service`: set `User=` to your VPS username and fix
the two paths if you didn't use `/opt/attendance-tracker` / `/home/<user>`. Then:

```bash
sudo cp deploy/attendance-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now attendance-bot
```

Watch it:

```bash
systemctl status attendance-bot
journalctl -u attendance-bot -f      # live logs
```

## 8. Use it

Message your bot `/check` any time between **6 PM and 7 AM**. Outside that window
it replies that it's skipping. You solve the CAPTCHA on your phone; the result
follows.

---

## Operating notes

- **Updating the code:** copy the new files up, then
  `sudo systemctl restart attendance-bot`.
- **One check at a time:** the bot runs checks serially; a `/check` sent while
  another is running is ignored until the first finishes.
- **Credentials live on the VPS** in `.env` (chmod 600). Treat the box
  accordingly. The Telegram bot only obeys your configured `TELEGRAM_CHAT_ID`, so
  no one else can trigger it even if they find the bot.
- **Resource use:** idle, it just long-polls Telegram (negligible). During a
  check it launches one Chromium under xvfb for ~30-60s, then closes it.
- **Time zone:** the 6 PM - 7 AM check uses the VPS clock. Confirm the VPS is on
  IST (`timedatectl`) or the window will be wrong.
