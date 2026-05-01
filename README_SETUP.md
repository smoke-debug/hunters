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


## Member Claim Logging

This package now includes a simple member claim log system.

### Setup the claim log channel
Run this once as an admin or someone with Manage Server:

```text
/claim_setup log_channel:#claimed-vanities
```

### Let members log a claimed vanity
Any server member can run:

```text
/claim_log vanity:prey claimed_date:2026-04-30 source:manual hunt status:Claimed value:100 notes:claimed after checking drops
```

What it records:
- vanity code/link
- member who claimed it
- claimed date
- source/method
- status: Claimed, Holding, Sold, Pending, Lost
- optional value/sold amount
- notes/proof/context

The bot posts a clean embed in the claim log channel and stores the record in:

```text
data/claims.json
```

### View recent claims
Vanity managers/admins can run:

```text
/claim_history
/claim_history user:@member limit:10
```


## Updated Claim Logging
Members now use `/claim_log` with only:
- `vanity`
- `claimed_date` in `YYYY-MM-DD` format
- `total_tried`
- `notes`

Each claim updates that member's stats in `data/claim_stats.json`. At 10+ total claims, the bot automatically tries to give the member the role named `elite hunter` or `Elite Hunter`. Create that role in your server before hunters reach 10 claims.

Useful public info commands:
- `/info_manager`
- `/info_elite_hunter`
- `/info_vanity_job`

Stats commands:
- `/claim_stats`
- `/claim_leaderboard`
- `/claim_history` manager/admin only
