# The website (alongside the Telegram bot)

Friends can use a web page instead of Telegram: log in with their roll number +
SAP password, press **Check my attendance**, type the CAPTCHA shown on the page,
and read the result. The Telegram bot keeps working; both share one process, one
user store and one browser-at-a-time lock.

A friend already registered on Telegram can log in on the site with the same roll
number and SAP password — it's the same record.

## Run it locally first (no VPS needed)

```bash
uv run python -m attendance web
```

Open http://127.0.0.1:8080. Locally the browser window pops up on your screen
(headed), exactly like `attendance run`. Set `WEB_COOKIE_SECURE=0` in `.env` only
if login doesn't stick on plain http.

## Turn it on in production

In `/opt/attendance-tracker/.env`:

```
WEB_PORT=8080
WEB_HOST=127.0.0.1        # see "Reaching it" below
WEB_TRUST_PROXY=1         # only when a proxy (Traefik) is in front
```

Then `git pull && uv sync && sudo systemctl restart attendance-bot`. The log shows
`website on http://...`. Without `WEB_PORT` the site is off.

## It MUST be served over HTTPS

The site receives SAP passwords. Over plain http they cross the internet in clear
text, and the session cookie is `Secure` so login won't even stick. Never open
port 8080 to the internet; put TLS in front and let only the proxy reach it.

## Reaching it (Traefik on this VPS)

n8n and the Hub are fronted by a Traefik container, and the firewall allows only
22/80/443. The bot runs on the host (systemd), not in Docker, so Traefik has to
call out to the host. **This part is specific to the box and has not been
exercised** — do it step by step:

1. Find the host address Docker containers see:
   `docker network inspect n8n_default -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}'`
2. Set `WEB_HOST` to that address (so the site listens only on the Docker
   bridge, not the public interface), restart the bot.
3. Allow just that subnet to port 8080 in ufw
   (`ufw allow from <subnet> to any port 8080 proto tcp`).
4. Add a Traefik route for a hostname such as
   `attendance.srv1247302.hstgr.cloud` → `http://<gateway>:8080` (file provider
   or labels, whichever the Traefik there already uses), with the same
   `certresolver` the Hub uses.
5. Confirm `https://attendance.…/` loads and `http://` redirects to it.

Check from outside that `http://193.203.160.36:8080` does **not** answer.

## Security notes

- Login is the roll number + SAP password, checked against the encrypted store.
  Wrong guesses are throttled per (address, roll number) and per address; sessions
  are server-side, `HttpOnly`, `SameSite=Strict`, revocable (logout, password
  change). Restarting the bot signs everyone out.
- A registered roll number cannot be re-registered by someone else; changes need
  a login. The invite code gates registration, with its own throttle.
- The CAPTCHA is always typed by the person. Nothing solves or bypasses it.
- Same custodianship caveat as the bot: you hold friends' SAP passwords
  (encrypted, key on the same box). See DEPLOY.md.
- Checks run one at a time; others see "Waiting for another check to finish...".
