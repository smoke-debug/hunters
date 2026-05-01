# Vanity Hunter Bot — Full Separate Package

This is the standalone vanity hunting bot. It is separate from your main community bot.

## What it does

- Checks Discord vanity/invite codes with `/vanity_check` and saved lists.
- Saves invalid/available targets into `data/invalid_vanities/` by code length.
- Can auto-watch saved lists on a schedule.
- Sends normal check results to your configured valid/invalid channels.
- Sends a clean cross-server update embed to another server/channel after checks finish.
- Lets members log claimed vanities with `/hunter_claim`.
- Lets managers add values/cuts to already logged member claims.
- Tracks total claims, attempts, best claim, total value, and calculated cuts.
- Auto-gives the `elite hunter` role once a member reaches 10+ logged claims.
- Maintains an auto-updating leaderboard embed.

## Files

- `bot.py` — main bot code
- `.env.example` — environment variable example
- `requirements.txt` — dependencies
- `data/` — storage folder created/used by the bot

## Setup

1. Create a Discord application and bot in the Discord Developer Portal.
2. Enable these bot intents:
   - Server Members Intent
3. Invite the bot to every server it needs to post in.
4. Make sure it has:
   - Send Messages
   - Embed Links
   - Manage Roles, only if you want auto `elite hunter` role assignment
   - Mention Everyone, only if you want cross-server `@everyone` update pings
5. Copy `.env.example` to `.env` and put your token:

```env
TOKEN=your_bot_token_here
CHECK_DELAY=3
WATCH_INTERVAL_MINUTES=10
LEADERBOARD_REFRESH_MINUTES=10
MAX_MANUAL_CODES=1000
MAX_LIST_CODES=2500
```

6. Install requirements:

```bash
pip install -r requirements.txt
```

7. Run:

```bash
python bot.py
```

## Main setup commands

### Result channels

```text
/vanity_setup valid_channel:#valid-results invalid_channel:#target-results ping_roles:@role delay_seconds:3
```

### Cross-server list update alerts

Use this in your main/server where checks are run:

```text
/list_update_setup target_channel_id:123456789012345678 ping_everyone:true
```

The target channel can be in another server, but the bot must be in that server and have permission to send messages there.

After checks finish, the bot sends a clean embed saying the lists were updated and includes fresh targets/list counts. If enabled, it pings everyone with:

```text
@everyone lists updated, make sure to go attempt
```

### Auto-updated leaderboard

```text
/leaderboard_setup channel:#leaderboard refresh_minutes:10
```

The leaderboard shows:

- Top 10 users by claims
- Total attempts
- Each user’s most valuable claim
- Server totals
- Best claim of the week at the top

## Member claim command

Members use:

```text
/hunter_claim vanity:prey claimed_date:2026-05-01 total_tried:250 notes:claimed from updated list
```

This logs:

- Vanity
- Date claimed
- Total vanities tried
- Notes

It updates their stats automatically.

## Manager value commands

Managers can add value to claims after the member logs them.

By hunter + vanity:

```text
/claim_value_set hunter:@user vanity:prey value:$50 cut_percent:40 notes:sold/pending payout
```

By claim ID:

```text
/claim_value_by_id claim_id:1710000000000 value:$50 cut_percent:40 notes:sold/pending payout
```

The bot calculates:

- Vanity value
- Hunter cut
- Owner/server cut
- Updates the leaderboard
- Logs the value update embed

Example:

```text
Value: $50
Cut: 40%
Hunter cut: $20
Owner/server cut: $30
```

## Useful commands

```text
/vanity_help
/vanity_add_list
/vanity_lists
/vanity_run_list
/vanity_watch_start
/vanity_watch_stop
/vanity_watches
/hunter_setup
/hunter_claim
/hunter_history
/hunter_stats
/hunter_leaderboard
/value_setup
/value_history
/info_roles
/info_vanity_job
```

## Notes

- The bot stores data in JSON files inside `data/`.
- Do not delete `data/` unless you want to reset lists, claims, values, and stats.
- If slash commands do not show, restart the bot and wait a few minutes.
- For the `elite hunter` role, create a role named exactly `elite hunter` or `Elite Hunter`.

## Updated Info + Help Commands

This rewrite expands the public info commands and adds a beginner-friendly help command.

### Public member commands
- `/help` — beginner guide for new hunters.
- `/vanity_help` — full command menu for members and managers.
- `/info_vanity_job` — detailed vanity hunting job guide with step-by-step instructions.
- `/info_roles` — shows Manager and Elite Hunter info in one clean command. Use the role parameter to view `manager`, `elite_hunter`, or `all`.

### How hunters should log claims
After successfully claiming a vanity, hunters should use:

`/hunter_claim vanity:<code> date:<YYYY-MM-DD> attempts:<number> notes:<short context>`

Example:

`/hunter_claim vanity:rare date:2026-05-01 attempts:275 notes:claimed from updated short list`

Managers can later add a value/cut to the already logged claim with `/claim_value_set` or `/claim_value_by_id`.
