# Polished Rewrite Notes

This package keeps the vanity hunting bot separate from your main bot and includes the requested professional logging/stat systems.

## Included systems

- Cross-server list update notifications after manual checks and watch checks.
- Auto-updating hunter leaderboard embed.
- Best claim of the week shown at the top of the leaderboard.
- Top 10 hunters with total claims, total attempts, and most valuable claim.
- Member claim logging with `/hunter_claim`.
- Manager value updates for already logged claims using `/claim_value_set` or `/claim_value_by_id`.
- Value/cut calculation with hunter cut and owner/server cut.
- Separate log channels for hunter claims and manager value updates.
- Auto `elite hunter` role after 10+ logged claims.
- Public info embeds for managers, elite hunter, and the vanity job.

## Most important commands

- `/vanity_setup` — set result channels and ping roles.
- `/list_update_setup` — set the other server/channel for list update alerts.
- `/leaderboard_setup` — create the auto-updating leaderboard.
- `/hunter_setup` — set member claim log channel.
- `/hunter_claim` — member logs vanity, date, total tried, and notes.
- `/claim_value_set` — manager adds value/cut to a member's existing claim by hunter + vanity.
- `/claim_value_by_id` — manager adds value/cut to an existing claim by claim ID.
- `/hunter_history` — find claim IDs and recent claims.
- `/hunter_leaderboard` — manually post the current leaderboard.
