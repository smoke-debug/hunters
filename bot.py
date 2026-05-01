import os
import json
import time
import random
import re
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks
from discord import app_commands

TOKEN = os.getenv("TOKEN")
TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")

DATA_FILE = "activity_data.json"
GIVEAWAYS_FILE = "giveaways.json"
INVITE_TRACK_FILE = "invite_join_tracking.json"
INVITE_STATS_FILE = "invite_stats.json"
VC_ROLE_REWARDS_FILE = "vc_role_rewards.json"
CONFIG_FILE = "config.json"
AUTORESPONDERS_FILE = "autoresponders.json"
WARNINGS_FILE = "warnings.json"
ANTINUKE_FILE = "antinuke.json"
REACTION_ROLES_FILE = "reaction_roles.json"
FAKEPERMS_FILE = "fakeperms.json"
TEMPVC_FILE = "tempvc.json"
POINTS_FILE = "points.json"
LEVEL_COOLDOWN_SECONDS = 45


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.guilds = True
intents.presences = True

bot = commands.Bot(command_prefix=os.getenv("PREFIX", "*"), intents=intents, help_command=None)
active_vc_sessions: dict[tuple[int, int], int] = {}
invite_cache: dict[int, dict[str, dict]] = {}
bot_start_time = int(time.time())
last_xp_gain: dict[tuple[int, int], int] = {}
point_message_cooldown: dict[tuple[int, int], int] = {}
antinuke_recent_actions: dict[tuple[int, int, str], list[int]] = {}
snipes: dict[int, dict] = {}
editsnipes: dict[int, dict] = {}
blacktea_games: set[int] = set()

DEFAULT_THEME_COLOR = discord.Color.from_rgb(43, 45, 49)
SUCCESS_COLOR = discord.Color.from_rgb(46, 204, 113)
ERROR_COLOR = discord.Color.from_rgb(231, 76, 60)
WARNING_COLOR = discord.Color.from_rgb(241, 196, 15)



# ---------- JSON HELPERS ----------
def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_activity_data():
    return load_json(DATA_FILE, {})


def save_activity_data(data):
    save_json(DATA_FILE, data)


def load_giveaways():
    return load_json(GIVEAWAYS_FILE, {})


def save_giveaways(data):
    save_json(GIVEAWAYS_FILE, data)


def load_invite_tracking():
    return load_json(INVITE_TRACK_FILE, {})


def save_invite_tracking(data):
    save_json(INVITE_TRACK_FILE, data)


def load_invite_stats():
    return load_json(INVITE_STATS_FILE, {})


def save_invite_stats(data):
    save_json(INVITE_STATS_FILE, data)


def load_vc_role_rewards():
    return load_json(VC_ROLE_REWARDS_FILE, {})


def save_vc_role_rewards(data):
    save_json(VC_ROLE_REWARDS_FILE, data)


def load_config():
    return load_json(CONFIG_FILE, {})


def save_config(data):
    save_json(CONFIG_FILE, data)


def load_autoresponders():
    return load_json(AUTORESPONDERS_FILE, {})


def save_autoresponders(data):
    save_json(AUTORESPONDERS_FILE, data)


def load_warnings():
    return load_json(WARNINGS_FILE, {})


def save_warnings(data):
    save_json(WARNINGS_FILE, data)


def load_antinuke():
    return load_json(ANTINUKE_FILE, {})


def save_antinuke(data):
    save_json(ANTINUKE_FILE, data)


def load_reaction_roles():
    return load_json(REACTION_ROLES_FILE, {})


def save_reaction_roles(data):
    save_json(REACTION_ROLES_FILE, data)


def load_fakeperms():
    return load_json(FAKEPERMS_FILE, {})


def save_fakeperms(data):
    save_json(FAKEPERMS_FILE, data)


def load_tempvc():
    return load_json(TEMPVC_FILE, {})


def save_tempvc(data):
    save_json(TEMPVC_FILE, data)


def load_points():
    return load_json(POINTS_FILE, {})


def save_points(data):
    save_json(POINTS_FILE, data)


def get_guild_config(guild_id: int) -> dict:
    config = load_config()
    return config.setdefault(str(guild_id), {})


def clean_embed(title: str, description: Optional[str] = None, color: Optional[discord.Color] = None) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color or DEFAULT_THEME_COLOR)
    embed.timestamp = discord.utils.utcnow()
    return embed


def safe_template(text: str, member: discord.Member) -> str:
    return (text
        .replace("{user}", member.mention)
        .replace("{user.name}", member.name)
        .replace("{user.mention}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{membercount}", str(member.guild.member_count or 0))
    )


def is_admin_or_mod(ctx: commands.Context) -> bool:
    perms = ctx.author.guild_permissions
    return bool(perms.administrator or perms.manage_guild or perms.manage_messages)


# ---------- FORMATTERS ----------
def parse_duration(duration: str) -> int:
    duration = duration.lower().replace(" ", "")
    matches = re.findall(r"(\d+)([smhd])", duration)
    if not matches:
        raise ValueError("Invalid duration format")

    total_seconds = 0
    for amount, unit in matches:
        amount = int(amount)
        if unit == "s":
            total_seconds += amount
        elif unit == "m":
            total_seconds += amount * 60
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "d":
            total_seconds += amount * 86400
    return total_seconds


def format_seconds(seconds: int) -> str:
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def format_uptime() -> str:
    return format_seconds(int(time.time()) - bot_start_time)


def format_requirement_lines(gw: dict) -> list[str]:
    lines: list[str] = []

    if gw.get("require_boosting"):
        lines.append("• Must currently be boosting this server")

    bonus = int(gw.get("booster_bonus_entries", 0))
    if bonus > 0:
        word = "entry" if bonus == 1 else "entries"
        lines.append(f"• Boosters get `+{bonus}` extra {word}")

    if gw.get("required_status_substring"):
        lines.append(f"• Must have status containing `{gw['required_status_substring']}` when entering")

    required_messages = int(gw.get("required_messages", 0))
    if required_messages > 0:
        lines.append(f"• `{required_messages}` messages since start")

    required_vc_seconds = int(gw.get("required_vc_seconds", 0))
    if required_vc_seconds > 0:
        lines.append(f"• `{format_seconds(required_vc_seconds)}` VC time since start")

    required_invites = int(gw.get("required_invites", 0))
    if required_invites > 0:
        lines.append(f"• `{required_invites}` invites gained since giveaway start")

    if gw.get("required_server_id"):
        lines.append(f"• Must be in server ID `{gw['required_server_id']}`")

    if gw.get("required_invite_code"):
        lines.append(f"• Must have joined with invite `{gw['required_invite_code']}`")

    if not lines:
        lines.append("• No entry requirements")

    return lines




# ---------- POINTS + SMOKE SHOP ----------
POINTS_MESSAGE_COOLDOWN_SECONDS = 60
POINTS_REPLY_COOLDOWN_SECONDS = 120
POINTS_ATTACHMENT_COOLDOWN_SECONDS = 300
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
WORK_COOLDOWN_SECONDS = 60 * 60
WEEKLY_COOLDOWN_SECONDS = 7 * 24 * 60 * 60
BEG_COOLDOWN_SECONDS = 30 * 60
SEARCH_COOLDOWN_SECONDS = 45 * 60
CRIME_COOLDOWN_SECONDS = 2 * 60 * 60
FISH_COOLDOWN_SECONDS = 60 * 60
TRIVIA_COOLDOWN_SECONDS = 20 * 60

DEFAULT_POINT_SETTINGS = {
    "chat_points": True,
    "reply_bonus": True,
    "attachment_bonus": True,
    "min_message_length": 8
}

DEFAULT_SHOP_ITEMS = {
    "nitro-basic": {"name": "Nitro Basic", "emoji": "💎", "price": 25000, "stock": "∞", "description": "Redeem for Nitro Basic when staff approves available stock."},
    "nitro": {"name": "Discord Nitro", "emoji": "🚀", "price": 60000, "stock": "∞", "description": "Redeem for full Discord Nitro when staff approves available stock."},
    "deco": {"name": "Decoration", "emoji": "🎨", "price": 35000, "stock": "∞", "description": "Redeem for an available Discord decoration."},
    "custom-role": {"name": "Custom Role", "emoji": "👑", "price": 15000, "stock": "∞", "description": "Redeem for a custom role or perk decided by staff."}
}

def ensure_points_guild(data: dict, guild_id: int):
    gid = str(guild_id)
    if gid not in data:
        data[gid] = {"users": {}, "shop_items": DEFAULT_SHOP_ITEMS.copy(), "settings": DEFAULT_POINT_SETTINGS.copy()}
    data[gid].setdefault("users", {})
    data[gid].setdefault("shop_items", DEFAULT_SHOP_ITEMS.copy())
    data[gid].setdefault("settings", DEFAULT_POINT_SETTINGS.copy())
    for key, value in DEFAULT_POINT_SETTINGS.items():
        data[gid]["settings"].setdefault(key, value)

def ensure_points_user(data: dict, guild_id: int, user_id: int):
    ensure_points_guild(data, guild_id)
    uid = str(user_id)
    users = data[str(guild_id)]["users"]
    if uid not in users:
        users[uid] = {
            "points": 0, "earned_total": 0, "spent_total": 0,
            "daily_last": 0, "work_last": 0, "weekly_last": 0,
            "beg_last": 0, "search_last": 0, "crime_last": 0, "fish_last": 0,
            "trivia_last": 0, "reply_bonus_last": 0, "attachment_bonus_last": 0
        }
    defaults = {
        "points": 0, "earned_total": 0, "spent_total": 0,
        "daily_last": 0, "work_last": 0, "weekly_last": 0,
        "beg_last": 0, "search_last": 0, "crime_last": 0, "fish_last": 0,
        "trivia_last": 0, "reply_bonus_last": 0, "attachment_bonus_last": 0
    }
    for key, value in defaults.items():
        users[uid].setdefault(key, value)

def get_points_record(guild_id: int, user_id: int) -> dict:
    data = load_points()
    ensure_points_user(data, guild_id, user_id)
    save_points(data)
    return data[str(guild_id)]["users"][str(user_id)]

def add_points(guild_id: int, user_id: int, amount: int) -> int:
    data = load_points()
    ensure_points_user(data, guild_id, user_id)
    rec = data[str(guild_id)]["users"][str(user_id)]
    rec["points"] = max(0, int(rec.get("points", 0)) + int(amount))
    if amount > 0:
        rec["earned_total"] = int(rec.get("earned_total", 0)) + int(amount)
    elif amount < 0:
        rec["spent_total"] = int(rec.get("spent_total", 0)) + abs(int(amount))
    save_points(data)
    return int(rec["points"])

def set_points(guild_id: int, user_id: int, amount: int) -> int:
    data = load_points()
    ensure_points_user(data, guild_id, user_id)
    data[str(guild_id)]["users"][str(user_id)]["points"] = max(0, int(amount))
    save_points(data)
    return max(0, int(amount))

def get_shop_items(guild_id: int) -> dict:
    data = load_points()
    ensure_points_guild(data, guild_id)
    changed = False
    for item_id, item in DEFAULT_SHOP_ITEMS.items():
        if item_id not in data[str(guild_id)]["shop_items"]:
            data[str(guild_id)]["shop_items"][item_id] = item
            changed = True
    if changed:
        save_points(data)
    return data[str(guild_id)]["shop_items"]

def build_smoke_shop_embed(guild: discord.Guild) -> discord.Embed:
    items = get_shop_items(guild.id)
    embed = discord.Embed(
        title="✦ SMOKE SHOP ✦",
        description=(
            "**Premium rewards you can earn by staying active.**\n"
            "Use `*buy <item_id>` when you have enough points. Staff handles manual rewards through tickets."
        ),
        color=discord.Color.from_rgb(255, 184, 77)
    )
    if bot.user:
        embed.set_author(name=f"{guild.name} Rewards Market", icon_url=bot.user.display_avatar.url)
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    lines = []
    for item_id in ["nitro-basic", "nitro", "deco", "custom-role"]:
        item = items.get(item_id)
        if not item:
            continue
        stock = item.get("stock", "∞")
        stock_text = "Unlimited" if str(stock) == "∞" else str(stock)
        lines.append(
            f"{item.get('emoji', '✦')} **{item.get('name', item_id)}**\n"
            f"╰ `ID: {item_id}`  •  `Price: {int(item.get('price', 0)):,}`  •  `Stock: {stock_text}`\n"
            f"> {item.get('description', 'Redeemable shop reward.')}"
        )
    embed.add_field(name="🏷️ Available Rewards", value="\n\n".join(lines)[:1024], inline=False)
    embed.add_field(
        name="⚡ Earn Points",
        value=(
            "`*daily` daily drop • `*weekly` weekly drop • `*work` hourly job\n"
            "`*beg` quick chance • `*search` scavenger hunt • `*fish` fishing\n"
            "`*crime` risky payout • chatting, replying, and media bonuses"
        ),
        inline=False
    )
    embed.add_field(name="🧾 Commands", value="`*balance` view balance • `*buy <item_id>` redeem • `*earnpoints` full earning guide", inline=False)
    embed.set_footer(text="Points save in points.json • Spam/farming can remove points or rewards")
    embed.timestamp = discord.utils.utcnow()
    return embed

def build_earn_points_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="💸 How To Earn Points",
        description="Every method below saves to `points.json`, so balances/cooldowns stay after restarts and code updates.",
        color=discord.Color.from_rgb(255, 184, 77)
    )
    embed.add_field(
        name="🗣️ Passive Activity",
        value=(
            "**Chatting:** `3–8` points about once per minute when chat points are enabled.\n"
            "**Replying:** bonus points for real replies, cooldown protected.\n"
            "**Media/attachments:** small bonus for sending images/clips, cooldown protected.\n"
            "Messages that are too short or spammy will not farm points well."
        ),
        inline=False
    )
    embed.add_field(
        name="⏰ Timed Claims",
        value="`*daily` — bigger reward every 24 hours.\n`*weekly` — large reward every 7 days.\n`*work` — steady hourly reward.",
        inline=False
    )
    embed.add_field(
        name="🎲 Mini Earn Commands",
        value="`*beg` — quick small chance reward every 30 minutes.\n`*search` — scavenger-style reward every 45 minutes.\n`*fish` — catch fish for points every hour.\n`*crime` — risky command with higher payout but possible point loss.\n`*trivia` — participation reward prompt every 20 minutes.",
        inline=False
    )
    embed.add_field(name="🛒 Spending", value="Use `*smokeshop` to see rewards, then `*buy <item_id>` to redeem. Open a ticket if staff needs to approve or deliver the reward.", inline=False)
    embed.add_field(name="⚠️ Rules", value="No spam, alt farming, copied messages, or fake activity. Staff can remove points using `*pointadmin remove` or `*pointadmin set`.", inline=False)
    embed.set_footer(text="Tip: pin this command or mention it in your welcome/info channel")
    embed.timestamp = discord.utils.utcnow()
    return embed

def format_cooldown(seconds: int) -> str:
    return format_seconds(max(0, int(seconds)))

def can_claim_cooldown(rec: dict, key: str, cooldown: int) -> tuple[bool, int]:
    now = int(time.time())
    remaining = cooldown - (now - int(rec.get(key, 0)))
    return remaining <= 0, max(0, remaining)

def apply_points_reward(data: dict, guild_id: int, user_id: int, amount: int, cooldown_key: Optional[str] = None):
    ensure_points_user(data, guild_id, user_id)
    rec = data[str(guild_id)]["users"][str(user_id)]
    if cooldown_key:
        rec[cooldown_key] = int(time.time())
    rec["points"] = max(0, int(rec.get("points", 0)) + int(amount))
    if amount > 0:
        rec["earned_total"] = int(rec.get("earned_total", 0)) + int(amount)
    elif amount < 0:
        rec["spent_total"] = int(rec.get("spent_total", 0)) + abs(int(amount))
    return rec
