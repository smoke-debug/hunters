# Vanity Hunter Bot — Separate Simple Bot

This package is a separate bot just for vanity hunting/checking. It is simpler than the old mixed-in cog.

## Commands

### First setup
- `/vanity_setup` — set valid result channel, invalid target channel, optional ping roles, and delay.
- `/vanity_access_add_user` — allow a worker to use commands.
- `/vanity_access_add_role` — allow a role to use commands.
- `/vanity_help` — show command help in Discord.

### Checking
- `/vanity_check codes: prey, mine, shop` — quick manual check.
- `/vanity_add_list name: short codes: prey,mine,shop` — save words to a list.
- `/vanity_lists` — show saved lists.
- `/vanity_run_list name: short` — check a saved list.
- `/vanity_stop` — stop the current run after the current check finishes.

### Auto checking
- `/vanity_watch_start name: short interval_minutes: 10` — auto-check a saved list.
- `/vanity_watch_stop name: short` — stop auto-checking a list.
- `/vanity_watches` — show active watches.

### Files
- `/vanity_files` — show saved invalid counts.
- Invalid targets save to `data/invalid_vanities/invalid_#_letters.txt` grouped by length.

## Setup guide

1. Create a new Discord Application/Bot for this vanity bot.
2. Copy the bot token.
3. Invite it with:
   - Send Messages
   - Embed Links
   - Read Message History
   - Use Slash Commands
   - Mention Roles, if using ping alerts
4. Add host/Railway variables:

```env
TOKEN=your_vanity_bot_token_here
CHECK_DELAY=3
WATCH_INTERVAL_MINUTES=10
```

5. Install requirements:

```bash
pip install -r requirements.txt
```

6. Run:

```bash
python bot.py
```

7. In Discord, run:

```text
/vanity_setup
```

Keep `delay_seconds` at `3` or higher to reduce rate-limit issues.

## Railway start command

```bash
python bot.py
```

## Optional advanced backup

`advanced_original_vanity_hunter_cog.py` is included only as a backup/reference from your original package. The simple separate bot uses `bot.py`.
