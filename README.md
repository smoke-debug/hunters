# Final Production Vanity Bot Package

## Features
- Claim-only hunter system.
- Hunters must set payment before submitting claims.
- `/set_payment`, `/edit_payment`, `/delete_payment`, `/view_payments`.
- Hunter Panel with Log Claim, My Stats, Info.
- Manager Panel with claim edit/status/delete, notes, settings, autoroles, guide.
- Pending claim review flow:
  - Hunters submit claim.
  - Claim stays pending.
  - Manager approves/denies.
  - Approved claims post publicly.
  - Denied claims do not post publicly.
  - Hunter gets DM on approve/deny.
- Valid-only claims:
  - The bot checks `discord.gg/code`.
  - If the invite is not currently valid, the claim is blocked.
- Autoroles based on approved claim count.
- Auto DM guide when Hunter or Manager role is added if you set role IDs in env.
- Two-server checker:
  - Private checker server stores/runs lists.
  - Vanity Hunters server receives invalid-list embeds.
  - Old invalid embeds are deleted/replaced.
  - Copyable `.txt` attachments are sent with each invalid-list embed.
  - Separate change-log channel can show Invalid → Valid and Valid → Invalid.

## Railway Setup
Set variables:
```env
TOKEN=your_token
DATA_DIR=/app/data
CLAIM_COOLDOWN_SECONDS=60
MAX_CLAIMS_PER_HOUR=5
CHECK_DELAY_SECONDS=3
HUNTER_ROLE_ID=your_hunter_role_id
MANAGER_ROLE_ID=your_manager_role_id
```

Recommended Railway volume:
- Mount path: `/app/data`
- Variable: `DATA_DIR=/app/data`

## Start command
```bash
python bot.py
```

## First-time Discord Setup
1. Invite bot with:
   - `bot`
   - `applications.commands`
2. Bot permissions needed:
   - Send Messages
   - Embed Links
   - Attach Files
   - Read Message History
   - Manage Roles, if using autoroles
   - Mention Roles, if checker pings a role
3. Run:
   - `/vanity_manager_add_role` or `/vanity_manager_add_user`
   - `/post_hunter_panel`
   - `/post_manager_panel`
4. In Manager Panel → Settings:
   - set pending claim review channel
   - set approved claim log channel

## Checker Setup
In your private checker server:
1. `/checker_add_list`
2. `/two_server_list_setup`
   - `hunters_invalid_channel_id`: channel in Vanity Hunters server
   - `hunters_valid_channel_id`: optional valid summary channel
   - `change_log_channel_id`: optional separate flip log channel
   - `ping_role_id`: optional role in Vanity Hunters server
3. `/checker_run_now`

## Important
Hunters cannot submit claims until they run `/set_payment`.