# ---------- GIVEAWAY ENTRY HELPERS ----------
def normalize_entrants(gw: dict) -> dict[str, dict]:
    raw = gw.get("entrants", {})
    normalized: dict[str, dict] = {}

    if isinstance(raw, list):
        for value in raw:
            try:
                user_id = str(int(value))
            except Exception:
                continue
            normalized[user_id] = {
                "entered_at": None,
                "status_locked_ok": False,
                "status_snapshot": None,
            }
        return normalized

    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                user_id = str(int(key))
            except Exception:
                continue

            if isinstance(value, dict):
                normalized[user_id] = {
                    "entered_at": value.get("entered_at"),
                    "status_locked_ok": bool(value.get("status_locked_ok", False)),
                    "status_snapshot": value.get("status_snapshot"),
                }
            else:
                normalized[user_id] = {
                    "entered_at": None,
                    "status_locked_ok": bool(value),
                    "status_snapshot": None,
                }

    return normalized


def get_unique_entrant_ids(gw: dict) -> list[int]:
    return [int(uid) for uid in normalize_entrants(gw).keys()]


def get_entrant_record(gw: dict, user_id: int) -> Optional[dict]:
    entrants = normalize_entrants(gw)
    return entrants.get(str(user_id))


def has_user_entered_giveaway(gw: dict, user_id: int) -> bool:
    return str(user_id) in normalize_entrants(gw)


def get_member_status_text(member: discord.Member) -> str:
    parts: list[str] = []
    for activity in getattr(member, "activities", []) or []:
        for attr in ("name", "state", "details"):
            value = getattr(activity, attr, None)
            if value:
                parts.append(str(value))
    return " | ".join(parts)[:300] if parts else ""


def add_user_to_giveaway(gw: dict, user_id: int, *, status_locked_ok: bool, status_snapshot: Optional[str]):
    entrants = normalize_entrants(gw)
    entrants[str(user_id)] = {
        "entered_at": int(time.time()),
        "status_locked_ok": bool(status_locked_ok),
        "status_snapshot": status_snapshot,
    }
    gw["entrants"] = entrants


def get_effective_entry_count_for_member(gw: dict, member: Optional[discord.Member]) -> int:
    entries = 1
    booster_bonus_entries = int(gw.get("booster_bonus_entries", 0))

    if member is not None and booster_bonus_entries > 0 and member.premium_since is not None:
        entries += booster_bonus_entries

    return entries


def get_giveaway_counts(gw: dict, guild: Optional[discord.Guild]) -> tuple[int, int]:
    entrant_ids = get_unique_entrant_ids(gw)
    unique_count = len(entrant_ids)
    total_entries = 0

    for user_id in entrant_ids:
        member = guild.get_member(user_id) if guild else None
        total_entries += get_effective_entry_count_for_member(gw, member)

    return unique_count, total_entries


def build_entrant_lines(gw: dict, guild: Optional[discord.Guild]) -> list[str]:
    entrant_ids = get_unique_entrant_ids(gw)
    lines = []

    for user_id in entrant_ids:
        member = guild.get_member(user_id) if guild else None
        entries = get_effective_entry_count_for_member(gw, member)
        record = get_entrant_record(gw, user_id) or {}
        display = member.mention if member else f"`{user_id}`"

        extra_bits = []
        if gw.get("required_status_substring"):
            extra_bits.append("status locked ✅" if record.get("status_locked_ok") else "status locked ❌")

        suffix = f" ({', '.join(extra_bits)})" if extra_bits else ""
        lines.append(f"{display} — {entries} {'entry' if entries == 1 else 'entries'}{suffix}")

    return lines


# ---------- ACTIVITY TRACKING ----------
def ensure_activity_user(data: dict, guild_id: int, user_id: int):
    gid = str(guild_id)
    uid = str(user_id)

    if gid not in data:
        data[gid] = {}

    if uid not in data[gid]:
        data[gid][uid] = {
            "messages_total": 0,
            "vc_seconds_total": 0,
            "message_events": [],
            "vc_sessions": [],
            "xp": 0
        }

    entry = data[gid][uid]
    entry.setdefault("messages_total", 0)
    entry.setdefault("vc_seconds_total", 0)
    entry.setdefault("message_events", [])
    entry.setdefault("vc_sessions", [])
    entry.setdefault("xp", 0)


def trim_activity_lists(entry: dict):
    cutoff = int(time.time()) - (90 * 24 * 3600)
    entry["message_events"] = [ts for ts in entry.get("message_events", []) if ts >= cutoff]
    entry["vc_sessions"] = [s for s in entry.get("vc_sessions", []) if int(s.get("end", 0)) >= cutoff]


def add_message_event(guild_id: int, user_id: int, ts: int):
    data = load_activity_data()
    ensure_activity_user(data, guild_id, user_id)
    entry = data[str(guild_id)][str(user_id)]
    entry["messages_total"] += 1
    entry["message_events"].append(ts)
    trim_activity_lists(entry)
    save_activity_data(data)


def add_vc_session(guild_id: int, user_id: int, start_ts: int, end_ts: int):
    if end_ts <= start_ts:
        return

    data = load_activity_data()
    ensure_activity_user(data, guild_id, user_id)
    entry = data[str(guild_id)][str(user_id)]
    duration = end_ts - start_ts
    entry["vc_seconds_total"] += duration
    entry["vc_sessions"].append({"start": start_ts, "end": end_ts})
    trim_activity_lists(entry)
    save_activity_data(data)


def get_user_activity(guild_id: int, user_id: int) -> dict:
    data = load_activity_data()
    return data.get(str(guild_id), {}).get(
        str(user_id),
        {
            "messages_total": 0,
            "vc_seconds_total": 0,
            "message_events": [],
            "vc_sessions": []
        }
    )


def get_messages_since(guild_id: int, user_id: int, since_ts: int) -> int:
    entry = get_user_activity(guild_id, user_id)
    return sum(1 for ts in entry.get("message_events", []) if ts >= since_ts)


def get_vc_seconds_since(guild_id: int, user_id: int, since_ts: int) -> int:
    total = 0
    entry = get_user_activity(guild_id, user_id)

    for session in entry.get("vc_sessions", []):
        start = int(session.get("start", 0))
        end = int(session.get("end", 0))
        if end <= since_ts:
            continue
        overlap_start = max(start, since_ts)
        if end > overlap_start:
            total += end - overlap_start

    key = (guild_id, user_id)
    if key in active_vc_sessions:
        live_start = int(active_vc_sessions[key])
        now = int(time.time())
        overlap_start = max(live_start, since_ts)
        if now > overlap_start:
            total += now - overlap_start

    return total


def get_total_vc_seconds(guild_id: int, user_id: int) -> int:
    entry = get_user_activity(guild_id, user_id)
    stored = int(entry.get("vc_seconds_total", 0))
    live = 0

    key = (guild_id, user_id)
    if key in active_vc_sessions:
        live_start = int(active_vc_sessions[key])
        now = int(time.time())
        if now > live_start:
            live = now - live_start

    return stored + live


# ---------- LEVELING HELPERS ----------
def xp_for_level(level: int) -> int:
    return 100 * (level ** 2)


def level_from_xp(xp: int) -> int:
    level = 0
    while xp >= xp_for_level(level + 1):
        level += 1
    return level


def add_xp_event(guild_id: int, user_id: int, amount: int) -> tuple[int, int, bool]:
    data = load_activity_data()
    ensure_activity_user(data, guild_id, user_id)
    entry = data[str(guild_id)][str(user_id)]
    entry.setdefault("xp", 0)
    before = level_from_xp(int(entry.get("xp", 0)))
    entry["xp"] = int(entry.get("xp", 0)) + int(amount)
    after = level_from_xp(int(entry.get("xp", 0)))
    save_activity_data(data)
    return int(entry["xp"]), after, after > before


# ---------- VC ROLE REWARDS ----------
def get_sorted_vc_reward_configs(guild_id: int) -> list[dict]:
    data = load_vc_role_rewards()
    rewards = data.get(str(guild_id), [])
    clean = []

    for reward in rewards:
        try:
            clean.append({
                "hours": int(reward["hours"]),
                "role_id": int(reward["role_id"])
            })
        except Exception:
            continue

    clean.sort(key=lambda x: x["hours"])
    return clean


def get_highest_qualified_vc_reward(guild_id: int, total_vc_seconds: int) -> Optional[dict]:
    rewards = get_sorted_vc_reward_configs(guild_id)
    qualified = None

    for reward in rewards:
        required_seconds = int(reward["hours"]) * 3600
        if total_vc_seconds >= required_seconds:
            qualified = reward
        else:
            break

    return qualified


async def sync_member_vc_reward_role(member: discord.Member):
    if member.bot:
        return

    rewards = get_sorted_vc_reward_configs(member.guild.id)
    if not rewards:
        return

    total_vc_seconds = get_total_vc_seconds(member.guild.id, member.id)
    qualified = get_highest_qualified_vc_reward(member.guild.id, total_vc_seconds)

    reward_role_ids = {int(r["role_id"]) for r in rewards}
    current_reward_roles = [role for role in member.roles if role.id in reward_role_ids]

    target_role = None
    if qualified:
        target_role = member.guild.get_role(int(qualified["role_id"]))

    roles_to_remove = [role for role in current_reward_roles if target_role is None or role.id != target_role.id]

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="VC milestone role sync")

        if target_role and target_role not in member.roles:
            await member.add_roles(target_role, reason="Reached VC milestone")
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


# ---------- INVITE HELPERS ----------
def ensure_invite_stats_user(data: dict, guild_id: int, user_id: int):
    gid = str(guild_id)
    uid = str(user_id)

    if gid not in data:
        data[gid] = {}
    if uid not in data[gid]:
        data[gid][uid] = {
            "total_invites": 0,
            "events": []
        }

    data[gid][uid].setdefault("total_invites", 0)
    data[gid][uid].setdefault("events", [])


def trim_invite_events(entry: dict):
    cutoff = int(time.time()) - (180 * 24 * 3600)
    entry["events"] = [ts for ts in entry.get("events", []) if ts >= cutoff]


def add_invite_event(guild_id: int, inviter_id: int, ts: int, count: int = 1):
    if inviter_id <= 0 or count <= 0:
        return

    data = load_invite_stats()
    ensure_invite_stats_user(data, guild_id, inviter_id)
    entry = data[str(guild_id)][str(inviter_id)]
    entry["total_invites"] += count
    entry["events"].extend([ts] * count)
    trim_invite_events(entry)
    save_invite_stats(data)


def get_invites_since(guild_id: int, user_id: int, since_ts: int) -> int:
    data = load_invite_stats()
    entry = data.get(str(guild_id), {}).get(str(user_id), {"events": []})
    return sum(1 for ts in entry.get("events", []) if ts >= since_ts)


def has_status_substring(member: discord.Member, substring: Optional[str]) -> bool:
    if not substring:
        return True

    target = substring.lower()
    for activity in getattr(member, "activities", []) or []:
        text_parts = []
        for attr in ("name", "state", "details"):
            value = getattr(activity, attr, None)
            if value:
                text_parts.append(str(value).lower())
        if target in " ".join(text_parts):
            return True

    return False


async def refresh_invite_cache_for_guild(guild: discord.Guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {
            invite.code: {
                "uses": invite.uses or 0,
                "inviter_id": invite.inviter.id if invite.inviter else None,
            }
            for invite in invites
        }
    except Exception:
        pass


async def detect_used_invite(guild: discord.Guild) -> tuple[Optional[str], Optional[int]]:
    try:
        before = invite_cache.get(guild.id, {})
        invites = await guild.invites()
        after = {
            invite.code: {
                "uses": invite.uses or 0,
                "inviter_id": invite.inviter.id if invite.inviter else None,
            }
            for invite in invites
        }

        used_code = None
        inviter_id = None

        for code, data in after.items():
            if data["uses"] > before.get(code, {}).get("uses", 0):
                used_code = code
                inviter_id = data.get("inviter_id")
                break

        invite_cache[guild.id] = after
        return used_code, inviter_id
    except Exception:
        return None, None


@bot.event
async def on_member_join(member: discord.Member):
    # Join utilities: ghost ping, welcome channel, DM on join, invite tracking.
    try:
        config_all = load_config()
        config = config_all.get(str(member.guild.id), {})

        ping_channel_id = config.get("ping_channel_id")
        if ping_channel_id:
            channel = member.guild.get_channel(int(ping_channel_id))
            if channel:
                msg = await channel.send(member.mention)
                await msg.delete(delay=1)

        welcome_channel_id = config.get("welcome_channel_id")
        welcome_message = config.get("welcome_message")
        if welcome_channel_id and welcome_message:
            channel = member.guild.get_channel(int(welcome_channel_id))
            if channel:
                embed = clean_embed("Welcome", safe_template(welcome_message, member), SUCCESS_COLOR)
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"{member.guild.name} • Member #{member.guild.member_count}")
                await channel.send(content=member.mention if config.get("welcome_ping") else None, embed=embed)

        join_dm = config.get("join_dm")
        if join_dm:
            try:
                embed = clean_embed(f"Welcome to {member.guild.name}", safe_template(join_dm, member), DEFAULT_THEME_COLOR)
                await member.send(embed=embed)
            except Exception:
                pass

        used_code, inviter_id = await detect_used_invite(member.guild)

        if used_code:
            tracked = load_invite_tracking()
            tracked.setdefault(str(member.guild.id), {})[str(member.id)] = used_code
            save_invite_tracking(tracked)

        if inviter_id:
            add_invite_event(member.guild.id, inviter_id, int(time.time()), 1)
    except Exception as e:
        print(f"on_member_join error: {e}")

    # AntiNuke: punish unauthorized bot adds. This only uses the real antinuke bypass list,
    # not fakeperms.
    try:
        if member.bot:
            entry = await audit_entry(member.guild, discord.AuditLogAction.bot_add, member.id)
            if entry and entry.user:
                await check_single_dangerous_action(
                    member.guild,
                    entry.user.id,
                    "botadd",
                    f"AntiNuke: unauthorized bot added (`{member}`)"
                )
    except Exception as e:
        print(f"botadd antinuke error: {e}")


@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild:
        await refresh_invite_cache_for_guild(invite.guild)


@bot.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild:
        await refresh_invite_cache_for_guild(invite.guild)


async def user_joined_target_server_via_invite(target_guild_id: int, user_id: int, invite_code: str) -> bool:
    tracked = load_invite_tracking()
    return tracked.get(str(target_guild_id), {}).get(str(user_id)) == invite_code


