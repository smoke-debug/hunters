# Claim-Only Vanity Hunter Bot

This version tracks successful vanity claims only. Attempts/no-pulls are removed.

## Main commands
- `/hunter_panel` - private hunter claim panel
- `/manager_panel` - private manager panel
- `/post_hunter_panel` - post the public hunter panel
- `/post_manager_panel` - post the public manager panel
- `/vanity_access_add_user` - give manager access
- `/vanity_access_add_role` - give manager access to a role

## Manager panel features
- Value claims by Claim ID
- See recent claims
- Setup claim log channel, value log channel, leaderboard channel, ping role, cooldown, and max claims per hour
- Add/remove multiple autoroles based on claim counts

## Anti-spam
The bot blocks:
- Duplicate vanity claims
- Claim logs too close together
- Too many claims in one hour

## Persistent data
Saved files:
- `data/config.json`
- `data/claims.json`
- `data/value_logs.json`

For Railway, use a persistent volume mounted to `/app/data` and set:

```env
DATA_DIR=/app/data
```
