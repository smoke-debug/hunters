# Claim-Only Vanity Bot + Private Checker -> Vanity Hunters Updates

This version keeps the claim-only hunter system and adds the two-server list checker flow:

- Run/check saved vanity lists inside your private checker server.
- Post fresh invalid-list embeds into your Vanity Hunters server.
- Ping a role from the Vanity Hunters server.
- Send valid/became-valid results to a separate channel.
- Delete the previous list update after the new one posts, so workers only see the newest list.
- Prevent duplicate vanity entries by de-duping saved lists and current invalid state.

## Required Discord permissions

Invite the bot to BOTH servers.

In the private checker server, the bot needs slash command access.

In the Vanity Hunters server, the bot needs:
- View Channel
- Send Messages
- Embed Links
- Mention Roles
- Read Message History
- Manage Messages is recommended if you want it to delete older bot messages reliably

## Setup flow

1. In the private checker server, add a list:

`/vanity_list_add name:3letters words:abc, def, make, ...`

2. Copy IDs from the Vanity Hunters server:

- Copy the channel ID where fresh invalid list embeds should go.
- Copy the role ID you want pinged.

3. Run setup in the private checker server:

`/vanity_list_setup`

Use:
- `valid_channel_id` = channel for valid/became-valid results
- `hunter_invalid_channel_id` = channel in Vanity Hunters where fresh invalid lists should post
- `hunter_ping_role_id` = optional role ID from Vanity Hunters

4. Run it manually:

`/vanity_list_run name:3letters`

Or let it auto-run based on the interval.

## What workers see

The Vanity Hunters server receives:

- A summary embed showing checked count, current invalid count, and how many went invalid -> valid.
- Separate fresh embeds titled like `3 lettered vanities`, `4 lettered vanities`, etc.

Old list messages are deleted only after the new update successfully posts.
