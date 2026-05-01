# Claim-Only Vanity Hunter Bot + Auto List Checker

## Setup
1. Upload the files to GitHub/Railway.
2. Set your bot token:
   - `TOKEN=your_bot_token`
3. For persistent data on Railway, add a volume mounted to `/app/data` and set:
   - `DATA_DIR=/app/data`
4. Start command:
   - `python bot.py`

## Main panels
- `/hunter_panel` — private hunter panel
- `/manager_panel` — private manager panel
- `/post_hunter_panel` — post public hunter panel
- `/post_manager_panel` — post public manager panel
- `/help` — help center
- `/post_help_panel` — public help panel

## Claim-only system
This version tracks claims only. Attempts/no-pulls/rate-limit sessions are not tracked.

Managers can set:
- claim log channel
- claim ping role
- leaderboard channel
- claim cooldown
- max claims per hour
- multiple claim-count autoroles

Managers are also responsible for helping find buyers for valuable claimed vanities.

## Auto vanity list checker
This version also supports automatic list checks, including channels in another server as long as the bot is in that server and can send messages there.

Commands:
- `/vanity_list_add name words replace_existing:false`
- `/vanity_list_setup name valid_channel invalid_channel interval_minutes ping_role delay_seconds max_per_run`
- `/vanity_list_run name`
- `/vanity_lists`
- `/vanity_list_enable name`
- `/vanity_list_disable name`
- `/vanity_list_remove name`

How it works:
- Valid/became-valid results go to the valid channel.
- Invalid vanities go to the invalid channel.
- Invalid vanities are grouped into auto-updating embeds by length.
- Example titles: `3 lettered vanities`, `4 lettered vanities`, `5 lettered vanities`.
- If a vanity becomes valid again, it is removed from the invalid embed automatically.
- Each list can have its own optional ping role.
- Multiple lists can be saved and checked independently.

## Important permissions
The bot needs:
- Send Messages
- Embed Links
- Read Message History
- Manage Roles, only if using autoroles

For cross-server list posts, invite the bot to both servers and choose channels the bot can access.
