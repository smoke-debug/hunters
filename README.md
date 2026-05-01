# Claim-Only Vanity Hunter Bot

This package is claim-only. It does not track attempts, no-pulls, or rate-limit sessions.

## Main commands
- `/help` - opens the new help center with buttons
- `/post_help_panel` - posts the public help panel
- `/hunter_panel` - opens a private hunter claim panel
- `/post_hunter_panel` - posts a public hunter claim panel
- `/manager_panel` - opens manager tools
- `/post_manager_panel` - posts manager tools
- `/vanity_access_add_user` - give manager access to a user
- `/vanity_access_add_role` - give manager access to a role

## Recommended Railway storage
Add a Railway volume mounted at:

`/app/data`

Then add this variable:

`DATA_DIR=/app/data`

This keeps claims, settings, autoroles, values, and leaderboard data after redeploys.

## Recommended anti-spam settings
- Cooldown: 60 seconds
- Max claims/hour: 6
- Duplicate claim blocking: always enabled

## Recommended autoroles
- 3 claims = Trial Hunter
- 10 claims = Elite Hunter
- 25 claims = Senior Hunter
- 50 claims = Top Hunter
