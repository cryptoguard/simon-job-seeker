# Sacrée Soirée Contract Monitor

Checks the two contract listing pages on `portail.sacreesoiree.com` every 5
minutes between 8 AM and 6 PM Eastern and sends a Telegram alert when a new
contract appears. Runs on free GitHub Actions cron.

## How it works

1. `monitor.py` logs into the portal, scrapes the per-session `sasokey`
   token from the page HTML, and calls the JSON API for both listings:
   - `get/contrats/placement/excludebooke` (ponctuels)
   - `get/contrats/eventss/excludebooke` (événements)
2. Contract IDs get diffed against `seen.json`. Each new ID becomes a
   Telegram message, then gets appended to `seen.json`.
3. The workflow commits `seen.json` back to the repo so state survives
   between runs.

## One-time setup

### 1. Telegram bot

1. In Telegram, message **`@BotFather`**, then `/newbot`. Pick any name and
   a username ending in `bot`. BotFather replies with a token like
   `123456:ABC-DEF...`.
2. Message **`@userinfobot`** and it replies with your numeric user ID.
3. Open a chat with your new bot and send any message (e.g. "hi"). This
   authorizes the bot to message you.

### 2. GitHub repo

Use a **public** repo. Private repos on the free plan get 2,000 Actions
minutes per month, and this cron exceeds that within the first week.

```bash
cd /home/mint/Applications/simon-job-seeker
git init
git add .
git commit -m "initial: contract availability monitor"
git branch -M main
git remote add origin git@github.com:<you>/contract-monitor.git
git push -u origin main
```

### 3. Repo secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add four secrets:

| Name                 | Value                                |
|----------------------|--------------------------------------|
| `PORTAL_EMAIL`       | your portal email                    |
| `PORTAL_PASSWORD`    | your portal password                 |
| `TELEGRAM_BOT_TOKEN` | from `@BotFather`                    |
| `TELEGRAM_CHAT_ID`   | numeric chat ID from `@userinfobot`  |

### 4. First run

In GitHub: **Actions → contract-monitor → Run workflow**. Watch the run
log:

- `Outside window` means you triggered outside 8 AM to 6 PM ET. Tick the
  **Bypass the 8am-6pm ET window check** toggle on the Run workflow form
  to force a run any time of day.
- A successful run prints `[placement] N listed, N new` and `[eventss] M
  listed, M new`. Since `seen.json` starts empty, every currently listed
  contract gets a Telegram message on the first run. After that, only new
  contracts trigger alerts.

The schedule takes over from there: every 5 minutes during the daytime
window.

## Local testing

```bash
cd /home/mint/Applications/simon-job-seeker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Either export the four env vars, or drop them in a gitignored .env and
# run `set -a && source .env && set +a` before the script.

FORCE_RUN=1 python monitor.py
```

`FORCE_RUN=1` bypasses the time-window check so you can test outside
working hours. To re-trigger an alert for an existing contract while
testing, remove its ID from `seen.json` and re-run.

## Files

- `monitor.py`: the script.
- `.github/workflows/check.yml`: cron schedule and run job.
- `seen.json`: committed list of already-alerted contract IDs, capped at
  1,000 per page.
- `requirements.txt`: just `requests`.

## Limitations

- GitHub Actions cron is best-effort. Runs can lag 1 to 15 minutes during
  peak load. If you need tighter timing, port to Cloudflare Workers; its
  free tier supports 1-minute cron with better punctuality.
- The portal API returns JSON prefixed with a UTF-8 BOM, which trips
  `requests.json()`. The script decodes with `utf-8-sig` to work around
  it. If the portal ever drops the BOM, nothing breaks.
- Alert text uses the fields `poste`, `adresse`, and one of
  `jour`/`date`/`heure`. Missing fields get skipped from the message body
  but the alert still fires with whatever data exists, and the contract
  page URL is always included.
