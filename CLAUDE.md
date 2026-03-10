# AI News Agent

An automated agent that scrapes AI-related tweets from key researchers, labs, and news accounts twice a day, summarizes them using Claude (Anthropic API), and delivers a digest via Telegram bot.

## What This Project Does

- **Scrapes** tweets from ~19 configured Twitter/X accounts using Playwright browser automation (no API key needed)
- **Summarizes** the most important AI developments using the Claude API
- **Delivers** a formatted digest to a Telegram bot at 8am and 6pm (configurable)

## Project Structure

```
ai-news-agent/
├── CLAUDE.md           ← you are here
├── .env                ← secrets (never commit this)
├── .env.example        ← template for .env
├── config.py           ← accounts list, model, schedule times, prompts
├── scraper.py          ← Playwright Twitter scraper
├── summarizer.py       ← Claude API summarization
├── telegram_bot.py     ← Telegram delivery
├── scheduler.py        ← APScheduler twice-daily job
├── main.py             ← entry point
├── setup_session.py    ← one-time Twitter login helper
├── session/            ← twitter_auth.json lives here (gitignored)
└── logs/               ← agent.log (gitignored)
```

## Setup (run once)

### 1. Install dependencies
```bash
pip3 install -r requirements.txt
python3 -m playwright install chromium
```

### 2. Configure secrets
Copy `.env.example` to `.env` and fill in all values:
```bash
cp .env.example .env
```

Required values in `.env`:
- `ANTHROPIC_API_KEY` — from https://console.anthropic.com
- `TELEGRAM_BOT_TOKEN` — create a bot via @BotFather on Telegram
- `TELEGRAM_CHAT_ID` — get your chat ID from @userinfobot on Telegram
- `TWITTER_USERNAME` — your Twitter/X email or handle
- `TWITTER_PASSWORD` — your Twitter/X password
- `SCHEDULE_TIMES` — default: `08:00,18:00`
- `TIMEZONE` — default: `America/New_York`

### 3. Log into Twitter (one-time, manual)
```bash
python3 setup_session.py
```
This opens a real browser window. Log in manually. Handle any 2FA/CAPTCHA yourself. Session cookies are saved to `session/twitter_auth.json` and reused on every future run.

**Re-run this command whenever the session expires** (Twitter logs you out every few weeks). The agent will send a Telegram alert telling you when this is needed.

## Running the Agent

### Test a single digest immediately
```bash
python3 main.py --run-now
```
Takes ~3-5 minutes to scrape all accounts. A digest will arrive in Telegram when done.

### Start scheduled operation (twice daily)
```bash
python3 main.py
```
Blocks and runs at the times configured in `SCHEDULE_TIMES`. Keep alive with `screen`, `tmux`, or a systemd service.

### Check logs
```bash
tail -f logs/agent.log
```

## Common Tasks

### Add or remove Twitter accounts
Edit the `TWITTER_ACCOUNTS` dict in `config.py`. Three categories: `researchers`, `labs`, `news`.

### Change schedule times
Edit `SCHEDULE_TIMES` in `.env`. Format: `HH:MM,HH:MM` (24h, comma-separated). Default: `08:00,18:00`.

### Change timezone
Edit `TIMEZONE` in `.env`. Uses pytz names e.g. `Europe/London`, `Asia/Tokyo`, `US/Pacific`.

### Switch to a smarter (but slower/pricier) Claude model
In `config.py`, change `CLAUDE_MODEL` to `claude-sonnet-4-5-20250929`.

### Adjust how many tweets are collected per account
In `config.py`: `MAX_TWEETS_PER_ACCOUNT` (default 10) and `TWEET_LOOKBACK_HOURS` (default 12).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Session expired" Telegram alert | Run `python3 setup_session.py` again |
| No digest arrives | Run `python3 main.py --run-now` and check `logs/agent.log` |
| Zero tweets collected | Check if session is valid; Twitter may have changed their layout |
| Telegram error | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` |
| `playwright install` needed | Run `playwright install chromium` |

## Architecture Notes

- **Single browser page** is reused across all accounts (preserves session, faster)
- **`data-testid` selectors** are used (more stable than obfuscated CSS class names)
- **Images/videos are blocked** via `page.route()` for ~60% faster page loads
- **Anti-bot measures**: randomized delays (800ms–6s), real Chrome User-Agent, `--disable-blink-features=AutomationControlled`
- **Session stored as JSON** (`storage_state`) — cookies + localStorage
- **Error isolation**: if one account fails to scrape, the run continues with the others
- **Session expiry detection**: both pre-run (cookie timestamp check) and mid-run (login redirect detection)

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic API key |
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | — | Your Telegram chat/user ID |
| `TWITTER_USERNAME` | ✅ | — | Twitter email or handle (for setup_session.py) |
| `TWITTER_PASSWORD` | ✅ | — | Twitter password (for setup_session.py) |
| `SESSION_FILE` | ❌ | `session/twitter_auth.json` | Path to saved session |
| `SCHEDULE_TIMES` | ❌ | `08:00,18:00` | Comma-separated HH:MM run times |
| `TIMEZONE` | ❌ | `America/New_York` | pytz timezone name |