# ---------- EMBEDS ----------
def build_activity_embed(guild: discord.Guild, user_ids: list[int]) -> discord.Embed:
    total_messages = 0
    total_vc_seconds = 0

    embed = discord.Embed(
        title="📊 Activity Summary",
        description="Realtime tracked activity for the selected members.",
        color=discord.Color.blurple()
    )

    for user_id in user_ids:
        member = guild.get_member(user_id)
        name = member.display_name if member else f"Unknown User ({user_id})"
        stats = get_user_activity(guild.id, user_id)
        messages = int(stats.get("messages_total", 0))
        vc_seconds = get_total_vc_seconds(guild.id, user_id)
        invites_since_day = get_invites_since(guild.id, user_id, int(time.time()) - 86400)

        total_messages += messages
        total_vc_seconds += vc_seconds

        embed.add_field(
            name=f"👤 {name}",
            value=(
                f"**Messages:** `{messages}`\n"
                f"**VC Time:** `{format_seconds(vc_seconds)}`\n"
                f"**Invites (24h):** `{invites_since_day}`\n"
                f"**User ID:** `{user_id}`"
            ),
            inline=False
        )

    embed.add_field(
        name="📦 Totals",
        value=(
            f"**Combined Messages:** `{total_messages}`\n"
            f"**Combined VC Time:** `{format_seconds(total_vc_seconds)}`"
        ),
        inline=False
    )
    embed.add_field(
        name="🤖 Bot Info",
        value=(
            f"**Uptime:** `{format_uptime()}`\n"
            f"**Tracking Since:** `Bot startup`"
        ),
        inline=False
    )

    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_giveaway_embed(gw: dict, host_name: str, guild: Optional[discord.Guild]) -> discord.Embed:
    unique_entrants, total_entries = get_giveaway_counts(gw, guild)

    embed = discord.Embed(
        title=f"🎉 {gw['prize']}",
        description="Use the buttons below to enter or manage this giveaway.",
        color=discord.Color.green() if gw.get("active", True) else discord.Color.red()
    )

    embed.add_field(
        name="⏰ Ends",
        value=f"<t:{gw['end_ts']}:F>\n<t:{gw['end_ts']}:R>",
        inline=True
    )
    embed.add_field(name="🏆 Winners", value=str(gw["winners_count"]), inline=True)
    embed.add_field(name="👥 Entrants", value=str(unique_entrants), inline=True)
    embed.add_field(name="🎟️ Total Entries", value=str(total_entries), inline=True)
    embed.add_field(name="✅ Requirements", value="\n".join(format_requirement_lines(gw)), inline=False)

    if gw.get("claim_server_link"):
        embed.add_field(name="📨 Claim Server", value=gw["claim_server_link"], inline=False)

    status_mode = "Active" if gw.get("active") else "Ended"
    embed.set_footer(text=f"Giveaway ID: {gw['id']} • {status_mode} • Host: {host_name}")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Bot Help",
        description="Slash commands and how to use them.",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="Giveaway Commands",
        value=(
            "`/gw_start` • Start a giveaway with requirements\n"
            "`/gw_enter` • Enter a giveaway by ID\n"
            "`/gw_end` • End a giveaway now by ID\n"
            "`/gw_end_latest` • End the latest active giveaway in this channel\n"
            "`/gw_reroll` • Reroll a giveaway\n"
            "`/gw_cancel` • Cancel a giveaway by ID\n"
            "`/gw_cancel_latest` • Cancel the latest active giveaway in this channel\n"
            "`/gw_list` • List recent giveaways\n"
            "`/gw_info` • Show one giveaway's details"
        ),
        inline=False
    )
    embed.add_field(
        name="VC Role Commands",
        value=(
            "`/vcrole_add` • Add or update a VC milestone role\n"
            "`/vcrole_remove` • Remove a VC milestone role\n"
            "`/vcrole_list` • Show all VC milestone roles\n"
            "`/vcrole_sync` • Manually sync all VC milestone roles"
        ),
        inline=False
    )
    embed.add_field(
        name="Buttons",
        value=(
            "`Enter Giveaway` • enter fast\n"
            "`View Info` • see full requirement info\n"
            "`View Entrants` • host/admin only\n"
            "`Show ID` • reveals the giveaway ID ephemerally\n"
            "`End Now` • host/admin only"
        ),
        inline=False
    )
    embed.add_field(
        name="Activity Commands",
        value=(
            "`/activity` • Show tracked messages, VC time, and recent invites\n"
            "`/resetactivity` • Reset tracked activity for users"
        ),
        inline=False
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


# ---------- GIVEAWAY VIEWS ----------
class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

        enter_button = discord.ui.Button(
            label="Enter Giveaway",
            style=discord.ButtonStyle.success,
            custom_id=f"gw_enter:{giveaway_id}"
        )
        info_button = discord.ui.Button(
            label="View Info",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gw_info:{giveaway_id}"
        )
        entrants_button = discord.ui.Button(
            label="View Entrants",
            style=discord.ButtonStyle.primary,
            custom_id=f"gw_entrants:{giveaway_id}"
        )
        id_button = discord.ui.Button(
            label="Show ID",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gw_showid:{giveaway_id}"
        )
        end_button = discord.ui.Button(
            label="End Now",
            style=discord.ButtonStyle.danger,
            custom_id=f"gw_endnow:{giveaway_id}"
        )

        async def enter_callback(interaction: discord.Interaction):
            await enter_giveaway(interaction, self.giveaway_id)

        async def info_callback(interaction: discord.Interaction):
            data = load_giveaways()
            gw = data.get(self.giveaway_id)
            if not gw:
                await interaction.response.send_message("Giveaway not found.", ephemeral=True)
                return

            host_name = str(gw.get("host_id"))
            if interaction.guild:
                host = interaction.guild.get_member(int(gw.get("host_id", 0)))
                if host:
                    host_name = host.display_name

            embed = build_giveaway_embed(gw, host_name, interaction.guild)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        async def entrants_callback(interaction: discord.Interaction):
            data = load_giveaways()
            gw = data.get(self.giveaway_id)
            if not gw:
                await interaction.response.send_message("Giveaway not found.", ephemeral=True)
                return

            is_host = interaction.user.id == int(gw.get("host_id", 0))
            is_admin = bool(interaction.user.guild_permissions.administrator) if interaction.guild else False

            if not is_host and not is_admin:
                await interaction.response.send_message(
                    "Only the giveaway host or an admin can view entrants.",
                    ephemeral=True
                )
                return

            lines = build_entrant_lines(gw, interaction.guild)
            if not lines:
                await interaction.response.send_message("Nobody has entered yet.", ephemeral=True)
                return

            chunks = []
            current = ""
            for line in lines:
                if len(current) + len(line) + 1 > 1900:
                    chunks.append(current)
                    current = line
                else:
                    current = f"{current}\n{line}".strip()

            if current:
                chunks.append(current)

            await interaction.response.send_message(
                f"**Entrants for:** **{gw['prize']}**\n\n{chunks[0]}",
                ephemeral=True
            )

            for extra in chunks[1:]:
                await interaction.followup.send(extra, ephemeral=True)

        async def id_callback(interaction: discord.Interaction):
            await interaction.response.send_message(
                f"Giveaway ID: `{self.giveaway_id}`",
                ephemeral=True
            )

        async def end_callback(interaction: discord.Interaction):
            data = load_giveaways()
            gw = data.get(self.giveaway_id)
            if not gw:
                await interaction.response.send_message("Giveaway not found.", ephemeral=True)
                return

            is_host = interaction.user.id == int(gw.get("host_id", 0))
            is_admin = bool(interaction.user.guild_permissions.administrator) if interaction.guild else False

            if not is_host and not is_admin:
                await interaction.response.send_message(
                    "Only the giveaway host or an admin can end this giveaway.",
                    ephemeral=True
                )
                return

            ok, msg = await conclude_giveaway(self.giveaway_id)
            await interaction.response.send_message(
                msg if ok else f"Could not end giveaway: {msg}",
                ephemeral=True
            )

        enter_button.callback = enter_callback
        info_button.callback = info_callback
        entrants_button.callback = entrants_callback
        id_button.callback = id_callback
        end_button.callback = end_callback

        self.add_item(enter_button)
        self.add_item(info_button)
        self.add_item(entrants_button)
        self.add_item(id_button)
        self.add_item(end_button)


# ---------- GIVEAWAY CORE ----------
async def validate_giveaway_requirements(
    host_guild: discord.Guild,
    member: discord.Member,
    gw: dict,
    *,
    check_live_status: bool = True
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if gw.get("require_boosting") and member.premium_since is None:
        reasons.append("You must currently be boosting this server.")

    if check_live_status and gw.get("required_status_substring") and not has_status_substring(member, gw["required_status_substring"]):
        reasons.append(f"Status must contain `{gw['required_status_substring']}`.")

    start_ts = int(gw["start_ts"])

    required_messages = int(gw.get("required_messages", 0))
    if required_messages > 0:
        msgs = get_messages_since(host_guild.id, member.id, start_ts)
        if msgs < required_messages:
            reasons.append(f"Need `{required_messages}` messages since start. You have `{msgs}`.")

    required_vc_seconds = int(gw.get("required_vc_seconds", 0))
    if required_vc_seconds > 0:
        vc = get_vc_seconds_since(host_guild.id, member.id, start_ts)
        if vc < required_vc_seconds:
            reasons.append(f"Need `{format_seconds(required_vc_seconds)}` VC time since start. You have `{format_seconds(vc)}`.")

    required_invites = int(gw.get("required_invites", 0))
    if required_invites > 0:
        invites = get_invites_since(host_guild.id, member.id, start_ts)
        if invites < required_invites:
            reasons.append(f"Need `{required_invites}` invites since start. You have `{invites}`.")

    target_server_id = gw.get("required_server_id")
    if target_server_id:
        target_guild = bot.get_guild(int(target_server_id))
        if target_guild is None or target_guild.get_member(member.id) is None:
            reasons.append(f"Must be in server `{target_server_id}`.")
        else:
            required_invite = gw.get("required_invite_code")
            if required_invite:
                joined_ok = await user_joined_target_server_via_invite(int(target_server_id), member.id, required_invite)
                if not joined_ok:
                    reasons.append(f"Must have joined target server with invite `{required_invite}`.")

    return len(reasons) == 0, reasons


def entrant_locked_status_ok(gw: dict, user_id: int) -> bool:
    if not gw.get("required_status_substring"):
        return True
    record = get_entrant_record(gw, user_id) or {}
    return bool(record.get("status_locked_ok", False))


async def refresh_giveaway_message(giveaway_id: str):
    data = load_giveaways()
    gw = data.get(giveaway_id)
    if not gw:
        return

    guild = bot.get_guild(int(gw["guild_id"]))
    if guild is None:
        return

    channel = guild.get_channel(int(gw["channel_id"]))
    if channel is None:
        return

    try:
        message = await channel.fetch_message(int(gw["message_id"]))
    except Exception:
        return

    host = guild.get_member(int(gw.get("host_id", 0)))
    host_name = host.display_name if host else str(gw.get("host_id"))
    embed = build_giveaway_embed(gw, host_name, guild)
    view = GiveawayView(giveaway_id) if gw.get("active") else None
    await message.edit(embed=embed, view=view)


async def enter_giveaway(interaction: discord.Interaction, giveaway_id: str):
    data = load_giveaways()
    gw = data.get(giveaway_id)
    if not gw:
        await interaction.response.send_message("That giveaway was not found.", ephemeral=True)
        return
    if not gw.get("active", False):
        await interaction.response.send_message("That giveaway is no longer active.", ephemeral=True)
        return
    if interaction.guild_id != gw.get("guild_id"):
        await interaction.response.send_message("That giveaway belongs to a different server.", ephemeral=True)
        return
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        await interaction.response.send_message("You must be in the server to enter.", ephemeral=True)
        return

    if has_user_entered_giveaway(gw, interaction.user.id):
        existing_entries = get_effective_entry_count_for_member(gw, member)
        await interaction.response.send_message(
            f"You already entered this giveaway. Your current total is **{existing_entries}** "
            f"{'entry' if existing_entries == 1 else 'entries'}.",
            ephemeral=True
        )
        return

    status_ok_at_entry = has_status_substring(member, gw.get("required_status_substring"))
    valid, reasons = await validate_giveaway_requirements(
        interaction.guild,
        member,
        gw,
        check_live_status=True
    )
    if not valid:
        embed = discord.Embed(
            title="❌ You can't enter yet",
            description="You need to meet these requirement(s) first:",
            color=discord.Color.red()
        )
        embed.add_field(name="Missing Requirements", value="\n".join(f"• {r}" for r in reasons), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    add_user_to_giveaway(
        gw,
        interaction.user.id,
        status_locked_ok=status_ok_at_entry,
        status_snapshot=get_member_status_text(member)
    )
    data[giveaway_id] = gw
    save_giveaways(data)
    await refresh_giveaway_message(giveaway_id)

    personal_entries = get_effective_entry_count_for_member(gw, member)
    unique_entrants, total_entries = get_giveaway_counts(gw, interaction.guild)

    embed = discord.Embed(
        title="✅ Entry Confirmed",
        description=f"You joined **{gw['prize']}**.",
        color=discord.Color.green()
    )
    embed.add_field(name="Your Entries", value=str(personal_entries), inline=True)
    embed.add_field(name="Total Entrants", value=str(unique_entrants), inline=True)
    embed.add_field(name="Total Entries", value=str(total_entries), inline=True)

    if gw.get("required_status_substring"):
        embed.add_field(
            name="Locked Status Check",
            value=f"Passed at entry for `{gw['required_status_substring']}`",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


async def announce_valid_winners(channel: discord.abc.Messageable, guild: discord.Guild, gw: dict, winners: list[discord.Member]):
    winner_mentions = " ".join(w.mention for w in winners)
    admin_role = guild.get_role(int(gw["admin_role_id"])) if gw.get("admin_role_id") else None
    admin_ping = admin_role.mention if admin_role else ""

    embed = discord.Embed(
        title="🏆 Giveaway Ended",
        description=(
            f"**Prize:** {gw['prize']}\n"
            f"**Winner(s):** {winner_mentions}"
        ),
        color=discord.Color.gold()
    )
    embed.add_field(name="Giveaway ID", value=gw["id"], inline=True)
    embed.add_field(name="Requirements Check", value="Passed", inline=True)
    embed.timestamp = discord.utils.utcnow()

    ping_line = " ".join(part for part in [admin_ping, winner_mentions] if part).strip()
    await channel.send(content=ping_line or winner_mentions or None, embed=embed)

    claim_link = gw.get("claim_server_link")
    for winner in winners:
        try:
            text = f"You won **{gw['prize']}** in **{guild.name}**."
            if claim_link:
                text += f"\nPlease join this server to get added to the claim waitlist: {claim_link}"
            await winner.send(text)
        except Exception:
            pass


async def conclude_giveaway(giveaway_id: str):
    data = load_giveaways()
    gw = data.get(giveaway_id)
    if not gw:
        return False, "Giveaway not found."

    if not gw.get("active", False):
        return False, "That giveaway is already ended."

    guild = bot.get_guild(int(gw["guild_id"]))
    if guild is None:
        return False, "Host guild is unavailable."

    channel = guild.get_channel(int(gw["channel_id"]))
    if channel is None:
        return False, "Giveaway channel is unavailable."

    entrant_ids = get_unique_entrant_ids(gw)
    if not entrant_ids:
        gw["active"] = False
        gw["ended_ts"] = int(time.time())
        save_giveaways(data)
        await refresh_giveaway_message(giveaway_id)
        await channel.send(f"Giveaway `{giveaway_id}` ended with no entries.")
        return True, "No entries."

    valid_members: list[discord.Member] = []
    invalid_attempts: list[str] = []

    for entrant_id in entrant_ids:
        member = guild.get_member(int(entrant_id))
        if member is None:
            invalid_attempts.append(f"<@{entrant_id}> is no longer in the server.")
            continue

        if not entrant_locked_status_ok(gw, entrant_id):
            invalid_attempts.append(f"{member.mention} did not have the required status when they entered.")
            continue

        ok, reasons = await validate_giveaway_requirements(
            guild,
            member,
            gw,
            check_live_status=False
        )
        if ok:
            valid_members.append(member)
        else:
            invalid_attempts.append(f"{member.mention} does not meet the remaining requirements.")
            try:
                message_text = (
                    "You were in a giveaway draw, but you do not currently meet the remaining requirements:\n- "
                    + "\n- ".join(reasons)
                )
                await member.send(message_text)
            except Exception:
                pass

    weighted_pool: list[int] = []
    for member in valid_members:
        entries = get_effective_entry_count_for_member(gw, member)
        weighted_pool.extend([member.id] * entries)

    winners: list[discord.Member] = []
    selected_ids: set[int] = set()

    while weighted_pool and len(winners) < int(gw["winners_count"]):
        chosen_id = random.choice(weighted_pool)
        weighted_pool = [uid for uid in weighted_pool if uid != chosen_id]

        if chosen_id in selected_ids:
            continue

        chosen_member = guild.get_member(chosen_id)
        if chosen_member is None:
            continue

        selected_ids.add(chosen_id)
        winners.append(chosen_member)

    gw["active"] = False
    gw["ended_ts"] = int(time.time())
    gw["last_valid_winner_ids"] = [member.id for member in winners]
    save_giveaways(data)
    await refresh_giveaway_message(giveaway_id)

    if invalid_attempts:
        embed = discord.Embed(
            title="⚠️ Requirement Check Results",
            description="\n".join(invalid_attempts[:10]),
            color=discord.Color.orange()
        )
        await channel.send(embed=embed)

    if winners:
        await announce_valid_winners(channel, guild, gw, winners)
        return True, f"Selected {len(winners)} valid winner(s)."

    await channel.send(f"Giveaway `{giveaway_id}` ended, but no entrant met the requirements.")
    return True, "No valid winners."


def find_latest_active_giveaway_for_channel(guild_id: int, channel_id: int) -> Optional[dict]:
    data = load_giveaways()
    relevant = [
        gw for gw in data.values()
        if int(gw.get("guild_id", 0)) == guild_id
        and int(gw.get("channel_id", 0)) == channel_id
        and bool(gw.get("active"))
    ]
    if not relevant:
        return None
    relevant.sort(key=lambda x: int(x.get("start_ts", 0)), reverse=True)
    return relevant[0]


# ---------- BACKGROUND TASKS ----------
@tasks.loop(seconds=30)
async def giveaway_watcher():
    await bot.wait_until_ready()
    data = load_giveaways()
    now = int(time.time())
    due_ids = [gid for gid, gw in data.items() if gw.get("active") and int(gw.get("end_ts", 0)) <= now]
    for gid in due_ids:
        try:
            await conclude_giveaway(gid)
        except Exception as e:
            print(f"Failed to conclude giveaway {gid}: {e}")


@tasks.loop(minutes=2)
async def vc_role_reward_watcher():
    await bot.wait_until_ready()

    for guild in bot.guilds:
        rewards = get_sorted_vc_reward_configs(guild.id)
        if not rewards:
            continue

        for member in guild.members:
            await sync_member_vc_reward_role(member)


# ---------- EVENTS ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    try:
        print("Syncing slash commands...")
        if TEST_GUILD_ID:
            guild_obj = discord.Object(id=int(TEST_GUILD_ID))
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} guild slash command(s) to {TEST_GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global slash command(s)")
    except Exception as e:
        print(f"Slash command sync failed: {e}")

    for guild in bot.guilds:
        await refresh_invite_cache_for_guild(guild)

    if not giveaway_watcher.is_running():
        giveaway_watcher.start()

    if not vc_role_reward_watcher.is_running():
        vc_role_reward_watcher.start()


@bot.event
async def on_guild_join(guild: discord.Guild):
    await refresh_invite_cache_for_guild(guild)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.guild is not None:
        now = int(time.time())
        add_message_event(message.guild.id, message.author.id, now)

        # Smoke points: saved in points.json and cooldown-protected so spam does not farm points.
        pkey = (message.guild.id, message.author.id)
        points_config = load_points()
        ensure_points_user(points_config, message.guild.id, message.author.id)
        guild_points = points_config[str(message.guild.id)]
        settings = guild_points.get("settings", {})
        rec = guild_points["users"][str(message.author.id)]
        content_len = len((message.content or "").strip())
        min_len = int(settings.get("min_message_length", 8))

        if settings.get("chat_points", True) and content_len >= min_len:
            if now - point_message_cooldown.get(pkey, 0) >= POINTS_MESSAGE_COOLDOWN_SECONDS:
                point_message_cooldown[pkey] = now
                apply_points_reward(points_config, message.guild.id, message.author.id, random.randint(3, 8))

        if settings.get("reply_bonus", True) and message.reference and content_len >= min_len:
            if now - int(rec.get("reply_bonus_last", 0)) >= POINTS_REPLY_COOLDOWN_SECONDS:
                apply_points_reward(points_config, message.guild.id, message.author.id, random.randint(4, 10), "reply_bonus_last")

        if settings.get("attachment_bonus", True) and message.attachments:
            if now - int(rec.get("attachment_bonus_last", 0)) >= POINTS_ATTACHMENT_COOLDOWN_SECONDS:
                apply_points_reward(points_config, message.guild.id, message.author.id, random.randint(5, 15), "attachment_bonus_last")

        save_points(points_config)

        # Leveling with cooldown so spam does not farm XP.
        config = load_config().get(str(message.guild.id), {})
        if config.get("leveling_enabled", True):
            key = (message.guild.id, message.author.id)
            if now - last_xp_gain.get(key, 0) >= LEVEL_COOLDOWN_SECONDS:
                last_xp_gain[key] = now
                xp, new_level, leveled = add_xp_event(message.guild.id, message.author.id, random.randint(8, 15))
                if leveled and config.get("levelup_messages", True):
                    level_channel_id = config.get("level_channel_id")
                    channel = message.guild.get_channel(int(level_channel_id)) if level_channel_id else message.channel
                    if channel:
                        embed = clean_embed("Level Up", f"{message.author.mention} reached **level {new_level}**.", SUCCESS_COLOR)
                        await channel.send(embed=embed)

        # Autoresponders.
        ar_data = load_autoresponders().get(str(message.guild.id), {})
        lowered = message.content.lower()
        for trigger, payload in ar_data.items():
            response = payload.get("response") if isinstance(payload, dict) else str(payload)
            exact = bool(payload.get("exact", False)) if isinstance(payload, dict) else False
            should_send = lowered == trigger.lower() if exact else trigger.lower() in lowered
            if should_send:
                await message.channel.send(response.replace("{user}", message.author.mention).replace("{server}", message.guild.name))
                break

    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    guild_id = member.guild.id
    user_id = member.id
    key = (guild_id, user_id)
    now = int(time.time())

    # Voicemaster: create a private temp VC when user joins the trigger channel.
    try:
        config = load_config().get(str(guild_id), {})
        trigger_id = config.get("tempvc_trigger_id")
        category_id = config.get("tempvc_category_id")
        if after.channel and trigger_id and int(after.channel.id) == int(trigger_id):
            category = member.guild.get_channel(int(category_id)) if category_id else after.channel.category
            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(connect=True),
                member: discord.PermissionOverwrite(manage_channels=True, move_members=True, mute_members=True, connect=True),
            }
            vc = await member.guild.create_voice_channel(
                name=f"{member.display_name}'s Room",
                category=category,
                overwrites=overwrites,
                reason="Voicemaster temporary channel created"
            )
            await member.move_to(vc)
            tempvc = load_tempvc()
            tempvc[str(vc.id)] = {"owner_id": member.id, "guild_id": guild_id, "created_at": now}
            save_tempvc(tempvc)

        if before.channel:
            tempvc = load_tempvc()
            if str(before.channel.id) in tempvc and len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Voicemaster temporary channel empty")
                except Exception:
                    pass
                tempvc.pop(str(before.channel.id), None)
                save_tempvc(tempvc)
    except Exception as e:
        print(f"voicemaster error: {e}")

    if before.channel is None and after.channel is not None:
        active_vc_sessions[key] = now
    elif before.channel is not None and after.channel is None:
        if key in active_vc_sessions:
            started = int(active_vc_sessions.pop(key))
            add_vc_session(guild_id, user_id, started, now)
    elif before.channel != after.channel:
        if key in active_vc_sessions:
            started = int(active_vc_sessions[key])
            add_vc_session(guild_id, user_id, started, now)
        if after.channel is not None:
            active_vc_sessions[key] = now

    try:
        await sync_member_vc_reward_role(member)
    except Exception:
        pass


# ---------- SLASH COMMANDS ----------
@bot.tree.command(name="help", description="Show all commands and how to use them.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed(), ephemeral=True)


@bot.tree.command(name="activity", description="Show tracked activity for selected members.")
@app_commands.describe(user_ids="Paste one or more user IDs separated by spaces")
async def activity(interaction: discord.Interaction, user_ids: str):
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    try:
        ids = [int(x) for x in user_ids.split()]
    except ValueError:
        await interaction.response.send_message("All user IDs must be numbers.", ephemeral=True)
        return

    embed = build_activity_embed(interaction.guild, ids)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="resetactivity", description="Reset tracked activity for selected members.")
@app_commands.describe(user_ids="Paste one or more user IDs separated by spaces")
async def resetactivity(interaction: discord.Interaction, user_ids: str):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    try:
        ids = [int(x) for x in user_ids.split()]
    except ValueError:
        await interaction.response.send_message("All user IDs must be numbers.", ephemeral=True)
        return

    data = load_activity_data()
    gid = str(interaction.guild.id)
    data.setdefault(gid, {})

    for uid in ids:
        data[gid][str(uid)] = {
            "messages_total": 0,
            "vc_seconds_total": 0,
            "message_events": [],
            "vc_sessions": []
        }
        key = (interaction.guild.id, uid)
        if key in active_vc_sessions:
            active_vc_sessions[key] = int(time.time())

    save_activity_data(data)
    await interaction.response.send_message(f"Reset activity for `{len(ids)}` user(s).", ephemeral=True)


@bot.tree.command(name="gw_start", description="Start a giveaway with custom requirements.")
@app_commands.describe(
    prize="Prize text shown in the giveaway",
    duration="Examples: 30m, 12h, 24h, 2d, 1h30m",
    winners="Number of winners",
    require_boosting="If true, users must currently be boosting to enter",
    booster_bonus_entries="Extra entries boosters get in the winner draw",
    required_messages="Messages required since giveaway start",
    required_vc_time="VC time required since giveaway start. Examples: 15m, 2h",
    required_status_substring="Required text in the user's custom status",
    required_invites="Invites gained after the giveaway start that are required to enter",
    required_server_id="Optional: user must be in this server too",
    required_invite_code="Optional: user must have joined the required server through this invite",
    claim_server_link="DM this link to winners",
    admin_role="Role to ping when a valid winner is found"
)
async def gw_start(
    interaction: discord.Interaction,
    prize: str,
    duration: str,
    winners: app_commands.Range[int, 1, 25] = 1,
    require_boosting: bool = False,
    booster_bonus_entries: app_commands.Range[int, 0, 10] = 0,
    required_messages: app_commands.Range[int, 0, 100000] = 0,
    required_vc_time: Optional[str] = None,
    required_status_substring: Optional[str] = None,
    required_invites: app_commands.Range[int, 0, 10000] = 0,
    required_server_id: Optional[str] = None,
    required_invite_code: Optional[str] = None,
    claim_server_link: Optional[str] = None,
    admin_role: Optional[discord.Role] = None,
):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    try:
        duration_seconds = parse_duration(duration)
    except ValueError:
        await interaction.response.send_message(
            "Invalid duration. Use examples like `30m`, `12h`, `24h`, `2d`, `1h30m`.",
            ephemeral=True
        )
        return

    required_vc_seconds = 0
    if required_vc_time:
        try:
            required_vc_seconds = parse_duration(required_vc_time)
        except ValueError:
            await interaction.response.send_message(
                "Invalid VC requirement. Use examples like `15m`, `2h`, `1h30m`.",
                ephemeral=True
            )
            return

    target_server_id_int = None
    if required_server_id:
        try:
            target_server_id_int = int(required_server_id)
        except ValueError:
            await interaction.response.send_message("required_server_id must be a number.", ephemeral=True)
            return

    giveaway_id = str(int(time.time() * 1000))
    now_ts = int(time.time())
    end_ts = now_ts + duration_seconds

    data = load_giveaways()
    data[giveaway_id] = {
        "id": giveaway_id,
        "guild_id": interaction.guild_id,
        "channel_id": interaction.channel_id,
        "message_id": None,
        "host_id": interaction.user.id,
        "prize": prize,
        "winners_count": winners,
        "require_boosting": require_boosting,
        "booster_bonus_entries": int(booster_bonus_entries),
        "start_ts": now_ts,
        "end_ts": end_ts,
        "required_messages": int(required_messages),
        "required_vc_seconds": int(required_vc_seconds),
        "required_status_substring": required_status_substring,
        "required_invites": int(required_invites),
        "required_server_id": target_server_id_int,
        "required_invite_code": required_invite_code,
        "claim_server_link": claim_server_link,
        "admin_role_id": admin_role.id if admin_role else None,
        "entrants": {},
        "active": True
    }
    save_giveaways(data)

    embed = build_giveaway_embed(data[giveaway_id], interaction.user.display_name, interaction.guild)
    view = GiveawayView(giveaway_id)
    await interaction.response.send_message(embed=embed, view=view)
    original = await interaction.original_response()

    data = load_giveaways()
    data[giveaway_id]["message_id"] = original.id
    save_giveaways(data)


@bot.tree.command(name="gw_enter", description="Enter a giveaway by its ID.")
@app_commands.describe(giveaway_id="Giveaway ID shown in the giveaway embed footer")
async def gw_enter(interaction: discord.Interaction, giveaway_id: str):
    await enter_giveaway(interaction, giveaway_id)


@bot.tree.command(name="gw_end", description="End a giveaway now and validate winners.")
@app_commands.describe(giveaway_id="Giveaway ID shown in the giveaway embed footer")
async def gw_end(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    ok, msg = await conclude_giveaway(giveaway_id)
    await interaction.response.send_message(msg if ok else f"Could not end giveaway: {msg}", ephemeral=True)


@bot.tree.command(name="gw_end_latest", description="End the latest active giveaway in this channel.")
async def gw_end_latest(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    gw = find_latest_active_giveaway_for_channel(interaction.guild_id, interaction.channel_id)
    if not gw:
        await interaction.response.send_message("No active giveaway found in this channel.", ephemeral=True)
        return

    ok, msg = await conclude_giveaway(gw["id"])
    await interaction.response.send_message(msg if ok else f"Could not end giveaway: {msg}", ephemeral=True)


@bot.tree.command(name="gw_reroll", description="Reroll a giveaway and find a new valid winner.")
@app_commands.describe(giveaway_id="Giveaway ID shown in the giveaway embed footer")
async def gw_reroll(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    data = load_giveaways()
    gw = data.get(giveaway_id)
    if not gw:
        await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        return

    gw["active"] = True
    gw["end_ts"] = int(time.time())
    save_giveaways(data)

    ok, msg = await conclude_giveaway(giveaway_id)
    await interaction.response.send_message(msg if ok else f"Could not reroll giveaway: {msg}", ephemeral=True)


@bot.tree.command(name="gw_cancel", description="Cancel an active giveaway.")
@app_commands.describe(giveaway_id="Giveaway ID shown in the giveaway embed footer")
async def gw_cancel(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    data = load_giveaways()
    gw = data.get(giveaway_id)
    if not gw:
        await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        return

    gw["active"] = False
    gw["cancelled_ts"] = int(time.time())
    save_giveaways(data)
    await refresh_giveaway_message(giveaway_id)
    await interaction.response.send_message(f"Cancelled giveaway `{giveaway_id}`.", ephemeral=True)


@bot.tree.command(name="gw_cancel_latest", description="Cancel the latest active giveaway in this channel.")
async def gw_cancel_latest(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    gw = find_latest_active_giveaway_for_channel(interaction.guild_id, interaction.channel_id)
    if not gw:
        await interaction.response.send_message("No active giveaway found in this channel.", ephemeral=True)
        return

    data = load_giveaways()
    data[gw["id"]]["active"] = False
    data[gw["id"]]["cancelled_ts"] = int(time.time())
    save_giveaways(data)
    await refresh_giveaway_message(gw["id"])
    await interaction.response.send_message(f"Cancelled giveaway `{gw['id']}`.", ephemeral=True)


@bot.tree.command(name="gw_list", description="List recent giveaways in this server.")
async def gw_list(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    data = load_giveaways()
    relevant = [gw for gw in data.values() if int(gw.get("guild_id", 0)) == interaction.guild_id]
    relevant.sort(key=lambda x: int(x.get("start_ts", 0)), reverse=True)
    relevant = relevant[:10]

    if not relevant:
        await interaction.response.send_message("No giveaways found in this server.", ephemeral=True)
        return

    embed = discord.Embed(title="🎁 Giveaway List", color=discord.Color.blurple())

    for gw in relevant:
        status = "Active" if gw.get("active") else "Ended"
        unique_entrants, total_entries = get_giveaway_counts(gw, interaction.guild)

        embed.add_field(
            name=f"{gw['prize']} • {status}",
            value=(
                f"**ID:** `{gw['id']}`\n"
                f"**Winners:** `{gw['winners_count']}`\n"
                f"**Entrants:** `{unique_entrants}`\n"
                f"**Total Entries:** `{total_entries}`\n"
                f"**Ends:** <t:{gw['end_ts']}:R>"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="gw_info", description="Show full info for one giveaway.")
@app_commands.describe(giveaway_id="Giveaway ID shown in the giveaway embed footer")
async def gw_info(interaction: discord.Interaction, giveaway_id: str):
    data = load_giveaways()
    gw = data.get(giveaway_id)
    if not gw:
        await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        return

    host_name = str(gw.get("host_id"))
    if interaction.guild:
        host = interaction.guild.get_member(int(gw.get("host_id", 0)))
        if host:
            host_name = host.display_name

    embed = build_giveaway_embed(gw, host_name, interaction.guild)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- VC ROLE COMMANDS ----------
@bot.tree.command(name="vcrole_add", description="Add or update a VC hour milestone role reward.")
@app_commands.describe(
    hours="Required VC hours, like 24 or 40",
    role="Role to give when that milestone is reached"
)
async def vcrole_add(interaction: discord.Interaction, hours: app_commands.Range[int, 1, 100000], role: discord.Role):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    me = interaction.guild.me
    if me is None:
        await interaction.response.send_message("Could not verify bot permissions.", ephemeral=True)
        return

    if role >= me.top_role:
        await interaction.response.send_message(
            "That role is above or equal to my top role. Move my bot role higher first.",
            ephemeral=True
        )
        return

    data = load_vc_role_rewards()
    gid = str(interaction.guild.id)
    data.setdefault(gid, [])

    updated = False
    for reward in data[gid]:
        if int(reward["hours"]) == int(hours):
            reward["role_id"] = role.id
            updated = True
            break

    if not updated:
        data[gid].append({
            "hours": int(hours),
            "role_id": role.id
        })

    data[gid].sort(key=lambda r: int(r["hours"]))
    save_vc_role_rewards(data)

    await interaction.response.send_message(
        f"Saved VC reward: `{hours}` hours → {role.mention}",
        ephemeral=True
    )


@bot.tree.command(name="vcrole_remove", description="Remove a VC hour milestone role reward.")
@app_commands.describe(hours="The VC hour milestone to remove")
async def vcrole_remove(interaction: discord.Interaction, hours: app_commands.Range[int, 1, 100000]):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    data = load_vc_role_rewards()
    gid = str(interaction.guild.id)
    rewards = data.get(gid, [])

    new_rewards = [r for r in rewards if int(r["hours"]) != int(hours)]

    if len(new_rewards) == len(rewards):
        await interaction.response.send_message("No VC reward milestone found for that hour amount.", ephemeral=True)
        return

    data[gid] = new_rewards
    save_vc_role_rewards(data)

    await interaction.response.send_message(f"Removed VC reward milestone for `{hours}` hours.", ephemeral=True)


@bot.tree.command(name="vcrole_list", description="List all VC milestone role rewards.")
async def vcrole_list(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    rewards = get_sorted_vc_reward_configs(interaction.guild.id)
    if not rewards:
        await interaction.response.send_message("No VC milestone roles have been set yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎙️ VC Milestone Roles",
        color=discord.Color.blurple()
    )

    lines = []
    for reward in rewards:
        role = interaction.guild.get_role(int(reward["role_id"]))
        role_text = role.mention if role else f"`{reward['role_id']}`"
        lines.append(f"`{reward['hours']}` hours → {role_text}")

    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="vcrole_sync", description="Manually sync VC milestone roles for everyone in the server.")
async def vcrole_sync(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    count = 0
    for member in interaction.guild.members:
        if member.bot:
            continue
        await sync_member_vc_reward_role(member)
        count += 1

    await interaction.followup.send(f"Synced VC milestone roles for `{count}` member(s).", ephemeral=True)


# ---------- PREFIX HELP + CONFIG COMMANDS ----------
@bot.command(name="help")
async def prefix_help(ctx, category: Optional[str] = None):
    prefix = bot.command_prefix if isinstance(bot.command_prefix, str) else "*"
    categories = {
        "setup": [
            (f"{prefix}setpingchannel #channel", "Ghost-pings new members in the selected channel."),
            (f"{prefix}setjoindm <message>", "Sets the DM sent to new members. Supports {user}, {user.name}, {server}."),
            (f"{prefix}clearjoindm", "Disables DM on join."),
            (f"{prefix}setwelcome #channel <message>", "Sets a public welcome embed."),
            (f"{prefix}setupvc <trigger vc> [category]", "Sets up Voicemaster temp voice rooms."),
            (f"{prefix}leveling on/off", "Toggles leveling."),
        ],
        "moderation": [
            (f"{prefix}ban @user [reason]", "Bans a member."),
            (f"{prefix}kick @user [reason]", "Kicks a member."),
            (f"{prefix}timeout @user 10m [reason]", "Times out a member."),
            (f"{prefix}untimeout @user", "Removes timeout."),
            (f"{prefix}warn @user <reason>", "Adds a warning."),
            (f"{prefix}warnings @user", "Shows warnings."),
            (f"{prefix}role @user @role", "Adds/removes a role from a member."),
            (f"{prefix}botclear 50", "Clears recent messages sent by bots."),
            (f"{prefix}purge 50", "Deletes recent messages."),
            (f"{prefix}lock [#channel]", "Locks a channel."),
            (f"{prefix}unlock [#channel]", "Unlocks a channel."),
            (f"{prefix}snipe", "Shows the last deleted message."),
            (f"{prefix}editsnipe", "Shows the last edited message."),
        ],
        "antinuke": [
            (f"{prefix}antinuke setup", "Shows the AntiNuke setup guide."),
            (f"{prefix}antinuke on/off", "Turns AntiNuke on/off."),
            (f"{prefix}antinuke module list", "Shows protection modules."),
            (f"{prefix}antinuke bypass user add/remove @user", "User bypass."),
            (f"{prefix}antinuke bypass role add/remove @role", "Role bypass."),
            (f"{prefix}antinuke punishment strip/kick/ban", "Sets punishment."),
            (f"{prefix}antinuke threshold 3 30", "Sets threshold/window."),
        ],
        "activity": [
            (f"{prefix}level [@user]", "Shows level and XP."),
            (f"{prefix}rank [@user]", "Alias for level."),
            (f"{prefix}leaderboard", "Shows top 10 by XP/messages/VC."),
            (f"{prefix}invites [@user]", "Shows invite stats."),
            (f"{prefix}topvc", "Shows top 10 VC users."),
        ],
        "autoresponder": [
            (f"{prefix}ar add <trigger> | <response>", "Creates an autoresponder."),
            (f"{prefix}ar exact <trigger> | <response>", "Creates exact-match autoresponder."),
            (f"{prefix}ar remove <trigger>", "Deletes an autoresponder."),
            (f"{prefix}ar list", "Lists autoresponders."),
        ],
        "fun": [
            (f"{prefix}blacktea", "Starts a quick blacktea-style word game."),
            (f"{prefix}ship @user @user", "Rates a ship."),
            (f"{prefix}rate [thing]", "Rates something out of 10."),
            (f"{prefix}8ball <question>", "Answers a question."),
            (f"{prefix}roast [@user]", "Light roast."),
            (f"{prefix}compliment [@user]", "Compliment."),
        ],
        "utility": [
            (f"{prefix}avatar [@user]", "Shows avatar."),
            (f"{prefix}userinfo [@user]", "Shows user info."),
            (f"{prefix}serverinfo", "Shows server info."),
            (f"{prefix}ping", "Shows bot latency."),
            ("/gw_start", "Starts an advanced giveaway with hidden button responses."),
            ("/gw_end, /gw_reroll, /gw_list", "Manage giveaways."),
            ("/vcrole_add, /vcrole_list", "Manage VC milestone roles."),
        ],
    }
    if category and category.lower() in categories:
        embed = clean_embed(f"Help • {category.title()}", color=DEFAULT_THEME_COLOR)
        for usage, desc in categories[category.lower()]:
            embed.add_field(name=usage, value=desc, inline=False)
    else:
        embed = clean_embed("Smoke Bot Help", f"Use `{prefix}help <category>` for details.\nCategories: `setup`, `moderation`, `antinuke`, `activity`, `autoresponder`, `fun`, `utility`", DEFAULT_THEME_COLOR)
        for name, cmds in categories.items():
            embed.add_field(name=name.title(), value="\n".join(f"`{u}` — {d}" for u, d in cmds[:4]), inline=False)
        embed.set_footer(text="Giveaways remain slash commands so buttons can use hidden/ephemeral messages.")
    await ctx.send(embed=embed)


@bot.command()
async def ping(ctx):
    await ctx.send(embed=clean_embed("Pong", f"Latency: `{round(bot.latency * 1000)}ms`", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(administrator=True)
async def setpingchannel(ctx, channel: discord.TextChannel):
    data = load_config(); gid = str(ctx.guild.id); data.setdefault(gid, {})["ping_channel_id"] = channel.id; save_config(data)
    await ctx.send(embed=clean_embed("Ping on Join Enabled", f"New members will be ghost-pinged in {channel.mention}.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(administrator=True)
async def setjoindm(ctx, *, message: str):
    data = load_config(); gid = str(ctx.guild.id); data.setdefault(gid, {})["join_dm"] = message; save_config(data)
    await ctx.send(embed=clean_embed("Join DM Set", "New members will receive your DM message.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(administrator=True)
async def clearjoindm(ctx):
    data = load_config(); data.setdefault(str(ctx.guild.id), {}).pop("join_dm", None); save_config(data)
    await ctx.send(embed=clean_embed("Join DM Disabled", "New members will no longer receive a DM from the bot.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(administrator=True)
async def setwelcome(ctx, channel: discord.TextChannel, *, message: str):
    data = load_config(); gid = str(ctx.guild.id); data.setdefault(gid, {})["welcome_channel_id"] = channel.id; data[gid]["welcome_message"] = message; save_config(data)
    await ctx.send(embed=clean_embed("Welcome Channel Set", f"Welcome embeds will be sent to {channel.mention}.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(administrator=True)
async def setupvc(ctx, trigger: discord.VoiceChannel, category: Optional[discord.CategoryChannel] = None):
    data = load_config(); gid = str(ctx.guild.id); data.setdefault(gid, {})["tempvc_trigger_id"] = trigger.id; data[gid]["tempvc_category_id"] = (category.id if category else (trigger.category.id if trigger.category else None)); save_config(data)
    await ctx.send(embed=clean_embed("Voicemaster Set", f"Joining **{trigger.name}** will create a temporary voice channel.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(administrator=True)
async def leveling(ctx, state: str):
    enabled = state.lower() in ("on", "enable", "enabled", "true")
    data = load_config(); data.setdefault(str(ctx.guild.id), {})["leveling_enabled"] = enabled; save_config(data)
    await ctx.send(embed=clean_embed("Leveling Updated", f"Leveling is now **{'on' if enabled else 'off'}**.", SUCCESS_COLOR))


# ---------- MODERATION COMMANDS ----------
@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=f"{ctx.author} | {reason}")
    await ctx.send(embed=clean_embed("Member Banned", f"**User:** {member}\n**Reason:** {reason}", ERROR_COLOR))


@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=f"{ctx.author} | {reason}")
    await ctx.send(embed=clean_embed("Member Kicked", f"**User:** {member}\n**Reason:** {reason}", WARNING_COLOR))


@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    seconds = parse_duration(duration)
    until = discord.utils.utcnow() + timedelta(seconds=seconds)
    await member.timeout(until, reason=f"{ctx.author} | {reason}")
    await ctx.send(embed=clean_embed("Member Timed Out", f"**User:** {member.mention}\n**Duration:** `{format_seconds(seconds)}`\n**Reason:** {reason}", WARNING_COLOR))


@bot.command()
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx, member: discord.Member):
    await member.timeout(None, reason=f"Removed by {ctx.author}")
    await ctx.send(embed=clean_embed("Timeout Removed", f"{member.mention} can chat again.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    amount = max(1, min(amount, 250))
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(embed=clean_embed("Messages Purged", f"Deleted `{len(deleted)-1}` messages.", SUCCESS_COLOR))
    await msg.delete(delay=4)


@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx, channel: Optional[discord.TextChannel] = None):
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Locked by {ctx.author}")
    await ctx.send(embed=clean_embed("Channel Locked", f"{channel.mention} is now locked.", WARNING_COLOR))


@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channel: Optional[discord.TextChannel] = None):
    channel = channel or ctx.channel
    await channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Unlocked by {ctx.author}")
    await ctx.send(embed=clean_embed("Channel Unlocked", f"{channel.mention} is now unlocked.", SUCCESS_COLOR))


@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str):
    data = load_warnings(); gid = str(ctx.guild.id); uid = str(member.id); data.setdefault(gid, {}).setdefault(uid, [])
    data[gid][uid].append({"reason": reason, "moderator_id": ctx.author.id, "ts": int(time.time())}); save_warnings(data)
    await ctx.send(embed=clean_embed("Warning Added", f"**User:** {member.mention}\n**Reason:** {reason}", WARNING_COLOR))


@bot.command(name="warnings")
@commands.has_permissions(manage_messages=True)
async def warnings_cmd(ctx, member: discord.Member):
    rows = load_warnings().get(str(ctx.guild.id), {}).get(str(member.id), [])
    embed = clean_embed(f"Warnings • {member.display_name}", color=DEFAULT_THEME_COLOR)
    if not rows:
        embed.description = "No warnings found."
    else:
        embed.description = "\n".join(f"`{i+1}.` {r['reason']} — <t:{r['ts']}:R>" for i, r in enumerate(rows[-10:]))
    await ctx.send(embed=embed)


@bot.command()
async def snipe(ctx):
    item = snipes.get(ctx.channel.id)
    if not item: return await ctx.send(embed=clean_embed("Snipe", "Nothing to snipe.", WARNING_COLOR))
    embed = clean_embed("Last Deleted Message", item.get("content") or "No text content.", DEFAULT_THEME_COLOR)
    embed.set_author(name=item["author"], icon_url=item["avatar"])
    await ctx.send(embed=embed)


@bot.command()
async def editsnipe(ctx):
    item = editsnipes.get(ctx.channel.id)
    if not item: return await ctx.send(embed=clean_embed("Edit Snipe", "Nothing to editsnipe.", WARNING_COLOR))
    embed = clean_embed("Last Edited Message", f"**Before:** {item['before']}\n**After:** {item['after']}", DEFAULT_THEME_COLOR)
    embed.set_author(name=item["author"], icon_url=item["avatar"])
    await ctx.send(embed=embed)


# ---------- ACTIVITY / LEVEL COMMANDS ----------
@bot.command(aliases=["rank"])
async def level(ctx, member: Optional[discord.Member] = None):
    member = member or ctx.author
    entry = get_user_activity(ctx.guild.id, member.id)
    xp = int(entry.get("xp", 0)); lvl = level_from_xp(xp); next_xp = xp_for_level(lvl + 1)
    embed = clean_embed(f"Level • {member.display_name}", color=DEFAULT_THEME_COLOR)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=f"`{lvl}`", inline=True)
    embed.add_field(name="XP", value=f"`{xp}/{next_xp}`", inline=True)
    embed.add_field(name="Messages", value=f"`{entry.get('messages_total', 0)}`", inline=True)
    embed.add_field(name="VC Time", value=f"`{format_seconds(get_total_vc_seconds(ctx.guild.id, member.id))}`", inline=True)
    await ctx.send(embed=embed)


@bot.command()
async def leaderboard(ctx):
    data = load_activity_data().get(str(ctx.guild.id), {})
    rows = []
    for uid, entry in data.items():
        rows.append((int(uid), int(entry.get("xp", 0)), int(entry.get("messages_total", 0)), get_total_vc_seconds(ctx.guild.id, int(uid))))
    rows.sort(key=lambda r: (r[1], r[2], r[3]), reverse=True)
    embed = clean_embed("Activity Leaderboard", "Top 10 members by XP, messages, and VC time.", DEFAULT_THEME_COLOR)
    lines=[]
    for i, (uid, xp, msgs, vc) in enumerate(rows[:10], 1):
        member = ctx.guild.get_member(uid); name = member.display_name if member else str(uid)
        medal = "👑" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`{i}.`"
        lines.append(f"{medal} **{name}** — Level `{level_from_xp(xp)}` • `{msgs}` msgs • `{format_seconds(vc)}` VC")
    embed.description = "\n".join(lines) if lines else "No activity tracked yet."
    await ctx.send(embed=embed)


@bot.command()
async def topvc(ctx):
    data = load_activity_data().get(str(ctx.guild.id), {})
    rows = [(int(uid), get_total_vc_seconds(ctx.guild.id, int(uid))) for uid in data]
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = clean_embed("Top Voice Activity", color=DEFAULT_THEME_COLOR)
    embed.description = "\n".join(f"`{i}.` {(ctx.guild.get_member(uid).display_name if ctx.guild.get_member(uid) else uid)} — `{format_seconds(sec)}`" for i, (uid, sec) in enumerate(rows[:10], 1)) or "No VC activity yet."
    await ctx.send(embed=embed)


@bot.command(name="invites")
async def invites_cmd(ctx, member: Optional[discord.Member] = None):
    member = member or ctx.author
    data = load_invite_stats().get(str(ctx.guild.id), {}).get(str(member.id), {"total_invites": 0, "events": []})
    await ctx.send(embed=clean_embed("Invite Stats", f"**User:** {member.mention}\n**Total Invites:** `{data.get('total_invites', 0)}`\n**24h Invites:** `{get_invites_since(ctx.guild.id, member.id, int(time.time())-86400)}`", DEFAULT_THEME_COLOR))


# ---------- AUTORESPONDER COMMAND GROUP ----------
@bot.group(invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def ar(ctx):
    await ctx.send("Use `*ar add trigger | response`, `*ar exact trigger | response`, `*ar remove trigger`, or `*ar list`.")


@ar.command(name="add")
@commands.has_permissions(manage_guild=True)
async def ar_add(ctx, *, text: str):
    if "|" not in text: return await ctx.send("Format: `*ar add trigger | response`")
    trigger, response = [x.strip() for x in text.split("|", 1)]
    data=load_autoresponders(); data.setdefault(str(ctx.guild.id), {})[trigger.lower()]={"response":response,"exact":False}; save_autoresponders(data)
    await ctx.send(embed=clean_embed("Autoresponder Added", f"Trigger: `{trigger}`", SUCCESS_COLOR))


@ar.command(name="exact")
@commands.has_permissions(manage_guild=True)
async def ar_exact(ctx, *, text: str):
    if "|" not in text: return await ctx.send("Format: `*ar exact trigger | response`")
    trigger, response = [x.strip() for x in text.split("|", 1)]
    data=load_autoresponders(); data.setdefault(str(ctx.guild.id), {})[trigger.lower()]={"response":response,"exact":True}; save_autoresponders(data)
    await ctx.send(embed=clean_embed("Exact Autoresponder Added", f"Trigger: `{trigger}`", SUCCESS_COLOR))


@ar.command(name="remove")
@commands.has_permissions(manage_guild=True)
async def ar_remove(ctx, *, trigger: str):
    data=load_autoresponders(); removed=data.get(str(ctx.guild.id), {}).pop(trigger.lower(), None); save_autoresponders(data)
    await ctx.send(embed=clean_embed("Autoresponder Removed" if removed else "Not Found", f"Trigger: `{trigger}`", SUCCESS_COLOR if removed else WARNING_COLOR))


@ar.command(name="list")
async def ar_list(ctx):
    rows=load_autoresponders().get(str(ctx.guild.id), {})
    embed=clean_embed("Autoresponders", color=DEFAULT_THEME_COLOR)
    embed.description="\n".join(f"`{k}` → {v.get('response') if isinstance(v, dict) else v}" for k,v in list(rows.items())[:20]) or "No autoresponders set."
    await ctx.send(embed=embed)


# ---------- FUN / SOCIAL / UTILITY ----------
@bot.command()
async def blacktea(ctx):
    if ctx.channel.id in blacktea_games: return await ctx.send("A blacktea game is already running here.")
    syllables=["an","ar","in","ou","st","ch","ea","oo","tr","ly","er","me","lo","ra","de"]
    syl=random.choice(syllables); blacktea_games.add(ctx.channel.id)
    await ctx.send(embed=clean_embed("Blacktea", f"Type a real word containing **{syl}**. You have 15 seconds.", DEFAULT_THEME_COLOR))
    def check(m): return m.channel==ctx.channel and not m.author.bot and syl in m.content.lower() and len(m.content) >= 3
    try:
        msg=await bot.wait_for("message", timeout=15, check=check)
        await ctx.send(embed=clean_embed("Blacktea Winner", f"{msg.author.mention} survived with `{msg.content}`.", SUCCESS_COLOR))
    except Exception:
        await ctx.send(embed=clean_embed("Blacktea Ended", "Nobody answered in time.", WARNING_COLOR))
    finally:
        blacktea_games.discard(ctx.channel.id)


@bot.command()
async def ship(ctx, a: discord.Member, b: Optional[discord.Member]=None):
    b=b or ctx.author; score=random.randint(0,100)
    await ctx.send(embed=clean_embed("Ship Rate", f"{a.mention} × {b.mention}\nCompatibility: **{score}%**", DEFAULT_THEME_COLOR))


@bot.command(name="8ball")
async def eightball(ctx, *, question: str):
    await ctx.send(embed=clean_embed("8Ball", random.choice(["Definitely.", "Probably.", "Not likely.", "Ask again later.", "Yes.", "No.", "It depends."]), DEFAULT_THEME_COLOR))


@bot.command()
async def rate(ctx, *, thing: str = "that"):
    await ctx.send(embed=clean_embed("Rating", f"I rate **{thing}** a **{random.randint(1,10)}/10**.", DEFAULT_THEME_COLOR))


@bot.command()
async def roast(ctx, member: Optional[discord.Member]=None):
    member=member or ctx.author
    roasts=["built like a loading screen", "has NPC energy", "types like their keyboard is underwater", "got WiFi personality"]
    await ctx.send(f"{member.mention} {random.choice(roasts)}")


@bot.command()
async def compliment(ctx, member: Optional[discord.Member]=None):
    member=member or ctx.author
    comps=["has elite energy", "is actually carrying the chat", "has a clean vibe", "is lowkey the main character"]
    await ctx.send(f"{member.mention} {random.choice(comps)}")


@bot.command()
async def avatar(ctx, member: Optional[discord.Member]=None):
    member=member or ctx.author
    embed=clean_embed(f"Avatar • {member.display_name}", color=DEFAULT_THEME_COLOR); embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command()
async def userinfo(ctx, member: Optional[discord.Member]=None):
    member=member or ctx.author
    embed=clean_embed(f"User Info • {member.display_name}", color=DEFAULT_THEME_COLOR)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    await ctx.send(embed=embed)


@bot.command()
async def serverinfo(ctx):
    g=ctx.guild
    embed=clean_embed(f"Server Info • {g.name}", color=DEFAULT_THEME_COLOR)
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Members", value=f"`{g.member_count}`", inline=True)
    embed.add_field(name="Channels", value=f"`{len(g.channels)}`", inline=True)
    embed.add_field(name="Roles", value=f"`{len(g.roles)}`", inline=True)
    embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    await ctx.send(embed=embed)



# ---------- ROLE / FAKEPERMS / REACTION ROLES / BOT CLEAR / ANTINUKE ----------
DANGEROUS_PERMISSIONS = {"administrator","manage_guild","manage_roles","manage_channels","manage_webhooks","ban_members","kick_members","moderate_members","manage_messages","mention_everyone"}
ANTINUKE_MODULES = {
    "ban": discord.AuditLogAction.ban, "kick": discord.AuditLogAction.kick, "botadd": discord.AuditLogAction.bot_add,
    "channel_create": discord.AuditLogAction.channel_create, "channel_delete": discord.AuditLogAction.channel_delete, "channel_update": discord.AuditLogAction.channel_update,
    "role_create": discord.AuditLogAction.role_create, "role_delete": discord.AuditLogAction.role_delete, "role_update": discord.AuditLogAction.role_update,
    "member_role_update": discord.AuditLogAction.member_role_update,
    "webhook_create": discord.AuditLogAction.webhook_create, "webhook_update": discord.AuditLogAction.webhook_update, "webhook_delete": discord.AuditLogAction.webhook_delete,
    "emoji_create": discord.AuditLogAction.emoji_create, "emoji_delete": discord.AuditLogAction.emoji_delete,
    "sticker_create": discord.AuditLogAction.sticker_create, "sticker_delete": discord.AuditLogAction.sticker_delete,
}

def get_fakeperms_for(member: discord.Member) -> set[str]:
    data = load_fakeperms().get(str(member.guild.id), {})
    perms = set(data.get("users", {}).get(str(member.id), []))
    for role in member.roles:
        perms.update(data.get("roles", {}).get(str(role.id), []))
    return perms

def has_fake_or_real_perm(ctx: commands.Context, perm: str) -> bool:
    if not ctx.guild: return False
    if ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator: return True
    if getattr(ctx.author.guild_permissions, perm, False): return True
    fake = get_fakeperms_for(ctx.author)
    return perm in fake or "admin" in fake

def can_manage_bot(ctx: commands.Context) -> bool:
    return has_fake_or_real_perm(ctx, "administrator") or has_fake_or_real_perm(ctx, "antinuke") or has_fake_or_real_perm(ctx, "manage_guild")

def bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
    me = guild.me or guild.get_member(bot.user.id)
    if not me: return False, "I could not verify my bot member."
    if not me.guild_permissions.manage_roles: return False, "I need **Manage Roles**."
    if role.is_default(): return False, "I cannot manage @everyone."
    if role.managed: return False, "That role is integration-managed."
    if role >= me.top_role: return False, "That role is above/equal to my top role. Move my bot role higher."
    return True, "OK"

def get_antinuke_settings(guild_id: int) -> dict:
    data = load_antinuke()
    st = data.setdefault(str(guild_id), {})
    st.setdefault("enabled", False); st.setdefault("punishment", "strip"); st.setdefault("threshold", 3); st.setdefault("window", 30)
    st.setdefault("whitelist_users", []); st.setdefault("whitelist_roles", []); st.setdefault("log_channel_id", None)
    st.setdefault("modules", {name: True for name in ANTINUKE_MODULES})
    if "whitelist" in st:
        for uid in st.pop("whitelist", []):
            if uid not in st["whitelist_users"]: st["whitelist_users"].append(uid)
    for name in ANTINUKE_MODULES: st["modules"].setdefault(name, True)
    save_antinuke(data)
    return st

def is_antinuke_bypassed(member: Optional[discord.Member], st: dict) -> bool:
    if member is None: return True
    if member.id == member.guild.owner_id: return True
    if member.id in [int(x) for x in st.get("whitelist_users", [])]: return True
    bypass_roles = {int(x) for x in st.get("whitelist_roles", [])}
    return any(r.id in bypass_roles for r in member.roles)

def role_has_dangerous_perms(role: discord.Role) -> bool:
    return any(getattr(role.permissions, p, False) for p in DANGEROUS_PERMISSIONS)

async def send_antinuke_log(guild: discord.Guild, title: str, description: str, color: discord.Color = WARNING_COLOR):
    st = get_antinuke_settings(guild.id)
    channel = guild.get_channel(int(st["log_channel_id"])) if st.get("log_channel_id") else None
    if channel:
        try: await channel.send(embed=clean_embed(title, description[:4000], color))
        except Exception: pass

async def audit_entry(guild: discord.Guild, action: discord.AuditLogAction, target_id: Optional[int] = None):
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > 12: continue
            if target_id is not None and getattr(entry.target, "id", None) != target_id: continue
            return entry
    except Exception:
        return None
    return None

async def punish_for_antinuke(guild: discord.Guild, actor_id: int, reason: str):
    st = get_antinuke_settings(guild.id)
    member = guild.get_member(actor_id)
    if not st.get("enabled") or is_antinuke_bypassed(member, st) or member == guild.me: return
    try:
        if st.get("punishment") == "ban":
            await member.ban(reason=reason)
        elif st.get("punishment") == "kick":
            await member.kick(reason=reason)
        else:
            roles = [r for r in member.roles if r != guild.default_role and r < guild.me.top_role and not r.managed and role_has_dangerous_perms(r)]
            if roles: await member.remove_roles(*roles, reason=reason)
        await send_antinuke_log(guild, "AntiNuke Action Taken", f"**User:** {member.mention} (`{member.id}`)\n**Punishment:** `{st.get('punishment')}`\n**Reason:** {reason}", ERROR_COLOR)
    except Exception as e:
        await send_antinuke_log(guild, "AntiNuke Failed", f"Could not punish <@{actor_id}>.\n`{e}`", ERROR_COLOR)

async def track_antinuke_action(guild: discord.Guild, actor_id: int, module: str, details: str = ""):
    st = get_antinuke_settings(guild.id)
    if not st.get("enabled") or not st.get("modules", {}).get(module, True): return
    member = guild.get_member(actor_id)
    if is_antinuke_bypassed(member, st): return
    now = int(time.time()); window = int(st.get("window", 30)); threshold = int(st.get("threshold", 3))
    key = (guild.id, actor_id, module)
    antinuke_recent_actions[key] = [t for t in antinuke_recent_actions.get(key, []) if now - t <= window] + [now]
    await send_antinuke_log(guild, "AntiNuke Watch", f"**User:** <@{actor_id}> (`{actor_id}`)\n**Module:** `{module}`\n**Count:** `{len(antinuke_recent_actions[key])}/{threshold}`\n{details}".strip())
    if len(antinuke_recent_actions[key]) >= threshold:
        await punish_for_antinuke(guild, actor_id, f"AntiNuke `{module}` threshold exceeded ({threshold}/{window}s)")
        antinuke_recent_actions[key] = []

async def check_single_dangerous_action(guild: discord.Guild, actor_id: int, module: str, reason: str):
    st = get_antinuke_settings(guild.id)
    if not st.get("enabled") or not st.get("modules", {}).get(module, True): return
    if is_antinuke_bypassed(guild.get_member(actor_id), st): return
    await punish_for_antinuke(guild, actor_id, reason)

@bot.command(name="role", aliases=["giverole", "addrole", "removerole", "takerole"])
async def role_command(ctx, member: discord.Member, *, role: discord.Role):
    if not has_fake_or_real_perm(ctx, "manage_roles"):
        return await ctx.send(embed=clean_embed("Missing Permission", "You need **Manage Roles** or fakeperm `manage_roles`.", ERROR_COLOR))
    ok, msg = bot_can_manage_role(ctx.guild, role)
    if not ok: return await ctx.send(embed=clean_embed("Role Error", msg, ERROR_COLOR))
    if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.send(embed=clean_embed("Role Error", "You cannot edit someone with an equal/higher top role than you.", ERROR_COLOR))
    try:
        if role in member.roles:
            await member.remove_roles(role, reason=f"Role command by {ctx.author}")
            text = f"Removed {role.mention} from {member.mention}."
        else:
            await member.add_roles(role, reason=f"Role command by {ctx.author}")
            text = f"Added {role.mention} to {member.mention}."
        await ctx.send(embed=clean_embed("Role Updated", text, SUCCESS_COLOR))
    except Exception as e:
        await ctx.send(embed=clean_embed("Role Error", f"`{e}`", ERROR_COLOR))

@bot.command(name="botclear", aliases=["bc", "clearbots", "botpurge"])
async def botclear(ctx, amount: int = 50):
    if not has_fake_or_real_perm(ctx, "manage_messages"):
        return await ctx.send(embed=clean_embed("Missing Permission", "You need **Manage Messages** or fakeperm `manage_messages`.", ERROR_COLOR))
    amount = max(1, min(amount, 200))
    deleted = await ctx.channel.purge(limit=amount + 1, check=lambda m: m.author.bot or m.id == ctx.message.id)
    await ctx.send(embed=clean_embed("Bot Clear", f"Deleted `{len(deleted)}` recent bot/command message(s).", SUCCESS_COLOR), delete_after=5)

@bot.group(name="fakeperm", aliases=["fakeperms"], invoke_without_command=True)
async def fakeperm(ctx):
    if not ctx.guild or not (ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator):
        return await ctx.send(embed=clean_embed("Missing Permission", "Only admins can manage fakeperms.", ERROR_COLOR))
    await ctx.send("Use `*fakeperm user add/remove @user <perm>`, `*fakeperm role add/remove @role <perm>`, or `*fakeperm list`. Fakeperms **do not bypass antinuke**.")

@fakeperm.group(name="user", invoke_without_command=True)
async def fakeperm_user(ctx): await ctx.send("Use `*fakeperm user add/remove @user <perm>`.")

@fakeperm_user.command(name="add")
async def fakeperm_user_add(ctx, member: discord.Member, perm: str):
    if not (ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator): return await ctx.send("You need administrator.")
    data=load_fakeperms(); gid=str(ctx.guild.id); data.setdefault(gid, {}).setdefault("users", {}).setdefault(str(member.id), [])
    if perm not in data[gid]["users"][str(member.id)]: data[gid]["users"][str(member.id)].append(perm)
    save_fakeperms(data); await ctx.send(embed=clean_embed("Fakeperm Added", f"{member.mention} → `{perm}`\nFakeperms do **not** bypass antinuke.", SUCCESS_COLOR))

@fakeperm_user.command(name="remove")
async def fakeperm_user_remove(ctx, member: discord.Member, perm: str):
    if not (ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator): return await ctx.send("You need administrator.")
    data=load_fakeperms(); perms=data.get(str(ctx.guild.id), {}).get("users", {}).get(str(member.id), [])
    if perm in perms: perms.remove(perm)
    save_fakeperms(data); await ctx.send(embed=clean_embed("Fakeperm Removed", f"{member.mention} → `{perm}` removed.", SUCCESS_COLOR))

@fakeperm.group(name="role", invoke_without_command=True)
async def fakeperm_role(ctx): await ctx.send("Use `*fakeperm role add/remove @role <perm>`.")

@fakeperm_role.command(name="add")
async def fakeperm_role_add(ctx, role: discord.Role, perm: str):
    if not (ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator): return await ctx.send("You need administrator.")
    data=load_fakeperms(); gid=str(ctx.guild.id); data.setdefault(gid, {}).setdefault("roles", {}).setdefault(str(role.id), [])
    if perm not in data[gid]["roles"][str(role.id)]: data[gid]["roles"][str(role.id)].append(perm)
    save_fakeperms(data); await ctx.send(embed=clean_embed("Fakeperm Added", f"{role.mention} → `{perm}`\nFakeperms do **not** bypass antinuke.", SUCCESS_COLOR))

@fakeperm_role.command(name="remove")
async def fakeperm_role_remove(ctx, role: discord.Role, perm: str):
    if not (ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator): return await ctx.send("You need administrator.")
    data=load_fakeperms(); perms=data.get(str(ctx.guild.id), {}).get("roles", {}).get(str(role.id), [])
    if perm in perms: perms.remove(perm)
    save_fakeperms(data); await ctx.send(embed=clean_embed("Fakeperm Removed", f"{role.mention} → `{perm}` removed.", SUCCESS_COLOR))

@fakeperm.command(name="list")
async def fakeperm_list(ctx):
    data=load_fakeperms().get(str(ctx.guild.id), {})
    users="\n".join(f"<@{u}>: `{', '.join(p)}`" for u,p in data.get("users", {}).items()) or "None"
    roles="\n".join(f"<@&{r}>: `{', '.join(p)}`" for r,p in data.get("roles", {}).items()) or "None"
    await ctx.send(embed=clean_embed("Fakeperms", f"**Users**\n{users}\n\n**Roles**\n{roles}"[:4000], DEFAULT_THEME_COLOR))

@bot.group(name="reactionrole", aliases=["rr"], invoke_without_command=True)
async def reactionrole(ctx):
    if not has_fake_or_real_perm(ctx, "manage_roles"): return await ctx.send("You need Manage Roles or fakeperm `manage_roles`.")
    await ctx.send("Use `*rr add <message_id> <emoji> @role`, `*rr remove <message_id> <emoji>`, or `*rr list`.")

@reactionrole.command(name="add")
async def reactionrole_add(ctx, message_id: int, emoji: str, role: discord.Role):
    if not has_fake_or_real_perm(ctx, "manage_roles"): return await ctx.send("Missing Manage Roles.")
    ok,msg=bot_can_manage_role(ctx.guild, role)
    if not ok: return await ctx.send(embed=clean_embed("Reaction Role Error", msg, ERROR_COLOR))
    try:
        message=await ctx.channel.fetch_message(message_id)
        await message.add_reaction(emoji)
    except Exception as e:
        return await ctx.send(embed=clean_embed("Reaction Role Error", f"Could not find/react to that message in this channel.\n`{e}`", ERROR_COLOR))
    data=load_reaction_roles(); gid=str(ctx.guild.id); data.setdefault(gid, {})
    data[gid][f"{ctx.channel.id}:{message_id}:{emoji}"]={"channel_id":ctx.channel.id,"message_id":message_id,"emoji":emoji,"role_id":role.id}
    save_reaction_roles(data)
    await ctx.send(embed=clean_embed("Reaction Role Added", f"{emoji} on [this message]({message.jump_url}) gives {role.mention}.", SUCCESS_COLOR))

@reactionrole.command(name="remove")
async def reactionrole_remove(ctx, message_id: int, emoji: str):
    if not has_fake_or_real_perm(ctx, "manage_roles"): return await ctx.send("Missing Manage Roles.")
    data=load_reaction_roles(); gid=str(ctx.guild.id)
    removed=data.get(gid, {}).pop(f"{ctx.channel.id}:{message_id}:{emoji}", None)
    save_reaction_roles(data)
    await ctx.send(embed=clean_embed("Reaction Role Removed", "Removed." if removed else "No reaction role found.", SUCCESS_COLOR if removed else WARNING_COLOR))

@reactionrole.command(name="list")
async def reactionrole_list(ctx):
    data=load_reaction_roles().get(str(ctx.guild.id), {})
    lines=[f"{v['emoji']} • <#{v['channel_id']}> • `{v['message_id']}` → <@&{v['role_id']}>" for v in data.values()]
    await ctx.send(embed=clean_embed("Reaction Roles", "\n".join(lines)[:4000] or "No reaction roles set.", DEFAULT_THEME_COLOR))

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None or payload.member is None or payload.member.bot: return
    item=load_reaction_roles().get(str(payload.guild_id), {}).get(f"{payload.channel_id}:{payload.message_id}:{str(payload.emoji)}")
    if not item: return
    guild=bot.get_guild(payload.guild_id); role=guild.get_role(int(item["role_id"])) if guild else None
    if role and bot_can_manage_role(guild, role)[0]:
        try: await payload.member.add_roles(role, reason="Reaction role add")
        except Exception: pass

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.guild_id is None: return
    item=load_reaction_roles().get(str(payload.guild_id), {}).get(f"{payload.channel_id}:{payload.message_id}:{str(payload.emoji)}")
    if not item: return
    guild=bot.get_guild(payload.guild_id); member=guild.get_member(payload.user_id) if guild else None; role=guild.get_role(int(item["role_id"])) if guild else None
    if member and role and not member.bot and bot_can_manage_role(guild, role)[0]:
        try: await member.remove_roles(role, reason="Reaction role remove")
        except Exception: pass

@bot.group(name="antinuke", aliases=["an"], invoke_without_command=True)
async def antinuke(ctx, state: Optional[str] = None):
    if not has_fake_or_real_perm(ctx, "antinuke") and not ctx.author.guild_permissions.administrator:
        return await ctx.send(embed=clean_embed("Missing Permission", "You need administrator or fakeperm `antinuke`.", ERROR_COLOR))
    if state is None:
        st=get_antinuke_settings(ctx.guild.id); enabled="ON" if st.get("enabled") else "OFF"
        return await ctx.send(embed=clean_embed("AntiNuke Status", f"**Status:** `{enabled}`\n**Punishment:** `{st.get('punishment')}`\n**Threshold:** `{st.get('threshold')}` actions / `{st.get('window')}`s\nUse `*antinuke setup` for the setup guide.", DEFAULT_THEME_COLOR))
    if state.lower() == "setup":
        return await antinuke_setup(ctx)
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id))
    if state.lower() in ("on","enable","enabled","true"): st["enabled"]=True
    elif state.lower() in ("off","disable","disabled","false"): st["enabled"]=False
    else: return await ctx.send("Use `*antinuke on`, `*antinuke off`, or `*antinuke setup`.")
    save_antinuke(data); await ctx.send(embed=clean_embed("AntiNuke Updated", f"AntiNuke is now **{'ON' if st['enabled'] else 'OFF'}**.", SUCCESS_COLOR))

@antinuke.command(name="setup")
async def antinuke_setup(ctx):
    desc=("**Simple setup**\n"
          "1. Move the bot role near the top, above staff roles it should protect against.\n"
          "2. Give the bot: `View Audit Log`, `Manage Roles`, `Kick Members`, `Ban Members`, `Manage Channels`, `Manage Webhooks`.\n"
          "3. Run `*antinuke logs #channel`.\n"
          "4. Run `*antinuke punishment strip` first. Use `ban` only when confident.\n"
          "5. Run `*antinuke threshold 3 30`.\n"
          "6. Add trusted users: `*antinuke bypass user add @user`.\n"
          "7. Add trusted roles: `*antinuke bypass role add @role`.\n"
          "8. Turn it on: `*antinuke on`.\n\n"
          "**Fakeperms do not bypass antinuke.** Only the bypass user/role list does.")
    await ctx.send(embed=clean_embed("AntiNuke Setup Guide", desc, DEFAULT_THEME_COLOR))

@antinuke.command(name="logs")
async def antinuke_logs(ctx, channel: discord.TextChannel):
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st["log_channel_id"]=channel.id; save_antinuke(data)
    await ctx.send(embed=clean_embed("AntiNuke Logs Set", f"Logs will go to {channel.mention}.", SUCCESS_COLOR))

@antinuke.command(name="punishment")
async def antinuke_punishment(ctx, punishment: str):
    punishment=punishment.lower()
    if punishment not in ("strip","kick","ban"): return await ctx.send("Punishment must be `strip`, `kick`, or `ban`.")
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st["punishment"]=punishment; save_antinuke(data)
    await ctx.send(embed=clean_embed("AntiNuke Punishment Set", f"Punishment: `{punishment}`", SUCCESS_COLOR))

@antinuke.command(name="threshold")
async def antinuke_threshold(ctx, threshold: int, window: int = 30):
    threshold=max(2,min(threshold,10)); window=max(10,min(window,120))
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st["threshold"]=threshold; st["window"]=window; save_antinuke(data)
    await ctx.send(embed=clean_embed("AntiNuke Threshold Set", f"`{threshold}` actions / `{window}s`", SUCCESS_COLOR))

@antinuke.group(name="module", invoke_without_command=True)
async def antinuke_module(ctx): await ctx.send("Use `*antinuke module list`, `*antinuke module on <module>`, or `*antinuke module off <module>`.")

@antinuke_module.command(name="list")
async def antinuke_module_list(ctx):
    st=get_antinuke_settings(ctx.guild.id)
    await ctx.send(embed=clean_embed("AntiNuke Modules", "\n".join(f"`{m}` — {'ON' if st['modules'].get(m) else 'OFF'}" for m in sorted(ANTINUKE_MODULES)), DEFAULT_THEME_COLOR))

@antinuke_module.command(name="on")
async def antinuke_module_on(ctx, module: str):
    if module not in ANTINUKE_MODULES: return await ctx.send("Unknown module. Use `*antinuke module list`.")
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st["modules"][module]=True; save_antinuke(data)
    await ctx.send(embed=clean_embed("Module Enabled", f"`{module}` is ON.", SUCCESS_COLOR))

@antinuke_module.command(name="off")
async def antinuke_module_off(ctx, module: str):
    if module not in ANTINUKE_MODULES: return await ctx.send("Unknown module. Use `*antinuke module list`.")
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st["modules"][module]=False; save_antinuke(data)
    await ctx.send(embed=clean_embed("Module Disabled", f"`{module}` is OFF.", WARNING_COLOR))

@antinuke.group(name="bypass", invoke_without_command=True)
async def antinuke_bypass(ctx): await ctx.send("Use `*antinuke bypass user add/remove @user`, `*antinuke bypass role add/remove @role`, or `*antinuke bypass list`.")

@antinuke_bypass.group(name="user", invoke_without_command=True)
async def antinuke_bypass_user(ctx): await ctx.send("Use `*antinuke bypass user add/remove @user`.")

@antinuke_bypass_user.command(name="add")
async def antinuke_bypass_user_add(ctx, member: discord.Member):
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st.setdefault("whitelist_users", [])
    if member.id not in st["whitelist_users"]: st["whitelist_users"].append(member.id)
    save_antinuke(data); await ctx.send(embed=clean_embed("AntiNuke Bypass Added", f"{member.mention} can bypass antinuke.", SUCCESS_COLOR))

@antinuke_bypass_user.command(name="remove")
async def antinuke_bypass_user_remove(ctx, member: discord.Member):
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st.setdefault("whitelist_users", [])
    if member.id in st["whitelist_users"]: st["whitelist_users"].remove(member.id)
    save_antinuke(data); await ctx.send(embed=clean_embed("AntiNuke Bypass Removed", f"{member.mention} no longer bypasses antinuke.", SUCCESS_COLOR))

@antinuke_bypass.group(name="role", invoke_without_command=True)
async def antinuke_bypass_role(ctx): await ctx.send("Use `*antinuke bypass role add/remove @role`.")

@antinuke_bypass_role.command(name="add")
async def antinuke_bypass_role_add(ctx, role: discord.Role):
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st.setdefault("whitelist_roles", [])
    if role.id not in st["whitelist_roles"]: st["whitelist_roles"].append(role.id)
    save_antinuke(data); await ctx.send(embed=clean_embed("AntiNuke Role Bypass Added", f"{role.mention} bypasses antinuke.", SUCCESS_COLOR))

@antinuke_bypass_role.command(name="remove")
async def antinuke_bypass_role_remove(ctx, role: discord.Role):
    data=load_antinuke(); st=data.setdefault(str(ctx.guild.id), get_antinuke_settings(ctx.guild.id)); st.setdefault("whitelist_roles", [])
    if role.id in st["whitelist_roles"]: st["whitelist_roles"].remove(role.id)
    save_antinuke(data); await ctx.send(embed=clean_embed("AntiNuke Role Bypass Removed", f"{role.mention} no longer bypasses antinuke.", SUCCESS_COLOR))

@antinuke_bypass.command(name="list")
async def antinuke_bypass_list(ctx):
    st=get_antinuke_settings(ctx.guild.id)
    users="\n".join(f"<@{u}> (`{u}`)" for u in st.get("whitelist_users", [])) or "None"
    roles="\n".join(f"<@&{r}> (`{r}`)" for r in st.get("whitelist_roles", [])) or "None"
    await ctx.send(embed=clean_embed("AntiNuke Bypass List", f"**Users**\n{users}\n\n**Roles**\n{roles}", DEFAULT_THEME_COLOR))

@bot.group(name="whitelist", invoke_without_command=True)
async def whitelist(ctx): await ctx.send("Use `*antinuke bypass user add @user`, `*antinuke bypass role add @role`, or `*antinuke bypass list`.")

@bot.group(name="antiset", invoke_without_command=True)
async def antiset(ctx): await ctx.send("Use `*antinuke punishment strip/kick/ban` or `*antinuke threshold 3 30`.")

@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild and not message.author.bot:
        snipes[message.channel.id] = {"content": message.content, "author": str(message.author), "avatar": message.author.display_avatar.url}

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild and not before.author.bot and before.content != after.content:
        editsnipes[before.channel.id] = {"before": before.content, "after": after.content, "author": str(before.author), "avatar": before.author.display_avatar.url}

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    entry=await audit_entry(guild, discord.AuditLogAction.ban, user.id)
    if entry and entry.user: await track_antinuke_action(guild, entry.user.id, "ban", f"**Target:** `{user}` (`{user.id}`)")

@bot.event
async def on_member_remove(member: discord.Member):
    entry=await audit_entry(member.guild, discord.AuditLogAction.kick, member.id)
    if entry and entry.user: await track_antinuke_action(member.guild, entry.user.id, "kick", f"**Target:** `{member}` (`{member.id}`)")

@bot.event
async def on_guild_channel_create(channel):
    entry=await audit_entry(channel.guild, discord.AuditLogAction.channel_create, channel.id)
    if entry and entry.user: await track_antinuke_action(channel.guild, entry.user.id, "channel_create", f"**Channel:** `{channel.name}`")

@bot.event
async def on_guild_channel_delete(channel):
    entry=await audit_entry(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
    if entry and entry.user: await track_antinuke_action(channel.guild, entry.user.id, "channel_delete", f"**Channel:** `{channel.name}`")

@bot.event
async def on_guild_channel_update(before, after):
    entry=await audit_entry(after.guild, discord.AuditLogAction.channel_update, after.id)
    if entry and entry.user: await track_antinuke_action(after.guild, entry.user.id, "channel_update", f"**Channel:** `{after.name}`")

@bot.event
async def on_guild_role_create(role):
    entry=await audit_entry(role.guild, discord.AuditLogAction.role_create, role.id)
    if entry and entry.user: await track_antinuke_action(role.guild, entry.user.id, "role_create", f"**Role:** `{role.name}`")

@bot.event
async def on_guild_role_delete(role):
    entry=await audit_entry(role.guild, discord.AuditLogAction.role_delete, role.id)
    if entry and entry.user: await track_antinuke_action(role.guild, entry.user.id, "role_delete", f"**Role:** `{role.name}`")

@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    entry=await audit_entry(after.guild, discord.AuditLogAction.role_update, after.id)
    if entry and entry.user:
        if role_has_dangerous_perms(after) and not role_has_dangerous_perms(before):
            await check_single_dangerous_action(after.guild, entry.user.id, "role_update", f"AntiNuke: dangerous permissions added to role `{after.name}`")
        else:
            await track_antinuke_action(after.guild, entry.user.id, "role_update", f"**Role:** `{after.name}`")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    added={r.id for r in after.roles} - {r.id for r in before.roles}
    dangerous=[after.guild.get_role(rid) for rid in added]
    dangerous=[r for r in dangerous if r and role_has_dangerous_perms(r)]
    if not dangerous: return
    entry=await audit_entry(after.guild, discord.AuditLogAction.member_role_update, after.id)
    if entry and entry.user:
        await check_single_dangerous_action(after.guild, entry.user.id, "member_role_update", f"AntiNuke: dangerous role given to {after} (`{after.id}`)")

@bot.event
async def on_webhooks_update(channel):
    for action,module in ((discord.AuditLogAction.webhook_create,"webhook_create"),(discord.AuditLogAction.webhook_update,"webhook_update"),(discord.AuditLogAction.webhook_delete,"webhook_delete")):
        entry=await audit_entry(channel.guild, action)
        if entry and entry.user:
            await track_antinuke_action(channel.guild, entry.user.id, module, f"**Channel:** `{channel.name}`")
            break

@bot.event
async def on_guild_emojis_update(guild, before, after):
    module="emoji_create" if len(after)>len(before) else "emoji_delete"
    action=discord.AuditLogAction.emoji_create if module=="emoji_create" else discord.AuditLogAction.emoji_delete
    entry=await audit_entry(guild, action)
    if entry and entry.user: await track_antinuke_action(guild, entry.user.id, module)

@bot.event
async def on_guild_stickers_update(guild, before, after):
    module="sticker_create" if len(after)>len(before) else "sticker_delete"
    action=discord.AuditLogAction.sticker_create if module=="sticker_create" else discord.AuditLogAction.sticker_delete
    entry=await audit_entry(guild, action)
    if entry and entry.user: await track_antinuke_action(guild, entry.user.id, module)



# ---------- POINTS + SMOKE SHOP COMMANDS ----------
@bot.command(name="smokeshop", aliases=["shop", "rewards"])
async def smokeshop(ctx, channel: Optional[discord.TextChannel] = None):
    if ctx.guild is None:
        return
    target = channel or ctx.channel
    embed = build_smoke_shop_embed(ctx.guild)
    await target.send(embed=embed)
    if target.id != ctx.channel.id:
        await ctx.send(embed=clean_embed("Smoke Shop Sent", f"Posted the shop in {target.mention}.", SUCCESS_COLOR))

@bot.command(name="earnpoints", aliases=["pointinfo", "howtoearn", "earn"])
async def earnpoints(ctx):
    if ctx.guild is None:
        return
    await ctx.send(embed=build_earn_points_embed(ctx.guild))

@bot.command(name="balance", aliases=["bal", "points"])
async def balance(ctx, member: Optional[discord.Member] = None):
    if ctx.guild is None:
        return
    member = member or ctx.author
    rec = get_points_record(ctx.guild.id, member.id)
    embed = clean_embed("💰 Points Balance", f"{member.mention} has **{int(rec.get('points', 0)):,}** points.", DEFAULT_THEME_COLOR)
    embed.add_field(name="Earned Total", value=f"`{int(rec.get('earned_total', 0)):,}`", inline=True)
    embed.add_field(name="Spent Total", value=f"`{int(rec.get('spent_total', 0)):,}`", inline=True)
    await ctx.send(embed=embed)

async def cooldown_reward_command(ctx, *, key: str, cooldown: int, reward_min: int, reward_max: int, title: str, messages: list[str], fail_chance: int = 0, fail_loss_min: int = 0, fail_loss_max: int = 0):
    if ctx.guild is None or ctx.author.bot:
        return
    data = load_points()
    ensure_points_user(data, ctx.guild.id, ctx.author.id)
    rec = data[str(ctx.guild.id)]["users"][str(ctx.author.id)]
    ok, remaining = can_claim_cooldown(rec, key, cooldown)
    if not ok:
        return await ctx.send(embed=clean_embed("Cooldown", f"You can use this again in `{format_cooldown(remaining)}`.", WARNING_COLOR))

    if fail_chance and random.randint(1, 100) <= fail_chance:
        loss = random.randint(fail_loss_min, fail_loss_max)
        rec = apply_points_reward(data, ctx.guild.id, ctx.author.id, -loss, key)
        save_points(data)
        return await ctx.send(embed=clean_embed("💀 Failed", f"{ctx.author.mention} {random.choice(messages)}\nYou lost **{loss:,}** points.\nBalance: **{int(rec['points']):,}**", ERROR_COLOR))

    reward = random.randint(reward_min, reward_max)
    rec = apply_points_reward(data, ctx.guild.id, ctx.author.id, reward, key)
    save_points(data)
    await ctx.send(embed=clean_embed(title, f"{ctx.author.mention} {random.choice(messages)}\nEarned **{reward:,}** points.\nBalance: **{int(rec['points']):,}**", SUCCESS_COLOR))

@bot.command(name="daily")
async def daily(ctx):
    await cooldown_reward_command(
        ctx,
        key="daily_last",
        cooldown=DAILY_COOLDOWN_SECONDS,
        reward_min=600,
        reward_max=1200,
        title="🌤️ Daily Claimed",
        messages=["claimed their daily smoke drop.", "picked up today's reward.", "checked in and got paid."]
    )

@bot.command(name="weekly")
async def weekly(ctx):
    await cooldown_reward_command(
        ctx,
        key="weekly_last",
        cooldown=WEEKLY_COOLDOWN_SECONDS,
        reward_min=3500,
        reward_max=7000,
        title="📦 Weekly Claimed",
        messages=["claimed their weekly crate.", "opened a weekly reward box.", "collected the weekly payout."]
    )

@bot.command(name="work")
async def work(ctx):
    await cooldown_reward_command(
        ctx,
        key="work_last",
        cooldown=WORK_COOLDOWN_SECONDS,
        reward_min=150,
        reward_max=450,
        title="🧰 Work Complete",
        messages=["finished a quick shift.", "handled shop errands.", "completed a paid task."]
    )

@bot.command(name="beg")
async def beg(ctx):
    await cooldown_reward_command(
        ctx,
        key="beg_last",
        cooldown=BEG_COOLDOWN_SECONDS,
        reward_min=40,
        reward_max=180,
        title="🪙 Beg Reward",
        messages=["got blessed by a random member.", "found spare points on the floor.", "asked nicely and got something."],
        fail_chance=25,
        fail_loss_min=0,
        fail_loss_max=0
    )

@bot.command(name="search", aliases=["scavenge"])
async def search_points(ctx):
    await cooldown_reward_command(
        ctx,
        key="search_last",
        cooldown=SEARCH_COOLDOWN_SECONDS,
        reward_min=120,
        reward_max=500,
        title="🔎 Search Complete",
        messages=["searched the server and found points.", "found a hidden stash.", "checked the shop shelves and found extra points."]
    )

@bot.command(name="fish")
async def fish(ctx):
    await cooldown_reward_command(
        ctx,
        key="fish_last",
        cooldown=FISH_COOLDOWN_SECONDS,
        reward_min=100,
        reward_max=650,
        title="🎣 Fishing Trip",
        messages=["caught something valuable.", "pulled up a rare fish.", "sold their catch for points."],
        fail_chance=15,
        fail_loss_min=0,
        fail_loss_max=0
    )

@bot.command(name="crime", aliases=["rob"])
async def crime(ctx):
    await cooldown_reward_command(
        ctx,
        key="crime_last",
        cooldown=CRIME_COOLDOWN_SECONDS,
        reward_min=600,
        reward_max=1800,
        title="🕶️ Risky Job Complete",
        messages=["pulled off a risky job.", "got away with a clean payout.", "made a sketchy profit."],
        fail_chance=35,
        fail_loss_min=150,
        fail_loss_max=700
    )

@bot.command(name="trivia")
async def trivia(ctx):
    questions = [
        "What is one way to earn passive points in this server?",
        "Which command shows the reward shop?",
        "Which command lets you redeem a reward?",
        "What should you open after buying a staff-approved reward?"
    ]
    await cooldown_reward_command(
        ctx,
        key="trivia_last",
        cooldown=TRIVIA_COOLDOWN_SECONDS,
        reward_min=100,
        reward_max=300,
        title="🧠 Trivia Reward",
        messages=[f"answered a trivia prompt: **{random.choice(questions)}**"],
    )

@bot.command(name="buy", aliases=["redeem"])
async def buy(ctx, item_id: str):
    if ctx.guild is None:
        return
    item_id = item_id.lower()
    data = load_points(); ensure_points_user(data, ctx.guild.id, ctx.author.id); ensure_points_guild(data, ctx.guild.id)
    items = data[str(ctx.guild.id)]["shop_items"]; item = items.get(item_id)
    if not item:
        return await ctx.send(embed=clean_embed("Unknown Item", "That item does not exist. Use `*smokeshop` to see item IDs.", ERROR_COLOR))
    price = int(item.get("price", 0)); rec = data[str(ctx.guild.id)]["users"][str(ctx.author.id)]
    if int(rec.get("points", 0)) < price:
        return await ctx.send(embed=clean_embed("Not Enough Points", f"You need **{price - int(rec.get('points', 0)):,}** more points for **{item.get('name', item_id)}**.", ERROR_COLOR))
    stock = item.get("stock", "∞")
    if str(stock) != "∞":
        stock_int = int(stock) if str(stock).isdigit() else 0
        if stock_int <= 0:
            return await ctx.send(embed=clean_embed("Out of Stock", "This reward is currently out of stock.", ERROR_COLOR))
        item["stock"] = stock_int - 1
    rec["points"] = int(rec.get("points", 0)) - price; rec["spent_total"] = int(rec.get("spent_total", 0)) + price
    save_points(data)
    embed = clean_embed("✅ Reward Purchased", f"{ctx.author.mention} bought **{item.get('name', item_id)}** for **{price:,}** points.", SUCCESS_COLOR)
    embed.add_field(name="Item ID", value=f"`{item_id}`", inline=True)
    embed.add_field(name="New Balance", value=f"`{int(rec['points']):,}`", inline=True)
    embed.add_field(name="Next Step", value="Open a ticket if this reward needs staff approval or manual claiming.", inline=False)
    await ctx.send(embed=embed)

@bot.group(name="pointadmin", aliases=["pa"], invoke_without_command=True)
async def pointadmin(ctx):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    await ctx.send("Use `*pointadmin add/remove/set @user amount`, `*pointadmin chatpoints on/off`, `*pointadmin replybonus on/off`, or `*pointadmin attachmentbonus on/off`.")

@pointadmin.command(name="add")
async def pointadmin_add(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    bal = add_points(ctx.guild.id, member.id, abs(amount))
    await ctx.send(embed=clean_embed("Points Added", f"Added `{abs(amount):,}` points to {member.mention}.\nBalance: `{bal:,}`", SUCCESS_COLOR))

@pointadmin.command(name="remove")
async def pointadmin_remove(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    bal = add_points(ctx.guild.id, member.id, -abs(amount))
    await ctx.send(embed=clean_embed("Points Removed", f"Removed `{abs(amount):,}` points from {member.mention}.\nBalance: `{bal:,}`", SUCCESS_COLOR))

@pointadmin.command(name="set")
async def pointadmin_set(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    bal = set_points(ctx.guild.id, member.id, amount)
    await ctx.send(embed=clean_embed("Points Set", f"Set {member.mention}'s balance to `{bal:,}` points.", SUCCESS_COLOR))

@pointadmin.command(name="chatpoints")
async def pointadmin_chatpoints(ctx, state: str):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    data = load_points(); ensure_points_guild(data, ctx.guild.id)
    data[str(ctx.guild.id)]["settings"]["chat_points"] = state.lower() in ("on", "enable", "enabled", "true")
    save_points(data)
    await ctx.send(embed=clean_embed("Chat Points Updated", f"Chat point earning is now **{'ON' if data[str(ctx.guild.id)]['settings']['chat_points'] else 'OFF'}**.", SUCCESS_COLOR))

@pointadmin.command(name="replybonus")
async def pointadmin_replybonus(ctx, state: str):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    data = load_points(); ensure_points_guild(data, ctx.guild.id)
    data[str(ctx.guild.id)]["settings"]["reply_bonus"] = state.lower() in ("on", "enable", "enabled", "true")
    save_points(data)
    await ctx.send(embed=clean_embed("Reply Bonus Updated", f"Reply bonus is now **{'ON' if data[str(ctx.guild.id)]['settings']['reply_bonus'] else 'OFF'}**.", SUCCESS_COLOR))

@pointadmin.command(name="attachmentbonus")
async def pointadmin_attachmentbonus(ctx, state: str):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("You need administrator permission.")
    data = load_points(); ensure_points_guild(data, ctx.guild.id)
    data[str(ctx.guild.id)]["settings"]["attachment_bonus"] = state.lower() in ("on", "enable", "enabled", "true")
    save_points(data)
    await ctx.send(embed=clean_embed("Attachment Bonus Updated", f"Attachment bonus is now **{'ON' if data[str(ctx.guild.id)]['settings']['attachment_bonus'] else 'OFF'}**.", SUCCESS_COLOR))


if not TOKEN:
    print("Set TOKEN and run again.")
else:
    bot.run(TOKEN)