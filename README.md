# Vanity Bot Production Package

## Main features
- Payment required before claims.
- Claim modal only asks for vanity code/link.
- Claims must be valid invites and require manager approval.
- Dynamic Get Roles button based on autorole settings.
- Manager role explanation: earned by being trusted/helpful.
- Payout calculator and payout info.
- Owner-favored payout tiers:
  - 0–4 approved claims: 30%
  - 5–14 approved claims: 35%
  - 15–24 approved claims: 40%
  - 25+ approved claims: 45%
- Two-server list checker with copyable invalid text files.
- Change logs for invalid → valid and valid → invalid.

## Start command
python bot.py

## Railway variables
TOKEN=your_token
DATA_DIR=/app/data
HUNTER_ROLE_ID=your_hunter_role_id
MANAGER_ROLE_ID=your_manager_role_id

Use a Railway volume mounted to /app/data if you want data to persist.


## Detailed payout info + manager applications
- Hunter panel includes detailed Payout Info.
- Hunter panel includes Apply For Manager.
- Hunter panel includes Manager Role Info.
- Manager application posts an embed to the configured channel.
- Owners can approve/deny using buttons.
- Approval gives manager role and DMs the applicant.
- Denial opens a reason modal, DMs the applicant, and logs the application as denied.

## New setup commands
- `/set_manager_application_channel`
- `/set_manager_role`
- `/manager_applications`


## Leaderboard
- `/leaderboard` shows the current top 10 privately.
- `/post_leaderboard` posts the top 10 leaderboard in the current or chosen channel.
- `/leaderboard_update` forces the posted leaderboard to update.
- Posted leaderboard auto-updates every 3 hours.
- Rankings are based only on approved/sold/paid claims.


## Added setup wizard + pending claim review buttons
- `/setup_wizard` opens a guided setup panel.
- Setup wizard can configure:
  - pending claim channel
  - approved claim log channel
  - leaderboard channel
  - manager role
  - manager application channel
  - claim cooldown / max claims per hour
  - post hunter/manager panels
- Pending claim embeds now include manager buttons:
  - Approve
  - Deny
  - Set Buyer / Status


## Checker status commands
- `/checker_status` shows all running checker lists.
- `/checker_toggle list_name enabled:true/false` enables or disables a checker list.


## Invite validation fix
- Invite validation now checks with discord.py first.
- If that fails, it checks Discord's public invite API.
- If Discord's API is uncertain/rate-limited, the claim is allowed as pending but marked for manager review.
- Confirmed invalid invites are still blocked.
