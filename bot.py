from __future__ import annotations

import os
import re
import json
import time
import asyncio
import datetime as dt
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =========================================================
# BASIC SETTINGS
# =========================================================
TOKEN = os.getenv("TOKEN")
DATA_DIR = Path("data")
INVALID_DIR = DATA_DIR / "invalid_vanities"
CONFIG_FILE = DATA_DIR / "config.json"
LISTS_FILE = DATA_DIR / "lists.json"
WATCHES_FILE = DATA_DIR / "watches.json"
CLAIMS_FILE = DATA_DIR / "claims.json"
ATTEMPTS_FILE = DATA_DIR / "attempts.json"
VALUE_LOGS_FILE = DATA_DIR / "value_logs.json"

CHECK_DELAY = float(os.getenv("CHECK_DELAY", "3"))
WATCH_INTERVAL_MINUTES = int(os.getenv("WATCH_INTERVAL_MINUTES", "10"))
LEADERBOARD_REFRESH_MINUTES = int(os.getenv("LEADERBOARD_REFRESH_MINUTES", "10"))
MAX_MANUAL_CODES = int(os.getenv("MAX_MANUAL_CODES", "1000"))
MAX_LIST_CODES = int(os.getenv("MAX_LIST_CODES", "2500"))

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

DARK = discord.Color.from_rgb(35, 35, 42)
GREEN = discord.Color.from_rgb(62, 180, 100)
RED = discord.Color.from_rgb(220, 75, 75)
GOLD = discord.Color.from_rgb(245, 185, 75)
PURPLE = discord.Color.from_rgb(155, 95, 255)
BLUE = discord.Color.from_rgb(90, 150, 255)

run_lock = asyncio.Lock()
stop_requested = False

# =========================================================
# STORAGE HELPERS
# =========================================================
def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    INVALID_DIR.mkdir(parents=True, exist_ok=True)
    for n in range(1, 33):
        (INVALID_DIR / f"invalid_{n}_letters.txt").touch(exist_ok=True)


def load_json(path: Path, default):
    ensure_dirs()
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def gkey(guild_id: int) -> str:
    return str(guild_id)


def now_ts() -> int:
    return int(time.time())


def make_id() -> str:
    return str(int(time.time() * 1000))


def embed(title: str, desc: str = "", color: discord.Color = DARK) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.timestamp = discord.utils.utcnow()
    return e


def money(value: float) -> str:
    return f"${float(value):,.2f}"


def clean_code(text: str) -> str:
    text = str(text or "").strip().lower()
    prefixes = (
        "https://discord.gg/", "http://discord.gg/", "discord.gg/",
        "https://discord.com/invite/", "http://discord.com/invite/", "discord.com/invite/",
    )
    for prefix in prefixes:
        text = text.replace(prefix, "")
    text = text.strip().strip("/")
    return re.sub(r"[^a-z0-9_-]", "", text)[:32]


def parse_codes(raw: str) -> List[str]:
    out, seen = [], set()
    for item in re.split(r"[,\n\s]+", raw or ""):
        code = clean_code(item)
        if code and code not in seen:
            out.append(code)
            seen.add(code)
    return out


def parse_role_ids(raw: Optional[str]) -> List[int]:
    ids = []
    for item in re.findall(r"\d{15,25}", raw or ""):
        rid = int(item)
        if rid not in ids:
            ids.append(rid)
    return ids


def role_mentions(guild: discord.Guild, role_ids: List[int]) -> str:
    return " ".join(role.mention for rid in role_ids if (role := guild.get_role(int(rid))))


def parse_money(raw: str) -> Tuple[Optional[float], Optional[str]]:
    text = str(raw or "").replace("$", "").replace(",", "").strip()
    try:
        value = round(float(text), 2)
    except Exception:
        return None, "Use a valid price/value like `25`, `25.50`, or `$25`."
    if value < 0:
        return None, "Value cannot be negative."
    return value, None


def parse_date(date_text: str) -> Tuple[Optional[int], Optional[str]]:
    text = str(date_text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not m:
        return None, "Use date format `YYYY-MM-DD`, like `2026-05-01`."
    y, mo, d = map(int, m.groups())
    try:
        date = dt.datetime(y, mo, d, 12, 0, 0, tzinfo=dt.timezone.utc)
        return int(date.timestamp()), None
    except ValueError:
        return None, "That date does not exist."


def week_start_ts(reference_ts: Optional[int] = None) -> int:
    ref = dt.datetime.fromtimestamp(reference_ts or now_ts(), tz=dt.timezone.utc)
    start = ref - dt.timedelta(days=ref.weekday(), hours=ref.hour, minutes=ref.minute, seconds=ref.second, microseconds=ref.microsecond)
    return int(start.timestamp())

# =========================================================
# CONFIG / DATA MODELS
# =========================================================
def config(guild_id: int) -> dict:
    data = load_json(CONFIG_FILE, {})
    return data.setdefault(gkey(guild_id), {
        "manager_users": [],
        "manager_roles": [],
        "valid_channel_id": None,
        "invalid_channel_id": None,
        "hunter_log_channel_id": None,
        "value_log_channel_id": None,
        "leaderboard_channel_id": None,
        "leaderboard_message_id": None,
        "leaderboard_refresh_minutes": LEADERBOARD_REFRESH_MINUTES,
        "cross_server_update_guild_id": None,
        "cross_server_update_channel_id": None,
        "cross_server_ping_everyone": True,
        "elite_hunter_role_name": "elite hunter",
        "ping_role_ids": [],
        "hunter_ping_role_id": None,
        "delay_seconds": CHECK_DELAY,
    })


def save_config(guild_id: int, cfg: dict) -> None:
    data = load_json(CONFIG_FILE, {})
    data[gkey(guild_id)] = cfg
    save_json(CONFIG_FILE, data)


def lists_for(guild_id: int) -> dict:
    data = load_json(LISTS_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_lists(guild_id: int, lists: dict) -> None:
    data = load_json(LISTS_FILE, {})
    data[gkey(guild_id)] = lists
    save_json(LISTS_FILE, data)


def watches_for(guild_id: int) -> dict:
    data = load_json(WATCHES_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_watches(guild_id: int, watches: dict) -> None:
    data = load_json(WATCHES_FILE, {})
    data[gkey(guild_id)] = watches
    save_json(WATCHES_FILE, data)


def claims_for(guild_id: int) -> list:
    data = load_json(CLAIMS_FILE, {})
    return data.setdefault(gkey(guild_id), [])


def save_claims(guild_id: int, claims: list) -> None:
    data = load_json(CLAIMS_FILE, {})
    data[gkey(guild_id)] = claims
    save_json(CLAIMS_FILE, data)


def attempts_for(guild_id: int) -> list:
    data = load_json(ATTEMPTS_FILE, {})
    return data.setdefault(gkey(guild_id), [])


def save_attempts(guild_id: int, attempts: list) -> None:
    data = load_json(ATTEMPTS_FILE, {})
    data[gkey(guild_id)] = attempts
    save_json(ATTEMPTS_FILE, data)


def value_logs_for(guild_id: int) -> list:
    data = load_json(VALUE_LOGS_FILE, {})
    return data.setdefault(gkey(guild_id), [])


def save_value_logs(guild_id: int, logs: list) -> None:
    data = load_json(VALUE_LOGS_FILE, {})
    data[gkey(guild_id)] = logs
    save_json(VALUE_LOGS_FILE, data)

# =========================================================
# PERMISSIONS
# =========================================================
def is_adminish(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild


def has_manager_access(member: discord.Member) -> bool:
    if is_adminish(member):
        return True
    cfg = config(member.guild.id)
    if str(member.id) in {str(x) for x in cfg.get("manager_users", [])}:
        return True
    member_roles = {str(role.id) for role in member.roles}
    allowed = {str(x) for x in cfg.get("manager_roles", [])}
    return bool(member_roles & allowed)


async def require_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not is_adminish(interaction.user):
        await interaction.response.send_message("You need Manage Server or Administrator.", ephemeral=True)
        return False
    return True


async def require_manager(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not has_manager_access(interaction.user):
        await interaction.response.send_message("You need vanity manager access, Manage Server, or Administrator.", ephemeral=True)
        return False
    return True

# =========================================================
# INVALID FILES
# =========================================================
def invalid_path(length: int) -> Path:
    ensure_dirs()
    return INVALID_DIR / f"invalid_{length}_letters.txt"


def read_invalid(length: int) -> set[str]:
    try:
        return {clean_code(x) for x in invalid_path(length).read_text(encoding="utf-8").splitlines() if clean_code(x)}
    except Exception:
        return set()


def write_invalid(length: int, values: set[str]) -> None:
    invalid_path(length).write_text("\n".join(sorted(values)) + ("\n" if values else ""), encoding="utf-8")


def invalid_file_counts() -> Dict[int, int]:
    return {n: len(read_invalid(n)) for n in range(1, 33)}

# =========================================================
# CLAIM / VALUE STATS
# =========================================================
def find_claim(claims: list, claim_id: Optional[str] = None, hunter_id: Optional[int] = None, code: Optional[str] = None) -> Optional[dict]:
    code = clean_code(code or "") if code else None
    for claim in reversed(claims):
        if claim_id and str(claim.get("id")) == str(claim_id):
            return claim
        if hunter_id and code and int(claim.get("user_id", 0)) == int(hunter_id) and clean_code(claim.get("code")) == code:
            return claim
    return None


def _empty_hunter_stats() -> dict:
    return {
        "claims": 0,
        "attempt_sessions": 0,
        "rate_limits": 0,
        "total_attempts": 0,
        "total_value": 0.0,
        "total_cut": 0.0,
        "most_valuable_claim": None,
        "last_claim_ts": None,
        "last_attempt_ts": None,
        "codes": [],
    }


def calculate_hunter_stats(guild_id: int) -> Dict[str, dict]:
    stats: Dict[str, dict] = {}

    # Attempt-only logs count toward total effort, even when no vanity was claimed.
    for row in attempts_for(guild_id):
        uid = str(row.get("user_id"))
        st = stats.setdefault(uid, _empty_hunter_stats())
        st["attempt_sessions"] += 1
        st["total_attempts"] += int(row.get("total_tried", 0))
        st["rate_limits"] += 1 if row.get("rate_limited") else 0
        st["last_attempt_ts"] = max(int(st.get("last_attempt_ts") or 0), int(row.get("logged_ts") or 0))

    # Claims also count toward effort because /hunter_claim includes attempts used for that pull.
    for claim in claims_for(guild_id):
        uid = str(claim.get("user_id"))
        st = stats.setdefault(uid, _empty_hunter_stats())
        st["claims"] += 1
        st["total_attempts"] += int(claim.get("total_tried", 0))
        st["last_claim_ts"] = max(int(st.get("last_claim_ts") or 0), int(claim.get("claimed_ts") or 0))
        code = clean_code(claim.get("code", ""))
        if code and code not in st["codes"]:
            st["codes"].append(code)
        value = float(claim.get("value") or 0)
        cut = float(claim.get("hunter_cut") or 0)
        st["total_value"] = round(float(st["total_value"]) + value, 2)
        st["total_cut"] = round(float(st["total_cut"]) + cut, 2)
        best = st.get("most_valuable_claim")
        if value > 0 and (not best or value > float(best.get("value", 0))):
            st["most_valuable_claim"] = {
                "code": code,
                "value": value,
                "claim_id": claim.get("id"),
                "claimed_ts": claim.get("claimed_ts"),
            }
    return stats

def best_weekly_claim(guild_id: int) -> Optional[dict]:
    start = week_start_ts()
    best = None
    for claim in claims_for(guild_id):
        claimed_ts = int(claim.get("claimed_ts") or claim.get("logged_ts") or 0)
        value = float(claim.get("value") or 0)
        if claimed_ts >= start and value > 0:
            if not best or value > float(best.get("value", 0)):
                best = claim
    return best


def top_claim_lines(guild: discord.Guild, limit: int = 10) -> List[str]:
    stats = calculate_hunter_stats(guild.id)
    ranked = sorted(stats.items(), key=lambda kv: (int(kv[1].get("claims", 0)), float(kv[1].get("total_value", 0)), int(kv[1].get("total_attempts", 0))), reverse=True)[:limit]
    lines = []
    for pos, (uid, st) in enumerate(ranked, start=1):
        medal = "🥇" if pos == 1 else "🥈" if pos == 2 else "🥉" if pos == 3 else f"`#{pos}`"
        best = st.get("most_valuable_claim")
        best_text = f"`discord.gg/{best['code']}` ({money(float(best['value']))})" if best else "`No value yet`"
        sessions = int(st.get('attempt_sessions', 0))
        lines.append(
            f"{medal} <@{uid}> — **{int(st.get('claims', 0))}** claims • "
            f"**{int(st.get('total_attempts', 0)):,}** attempts • "
            f"**{sessions:,}** no-pull sessions • best {best_text}"
        )
    return lines

# =========================================================
# EMBED BUILDERS
# =========================================================
def claim_embed(guild: discord.Guild, claim: dict) -> discord.Embed:
    e = embed("🏷️ Vanity Claim Logged", color=GOLD)
    e.description = (
        f"**Vanity:** `discord.gg/{claim.get('code')}`\n"
        f"**Hunter:** <@{claim.get('user_id')}>\n"
        f"**Claim Date:** <t:{int(claim.get('claimed_ts', now_ts()))}:D>\n"
        f"**Vanities Tried:** `{int(claim.get('total_tried', 0)):,}`\n"
        f"**Logged:** <t:{int(claim.get('logged_ts', now_ts()))}:R>"
    )
    if float(claim.get("value") or 0) > 0:
        e.add_field(
            name="Value Added",
            value=(
                f"**Value:** `{money(float(claim.get('value', 0)))}`\n"
                f"**Hunter Cut:** `{float(claim.get('cut_percent', 0)):g}%` = `{money(float(claim.get('hunter_cut', 0)))}`"
            ),
            inline=False,
        )
    e.add_field(name="Notes", value=(claim.get("notes") or "No notes.")[:1024], inline=False)
    e.set_footer(text=f"Claim ID: {claim.get('id')} • {guild.name}")
    return e




def attempt_embed(guild: discord.Guild, attempt: dict) -> discord.Embed:
    e = embed("📝 Hunter Attempts Logged", color=BLUE)
    e.description = (
        f"**Hunter:** <@{attempt.get('user_id')}>\n"
        f"**Attempts Tried:** `{int(attempt.get('total_tried', 0)):,}`\n"
        f"**Rate Limited:** `{'Yes' if attempt.get('rate_limited') else 'No'}`\n"
        f"**Logged:** <t:{int(attempt.get('logged_ts', now_ts()))}:R>"
    )
    if attempt.get("list_name"):
        e.add_field(name="List / Batch", value=str(attempt.get("list_name"))[:1024], inline=False)
    e.add_field(name="Notes", value=(attempt.get("notes") or "No notes.")[:1024], inline=False)
    e.set_footer(text=f"Attempt ID: {attempt.get('id')} • {guild.name}")
    return e


def value_update_embed(guild: discord.Guild, claim: dict, updated_by: int, notes: Optional[str] = None) -> discord.Embed:
    e = embed("💸 Claim Value Updated", color=GREEN)
    e.description = (
        f"**Vanity:** `discord.gg/{claim.get('code')}`\n"
        f"**Hunter:** <@{claim.get('user_id')}>\n"
        f"**Value:** `{money(float(claim.get('value', 0)))}`\n"
        f"**Hunter Cut:** `{float(claim.get('cut_percent', 0)):g}%` = `{money(float(claim.get('hunter_cut', 0)))}`\n"
        f"**Owner/Server Cut:** `{money(float(claim.get('owner_cut', 0)))}`\n"
        f"**Updated By:** <@{updated_by}>"
    )
    if notes:
        e.add_field(name="Manager Notes", value=notes[:1024], inline=False)
    e.set_footer(text=f"Claim ID: {claim.get('id')} • {guild.name}")
    return e


def leaderboard_embed(guild: discord.Guild) -> discord.Embed:
    lines = top_claim_lines(guild, 10)
    best = best_weekly_claim(guild.id)
    e = embed("🏆 Vanity Hunter Leaderboard", color=GOLD)
    if best:
        e.description = (
            f"🌟 **Best Claim This Week:** `discord.gg/{best.get('code')}` by <@{best.get('user_id')}> "
            f"— **{money(float(best.get('value', 0)))}**\n"
            f"Claimed <t:{int(best.get('claimed_ts') or best.get('logged_ts') or now_ts())}:D>\n\n"
            + ("\n".join(lines) if lines else "No hunter stats yet.")
        )
    else:
        e.description = "🌟 **Best Claim This Week:** `No valued weekly claim yet.`\n\n" + ("\n".join(lines) if lines else "No hunter stats yet.")
    total_claims = len(claims_for(guild.id))
    total_attempts = sum(int(c.get("total_tried", 0)) for c in claims_for(guild.id)) + sum(int(a.get("total_tried", 0)) for a in attempts_for(guild.id))
    total_value = sum(float(c.get("value") or 0) for c in claims_for(guild.id))
    e.add_field(name="Server Totals", value=f"Claims: `{total_claims:,}` • Attempts: `{total_attempts:,}` • Value: `{money(total_value)}`", inline=False)
    e.set_footer(text="Auto-updates based on logged hunter claims and manager value updates")
    return e


def list_update_embed(guild: discord.Guild, label: str, stats: dict) -> discord.Embed:
    counts = invalid_file_counts()
    active_counts = [f"`{n}` letters: `{count}`" for n, count in counts.items() if count][:10]
    e = embed("📋 Vanity Lists Updated", color=BLUE)
    e.description = (
        "**Lists updated — make sure to go attempt.**\n\n"
        f"**Server:** `{guild.name}`\n"
        f"**Checked List:** `{label}`\n"
        f"**Processed:** `{int(stats.get('processed', 0)):,}`\n"
        f"**New Targets:** `{len(stats.get('recent', [])):,}`\n"
        f"**Became Valid Again:** `{len(stats.get('became_valid', [])):,}`\n"
        f"**Updated:** <t:{now_ts()}:R>"
    )
    if stats.get("recent"):
        e.add_field(name="Fresh Targets", value="\n".join(f"`discord.gg/{x}`" for x in stats["recent"][:15]), inline=False)
    e.add_field(name="Current Saved Invalid Files", value="\n".join(active_counts) if active_counts else "No saved invalids yet.", inline=False)
    e.set_footer(text="Use the updated lists to start attempting claims")
    return e

# =========================================================
# DISCORD INVITE CHECKING
# =========================================================
async def invite_status(code: str) -> str:
    try:
        await bot.fetch_invite(code)
        return "valid"
    except discord.NotFound:
        return "invalid"
    except discord.Forbidden:
        return "error: forbidden"
    except discord.HTTPException as e:
        return f"error: HTTP {getattr(e, 'status', 'unknown')}"
    except Exception as e:
        return f"error: {type(e).__name__}"


def compact_codes(codes: List[str], limit: int = 25) -> str:
    if not codes:
        return "None"
    shown = [f"`discord.gg/{c}`" for c in codes[:limit]]
    if len(codes) > limit:
        shown.append(f"...and {len(codes) - limit} more")
    return "\n".join(shown)


async def send_cross_server_update(source_guild: discord.Guild, label: str, stats: dict) -> None:
    cfg = config(source_guild.id)
    target_channel_id = cfg.get("cross_server_update_channel_id")
    if not target_channel_id:
        return
    channel = bot.get_channel(int(target_channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(target_channel_id))
        except Exception:
            return
    content = "@everyone lists updated, make sure to go attempt" if cfg.get("cross_server_ping_everyone", True) else "lists updated, make sure to go attempt"
    try:
        await channel.send(content=content, embed=list_update_embed(source_guild, label, stats), allowed_mentions=discord.AllowedMentions(everyone=True))
    except Exception:
        pass


async def run_check(guild: discord.Guild, label: str, codes: List[str], valid_channel, invalid_channel, ping_role_ids: List[int], delay: float, status_msg=None, alert_only_recent: bool = False) -> dict:
    global stop_requested
    before = {n: read_invalid(n) for n in range(1, 33)}
    current = {n: set(before[n]) for n in range(1, 33)}
    stats = {"valid": [], "invalid": [], "recent": [], "became_valid": [], "errors": [], "processed": 0, "start": now_ts(), "stopped": False}
    touched = set()

    for i, code in enumerate(codes, start=1):
        if stop_requested:
            stats["stopped"] = True
            break
        length = len(code)
        touched.add(length)
        status = await invite_status(code)
        stats["processed"] = i
        if status == "valid":
            stats["valid"].append(code)
            if length in current and code in current[length]:
                current[length].remove(code)
                stats["became_valid"].append(code)
        elif status == "invalid":
            stats["invalid"].append(code)
            if length in current:
                if code not in before[length]:
                    stats["recent"].append(code)
                current[length].add(code)
        else:
            stats["errors"].append(f"{code}: {status}")

        if status_msg and (i == 1 or i % 10 == 0 or i == len(codes)):
            try:
                await status_msg.edit(content=f"Checking `{label}` — `{i}/{len(codes)}` done • new targets `{len(stats['recent'])}` • errors `{len(stats['errors'])}`")
            except Exception:
                pass
        if i < len(codes):
            await asyncio.sleep(max(1.0, float(delay)))

    for length in touched:
        if length in current:
            write_invalid(length, current[length])

    quiet = alert_only_recent and not stats["recent"] and not stats["became_valid"] and not stats["errors"]
    if not quiet:
        runtime = now_ts() - stats["start"]
        desc = (
            f"**List:** `{label}`\n"
            f"**Processed:** `{stats['processed']:,}`\n"
            f"**Valid:** `{len(stats['valid']):,}` • **Invalid:** `{len(stats['invalid']):,}` • **Errors:** `{len(stats['errors']):,}`\n"
            f"**New Invalid Targets:** `{len(stats['recent']):,}`\n"
            f"**Became Valid Again:** `{len(stats['became_valid']):,}`\n"
            f"**Runtime:** `{runtime}s`"
        )
        await valid_channel.send(embed=embed("✅ Vanity Results — Valid", desc + "\n\n" + compact_codes(stats["valid"]), GREEN))
        target_embed = embed("🔥 Vanity Results — Targets", desc, RED if stats["recent"] else PURPLE)
        target_embed.add_field(name="New Targets", value=compact_codes(stats["recent"]), inline=False)
        target_embed.add_field(name="All Invalid This Run", value=compact_codes(stats["invalid"]), inline=False)
        if stats["errors"]:
            target_embed.add_field(name="Errors", value="\n".join(stats["errors"][:10]), inline=False)
        await invalid_channel.send(content=role_mentions(guild, ping_role_ids) or None, embed=target_embed, allowed_mentions=discord.AllowedMentions(roles=True))

    await send_cross_server_update(guild, label, stats)
    return stats

# =========================================================
# BACKGROUND TASKS
# =========================================================
@tasks.loop(seconds=30)
async def watch_loop():
    if run_lock.locked():
        return
    all_watches = load_json(WATCHES_FILE, {})
    for guild_id, watches in list(all_watches.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        changed = False
        for name, watch in list(watches.items()):
            if now_ts() < int(watch.get("next_run", 0)):
                continue
            codes = lists_for(guild.id).get(name, [])[:MAX_LIST_CODES]
            valid_channel = guild.get_channel(int(watch.get("valid_channel_id") or 0))
            invalid_channel = guild.get_channel(int(watch.get("invalid_channel_id") or 0))
            if codes and valid_channel and invalid_channel:
                async with run_lock:
                    await run_check(guild, f"watch:{name}", codes, valid_channel, invalid_channel, [int(x) for x in watch.get("ping_role_ids", [])], CHECK_DELAY, None, True)
            watch["last_run"] = now_ts()
            watch["next_run"] = now_ts() + int(watch.get("interval_minutes", WATCH_INTERVAL_MINUTES)) * 60
            watches[name] = watch
            changed = True
        if changed:
            all_watches[guild_id] = watches
    save_json(WATCHES_FILE, all_watches)


@watch_loop.before_loop
async def before_watch_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=1)
async def leaderboard_loop():
    all_config = load_json(CONFIG_FILE, {})
    for guild_id, cfg in list(all_config.items()):
        channel_id = cfg.get("leaderboard_channel_id")
        if not channel_id:
            continue
        last = int(cfg.get("leaderboard_last_update", 0) or 0)
        refresh = max(1, int(cfg.get("leaderboard_refresh_minutes", LEADERBOARD_REFRESH_MINUTES) or LEADERBOARD_REFRESH_MINUTES))
        if now_ts() - last < refresh * 60:
            continue
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        channel = guild.get_channel(int(channel_id))
        if not channel:
            continue
        try:
            lb_embed = leaderboard_embed(guild)
            message_id = cfg.get("leaderboard_message_id")
            if message_id:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    await msg.edit(embed=lb_embed)
                except Exception:
                    msg = await channel.send(embed=lb_embed)
                    cfg["leaderboard_message_id"] = msg.id
            else:
                msg = await channel.send(embed=lb_embed)
                cfg["leaderboard_message_id"] = msg.id
            cfg["leaderboard_last_update"] = now_ts()
            all_config[guild_id] = cfg
        except Exception:
            pass
    save_json(CONFIG_FILE, all_config)


@leaderboard_loop.before_loop
async def before_leaderboard_loop():
    await bot.wait_until_ready()

# =========================================================
# EVENTS
# =========================================================
@bot.event
async def on_ready():
    ensure_dirs()
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Slash sync failed: {e}")
    if not watch_loop.is_running():
        watch_loop.start()
    if not leaderboard_loop.is_running():
        leaderboard_loop.start()

# =========================================================
# HELP / SETUP COMMANDS
# =========================================================
@bot.tree.command(name="help", description="Beginner guide for using the vanity hunter panel system.")
async def help_command(interaction: discord.Interaction):
    e = embed("📘 Vanity Hunting Guide", color=PURPLE)
    e.description = (
        "**Welcome to the Vanity Hunting System**\n\n"
        "The bot now uses clean panels instead of making hunters and managers remember a bunch of separate commands. "
        "Hunters should use the **Hunter Panel** for attempts, claims, edits, removals, stats, and leaderboard. "
        "Managers should use the **Manager Panel** for valuing claims, checking logs, setup, and manager tools.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "**⚠️ IMPORTANT RULE**\n"
        "**NOT logging correct attempts or claims WILL get you suspended from the job.**\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    e.add_field(
        name="✅ What New Hunters Should Do",
        value=(
            "```\n"
            "1. Open the Hunter Panel.\n"
            "2. Press Log Attempt if you tried vanities but did not pull one.\n"
            "3. Press Log Claim if you successfully claimed a vanity.\n"
            "4. Use My Stats to check total attempts, claims, and claimed links.\n"
            "5. Use Edit Claim or Remove Claim only for your own logs.\n"
            "```"
        ),
        inline=False,
    )
    e.add_field(
        name="🏹 Hunter Panel Buttons",
        value=(
            "**Log Attempt** — log attempts even if you got no vanity.\n"
            "**Log Claim** — log a successful pull like `discord.gg/make`.\n"
            "**My Stats** — see total attempts, claims pulled, value, cut, and claimed vanities.\n"
            "**Leaderboard** — view the top hunters.\n"
            "**Edit Claim** — fix your own claim by Claim ID.\n"
            "**Remove Claim** — remove your own incorrect claim."
        ),
        inline=False,
    )
    e.add_field(
        name="📌 Manager Panel Buttons",
        value=(
            "**Value Claim** — add/update claim value and hunter cut by Claim ID.\n"
            "**Recent Claims** — view recent claim IDs and hunter logs.\n"
            "**Value History** — review recent manager value updates.\n"
            "**Setup Logs** — set claim log channel, ping role, value log, and leaderboard channel.\n"
            "**Leaderboard** — view current rankings.\n"
            "**Manager Guide** — quick workflow reminder."
        ),
        inline=False,
    )
    e.add_field(
        name="📍 Main Commands To Remember",
        value=(
            "`/hunter_panel` — opens your private hunter control panel.\n"
            "`/manager_panel` — opens the private manager control panel.\n"
            "`/post_hunter_panel` — managers can post a public hunter panel.\n"
            "`/post_manager_panel` — admins can post a public manager panel.\n"
            "`/vanity_help` — shows the simplified command menu."
        ),
        inline=False,
    )
    e.set_footer(text="Use the panels first • Clean logs = trusted hunters • Bad logs can get you suspended")
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="vanity_help", description="Show the simplified vanity bot command menu.")
async def vanity_help(interaction: discord.Interaction):
    e = embed("📚 Vanity Bot Command Menu", color=PURPLE)
    e.description = (
        "The main hunter/manager features were moved into button panels so the server stays cleaner and easier to use.\n\n"
        "**⚠️ Logging Rule:** **NOT logging correct attempts or claims WILL get you suspended from the job.**"
    )
    e.add_field(
        name="Main Panel Commands",
        value=(
            "`/hunter_panel` — private panel for attempts, claims, edits, removals, stats, and leaderboard.\n"
            "`/manager_panel` — private manager panel for valuing claims, logs, setup, and leaderboard.\n"
            "`/post_hunter_panel` — post a public hunter panel in a channel.\n"
            "`/post_manager_panel` — post a public manager panel in a channel."
        ),
        inline=False,
    )
    e.add_field(
        name="Public Info Commands",
        value=(
            "`/help` — beginner guide for the panel system.\n"
            "`/vanity_help` — simplified command menu.\n"
            "`/info_vanity_job` — full job guide.\n"
            "`/info_roles` — manager and Elite Hunter role information."
        ),
        inline=False,
    )
    e.add_field(
        name="Vanity Checking Commands",
        value=(
            "`/vanity_check` — check pasted vanities.\n"
            "`/vanity_add_list` — save a named vanity list.\n"
            "`/vanity_run_list` — run a saved list.\n"
            "`/vanity_stop` — stop the current run.\n"
            "`/vanity_lists` — view saved lists.\n"
            "`/vanity_watch_start` — scheduled checks.\n"
            "`/vanity_watch_stop` — stop scheduled checks.\n"
            "`/vanity_watches` — view active watches."
        ),
        inline=False,
    )
    e.add_field(
        name="Setup / Access Commands",
        value=(
            "`/vanity_setup` — set vanity result channels and delay.\n"
            "`/list_update_setup` — send cross-server list update alerts.\n"
            "`/leaderboard_setup` — create/update the auto leaderboard.\n"
            "`/vanity_access_add_user` — give manager access to a user.\n"
            "`/vanity_access_add_role` — give manager access to a role."
        ),
        inline=False,
    )
    e.set_footer(text="Hunter claim/value/stats commands are now handled through panels.")
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="vanity_setup", description="Set result channels, ping roles, and check delay.")
async def vanity_setup(interaction: discord.Interaction, valid_channel: discord.TextChannel, invalid_channel: discord.TextChannel, ping_roles: Optional[str] = None, delay_seconds: app_commands.Range[float, 1.0, 30.0] = CHECK_DELAY):
    if not await require_admin(interaction):
        return
    cfg = config(interaction.guild.id)
    cfg.update({"valid_channel_id": valid_channel.id, "invalid_channel_id": invalid_channel.id, "ping_role_ids": parse_role_ids(ping_roles), "delay_seconds": float(delay_seconds)})
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Saved. Valid: {valid_channel.mention} • Invalid: {invalid_channel.mention} • Delay: `{delay_seconds}s`", ephemeral=True)


@bot.tree.command(name="list_update_setup", description="Send clean list-update alerts to another channel/server after checks finish.")
@app_commands.describe(
    target_channel_id="Channel ID in the other server. The bot must be in that server and able to send messages.",
    ping_everyone="Whether to ping @everyone with the update notification."
)
async def list_update_setup(interaction: discord.Interaction, target_channel_id: str, ping_everyone: bool = True):
    if not await require_admin(interaction):
        return
    channel_id_match = re.search(r"\d{15,25}", target_channel_id)
    if not channel_id_match:
        return await interaction.response.send_message("Paste a valid target channel ID.", ephemeral=True)
    channel_id = int(channel_id_match.group(0))
    channel = bot.get_channel(channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return await interaction.response.send_message("I could not find that channel. Make sure the bot is in that server and has access.", ephemeral=True)
    cfg = config(interaction.guild.id)
    cfg["cross_server_update_channel_id"] = channel_id
    cfg["cross_server_update_guild_id"] = getattr(channel, "guild", None).id if getattr(channel, "guild", None) else None
    cfg["cross_server_ping_everyone"] = bool(ping_everyone)
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"List update alerts will post in <#{channel_id}>. Ping everyone: `{ping_everyone}`", ephemeral=True)


@bot.tree.command(name="leaderboard_setup", description="Create or update the auto-refreshing hunter leaderboard embed.")
async def leaderboard_setup(interaction: discord.Interaction, channel: discord.TextChannel, refresh_minutes: app_commands.Range[int, 1, 1440] = LEADERBOARD_REFRESH_MINUTES):
    if not await require_admin(interaction):
        return
    cfg = config(interaction.guild.id)
    cfg["leaderboard_channel_id"] = channel.id
    cfg["leaderboard_refresh_minutes"] = int(refresh_minutes)
    cfg["leaderboard_last_update"] = 0
    save_config(interaction.guild.id, cfg)
    await refresh_leaderboard_now(interaction.guild)
    await interaction.response.send_message(f"Leaderboard set in {channel.mention} and will refresh every `{refresh_minutes}` minutes.", ephemeral=True)


async def refresh_leaderboard_now(guild: discord.Guild) -> None:
    cfg = config(guild.id)
    channel = guild.get_channel(int(cfg.get("leaderboard_channel_id") or 0))
    if not channel:
        return
    lb_embed = leaderboard_embed(guild)
    message_id = cfg.get("leaderboard_message_id")
    try:
        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(embed=lb_embed)
            except Exception:
                msg = await channel.send(embed=lb_embed)
                cfg["leaderboard_message_id"] = msg.id
        else:
            msg = await channel.send(embed=lb_embed)
            cfg["leaderboard_message_id"] = msg.id
        cfg["leaderboard_last_update"] = now_ts()
        save_config(guild.id, cfg)
    except Exception:
        pass


@bot.tree.command(name="vanity_access_add_user", description="Allow a user to use manager vanity commands.")
async def vanity_access_add_user(interaction: discord.Interaction, user: discord.Member):
    if not await require_admin(interaction):
        return
    cfg = config(interaction.guild.id)
    users = [str(x) for x in cfg.get("manager_users", [])]
    if str(user.id) not in users:
        users.append(str(user.id))
    cfg["manager_users"] = users
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {user.mention} as a vanity manager.", ephemeral=True)


@bot.tree.command(name="vanity_access_add_role", description="Allow a role to use manager vanity commands.")
async def vanity_access_add_role(interaction: discord.Interaction, role: discord.Role):
    if not await require_admin(interaction):
        return
    cfg = config(interaction.guild.id)
    roles = [str(x) for x in cfg.get("manager_roles", [])]
    if str(role.id) not in roles:
        roles.append(str(role.id))
    cfg["manager_roles"] = roles
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {role.mention} as a vanity manager role.", ephemeral=True)

# =========================================================
# CHECK COMMANDS
# =========================================================
@bot.tree.command(name="vanity_check", description="Check comma/space separated vanity codes.")
async def vanity_check(interaction: discord.Interaction, codes: str):
    global stop_requested
    if not await require_manager(interaction):
        return
    parsed = parse_codes(codes)[:MAX_MANUAL_CODES]
    if not parsed:
        return await interaction.response.send_message("Paste at least one code.", ephemeral=True)
    if run_lock.locked():
        return await interaction.response.send_message("A check is already running.", ephemeral=True)
    cfg = config(interaction.guild.id)
    valid_channel = interaction.guild.get_channel(int(cfg.get("valid_channel_id") or 0)) or interaction.channel
    invalid_channel = interaction.guild.get_channel(int(cfg.get("invalid_channel_id") or 0)) or interaction.channel
    await interaction.response.send_message(f"Checking `{len(parsed)}` codes...", ephemeral=True)
    msg = await interaction.channel.send(f"Checking `manual` — `0/{len(parsed)}` done")
    async with run_lock:
        stop_requested = False
        await run_check(interaction.guild, "manual", parsed, valid_channel, invalid_channel, cfg.get("ping_role_ids", []), cfg.get("delay_seconds", CHECK_DELAY), msg)
        stop_requested = False


@bot.tree.command(name="vanity_add_list", description="Save or add words to a named list.")
async def vanity_add_list(interaction: discord.Interaction, name: str, codes: str, replace: bool = False):
    if not await require_manager(interaction):
        return
    name = clean_code(name.replace(" ", "-"))[:40]
    parsed = parse_codes(codes)
    if not name or not parsed:
        return await interaction.response.send_message("Use a valid list name and at least one code.", ephemeral=True)
    saved_lists = lists_for(interaction.guild.id)
    base = [] if replace else saved_lists.get(name, [])
    merged = []
    for code in base + parsed:
        if code not in merged:
            merged.append(code)
    saved_lists[name] = merged
    save_lists(interaction.guild.id, saved_lists)
    await interaction.response.send_message(f"Saved `{len(merged):,}` codes in list `{name}`.", ephemeral=True)


@bot.tree.command(name="vanity_lists", description="Show saved vanity lists.")
async def vanity_lists(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    saved_lists = lists_for(interaction.guild.id)
    desc = "\n".join(f"`{name}` — `{len(codes):,}` codes" for name, codes in saved_lists.items()) or "No lists yet."
    await interaction.response.send_message(embed=embed("Saved Vanity Lists", desc, PURPLE), ephemeral=True)


@bot.tree.command(name="vanity_run_list", description="Check a saved vanity list.")
async def vanity_run_list(interaction: discord.Interaction, name: str):
    global stop_requested
    if not await require_manager(interaction):
        return
    name = clean_code(name.replace(" ", "-"))[:40]
    codes = lists_for(interaction.guild.id).get(name, [])[:MAX_LIST_CODES]
    if not codes:
        return await interaction.response.send_message("That list is empty or missing.", ephemeral=True)
    if run_lock.locked():
        return await interaction.response.send_message("A check is already running.", ephemeral=True)
    cfg = config(interaction.guild.id)
    valid_channel = interaction.guild.get_channel(int(cfg.get("valid_channel_id") or 0)) or interaction.channel
    invalid_channel = interaction.guild.get_channel(int(cfg.get("invalid_channel_id") or 0)) or interaction.channel
    await interaction.response.send_message(f"Checking list `{name}` with `{len(codes):,}` codes...", ephemeral=True)
    msg = await interaction.channel.send(f"Checking `{name}` — `0/{len(codes)}` done")
    async with run_lock:
        stop_requested = False
        await run_check(interaction.guild, name, codes, valid_channel, invalid_channel, cfg.get("ping_role_ids", []), cfg.get("delay_seconds", CHECK_DELAY), msg)
        stop_requested = False


@bot.tree.command(name="vanity_stop", description="Stop the current vanity check.")
async def vanity_stop(interaction: discord.Interaction):
    global stop_requested
    if not await require_manager(interaction):
        return
    if not run_lock.locked():
        return await interaction.response.send_message("No check is running.", ephemeral=True)
    stop_requested = True
    await interaction.response.send_message("Stop requested.", ephemeral=True)


@bot.tree.command(name="vanity_watch_start", description="Auto-check a saved list every few minutes.")
async def vanity_watch_start(interaction: discord.Interaction, name: str, interval_minutes: app_commands.Range[int, 5, 1440] = WATCH_INTERVAL_MINUTES):
    if not await require_manager(interaction):
        return
    name = clean_code(name.replace(" ", "-"))[:40]
    if name not in lists_for(interaction.guild.id):
        return await interaction.response.send_message("That list does not exist.", ephemeral=True)
    cfg = config(interaction.guild.id)
    watches = watches_for(interaction.guild.id)
    watches[name] = {
        "enabled": True,
        "interval_minutes": int(interval_minutes),
        "next_run": now_ts() + 5,
        "valid_channel_id": cfg.get("valid_channel_id"),
        "invalid_channel_id": cfg.get("invalid_channel_id"),
        "ping_role_ids": cfg.get("ping_role_ids", []),
    }
    save_watches(interaction.guild.id, watches)
    await interaction.response.send_message(f"Watching `{name}` every `{interval_minutes}` minutes.", ephemeral=True)


@bot.tree.command(name="vanity_watch_stop", description="Stop auto-checking a saved list.")
async def vanity_watch_stop(interaction: discord.Interaction, name: str):
    if not await require_manager(interaction):
        return
    name = clean_code(name.replace(" ", "-"))[:40]
    watches = watches_for(interaction.guild.id)
    watches.pop(name, None)
    save_watches(interaction.guild.id, watches)
    await interaction.response.send_message(f"Stopped watching `{name}`.", ephemeral=True)


@bot.tree.command(name="vanity_watches", description="Show active auto-checks.")
async def vanity_watches(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    watches = watches_for(interaction.guild.id)
    desc = "\n".join(f"`{name}` — every `{w.get('interval_minutes')}` min • next <t:{int(w.get('next_run',0))}:R>" for name, w in watches.items()) or "No active watches."
    await interaction.response.send_message(embed=embed("Active Watches", desc, GOLD), ephemeral=True)

# =========================================================
async def get_claim_log_channel(guild: discord.Guild, fallback_channel=None):
    """Return the configured claim-log channel, falling back safely if needed."""
    cfg = config(guild.id)
    channel_id = cfg.get("hunter_log_channel_id")
    channel = None
    if channel_id:
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(channel_id))
            except Exception:
                channel = None
    return channel or fallback_channel


async def delete_previous_claim_embed(guild: discord.Guild, claim: dict) -> bool:
    """Delete only the previously posted embed/message for this exact claim ID."""
    old_message_id = claim.get("log_message_id")
    old_channel_id = claim.get("log_channel_id")
    if not old_message_id or not old_channel_id:
        return False

    channel = guild.get_channel(int(old_channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(old_channel_id))
        except Exception:
            return False

    try:
        msg = await channel.fetch_message(int(old_message_id))
    except Exception:
        return False

    # Safety check: only delete the old bot embed that belongs to the same Claim ID.
    try:
        if msg.author.id != bot.user.id:
            return False
        footer_text = ""
        if msg.embeds:
            footer_text = msg.embeds[0].footer.text or ""
        if f"Claim ID: {claim.get('id')}" not in footer_text:
            return False
        await msg.delete()
        return True
    except Exception:
        return False


async def send_claim_embed_replacing_previous(
    guild: discord.Guild,
    claim: dict,
    claim_log_embed: discord.Embed,
    *,
    fallback_channel=None,
    ping_role: bool = False,
) -> Optional[discord.Message]:
    """
    Sends a fresh claim embed and deletes only the previous embed saved on this claim.
    This prevents duplicate edited/value-updated claim embeds while leaving unrelated claims alone.
    """
    await delete_previous_claim_embed(guild, claim)
    channel = await get_claim_log_channel(guild, fallback_channel)
    if not channel:
        return None

    cfg = config(guild.id)
    content = None
    allowed = discord.AllowedMentions.none()
    ping_role_id = cfg.get("hunter_ping_role_id")
    if ping_role and ping_role_id:
        role = guild.get_role(int(ping_role_id))
        if role:
            content = role.mention
            allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)

    try:
        msg = await channel.send(content=content, embed=claim_log_embed, allowed_mentions=allowed)
        claim["log_channel_id"] = channel.id
        claim["log_message_id"] = msg.id
        claim["log_updated_ts"] = now_ts()
        return msg
    except Exception:
        return None


# CLAIM COMMANDS
# =========================================================
# =========================================================
# CLEAN PANEL SYSTEM (BUTTONS + MODALS)
# =========================================================
def parse_bool_text(raw: str, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return default
    return text in {"yes", "y", "true", "1", "rl", "rate limited", "ratelimited"}


def panel_hunter_embed(guild: discord.Guild) -> discord.Embed:
    e = embed("🏹 Vanity Hunter Panel", color=PURPLE)
    e.description = (
        "Use the buttons below instead of remembering every command.\n\n"
        "**Log Attempt** — log attempts even when you did **not** pull a vanity.\n"
        "**Log Claim** — log a successful vanity pull.\n"
        "**My Stats** — see total attempts, claims, and claimed vanities.\n"
        "**Edit / Remove** — fix or remove your own claim by Claim ID.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "**⚠️ NOT logging correct attempts or claims WILL get you suspended from the job.**\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    e.set_footer(text=f"{guild.name} • Hunter tools")
    return e


def panel_manager_embed(guild: discord.Guild) -> discord.Embed:
    e = embed("🛠️ Vanity Manager Panel", color=GOLD)
    e.description = (
        "Use this panel to manage hunters, claims, values, logs, and setup.\n\n"
        "**Value Claim** — add sale/value and hunter cut using a Claim ID.\n"
        "**Recent Claims** — view recent claims and IDs.\n"
        "**Value History** — review recent manager value updates.\n"
        "**Setup Logs** — set claim log, ping role, value log, and leaderboard channels by ID.\n"
        "**Leaderboard** — post the current hunter leaderboard.\n\n"
        "Managers can still use slash commands if needed, but this is the cleaner daily workflow."
    )
    e.set_footer(text=f"{guild.name} • Manager tools")
    return e


async def create_claim_from_panel(interaction: discord.Interaction, vanity: str, claimed_date: str, total_tried_raw: str, notes: Optional[str], rate_limited: bool = False, list_name: Optional[str] = None):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    code = clean_code(vanity)
    if not code:
        return await interaction.response.send_message("Use a valid vanity code or invite link.", ephemeral=True)
    try:
        total_tried = int(str(total_tried_raw).replace(",", "").strip())
    except Exception:
        return await interaction.response.send_message("Attempts must be a number, like `250`.", ephemeral=True)
    if total_tried < 0:
        return await interaction.response.send_message("Attempts cannot be negative.", ephemeral=True)
    claimed_ts, err = parse_date(claimed_date)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)

    claim = {
        "id": make_id(),
        "guild_id": interaction.guild.id,
        "user_id": interaction.user.id,
        "code": code,
        "claimed_ts": claimed_ts,
        "logged_ts": now_ts(),
        "total_tried": int(total_tried),
        "rate_limited": bool(rate_limited),
        "list_name": (list_name or "").strip()[:120] or None,
        "notes": (notes or "").strip()[:1000] or None,
        "value": None,
        "cut_percent": None,
        "hunter_cut": None,
        "owner_cut": None,
        "value_updated_by": None,
        "value_updated_ts": None,
    }
    claims = claims_for(interaction.guild.id)
    claims.append(claim)
    save_claims(interaction.guild.id, claims[-5000:])

    log_channel = await get_claim_log_channel(interaction.guild, interaction.channel)
    await send_claim_embed_replacing_previous(interaction.guild, claim, claim_embed(interaction.guild, claim), fallback_channel=log_channel, ping_role=True)
    save_claims(interaction.guild.id, claims[-5000:])

    stats = calculate_hunter_stats(interaction.guild.id).get(str(interaction.user.id), {})
    elite = await maybe_give_elite_hunter(interaction.user, int(stats.get("claims", 0)))
    await refresh_leaderboard_now(interaction.guild)
    msg = f"Logged `discord.gg/{code}` with `{int(total_tried):,}` attempts. Claim ID: `{claim['id']}`."
    if elite:
        msg += f" You earned {elite.mention}."
    await interaction.response.send_message(msg, ephemeral=True)


async def create_attempt_from_panel(interaction: discord.Interaction, total_tried_raw: str, rate_limited_raw: str, vanity: Optional[str], attempted_date: Optional[str], notes: Optional[str]):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    try:
        total_tried = int(str(total_tried_raw).replace(",", "").strip())
    except Exception:
        return await interaction.response.send_message("Attempts must be a number, like `250`.", ephemeral=True)
    if total_tried <= 0:
        return await interaction.response.send_message("Attempts must be at least `1`.", ephemeral=True)

    attempted_ts = now_ts()
    if attempted_date and attempted_date.strip():
        attempted_ts, err = parse_date(attempted_date.strip())
        if err:
            return await interaction.response.send_message(err, ephemeral=True)

    if vanity and clean_code(vanity):
        return await create_claim_from_panel(
            interaction,
            vanity=vanity,
            claimed_date=(attempted_date.strip() if attempted_date and attempted_date.strip() else dt.datetime.fromtimestamp(attempted_ts, tz=dt.timezone.utc).strftime("%Y-%m-%d")),
            total_tried_raw=str(total_tried),
            notes=notes,
            rate_limited=parse_bool_text(rate_limited_raw, True),
        )

    attempt = {
        "id": make_id(),
        "guild_id": interaction.guild.id,
        "user_id": interaction.user.id,
        "attempted_ts": attempted_ts,
        "logged_ts": now_ts(),
        "total_tried": int(total_tried),
        "rate_limited": parse_bool_text(rate_limited_raw, True),
        "list_name": None,
        "notes": (notes or "").strip()[:1000] or None,
    }
    attempts = attempts_for(interaction.guild.id)
    attempts.append(attempt)
    save_attempts(interaction.guild.id, attempts[-10000:])

    log_channel = await get_claim_log_channel(interaction.guild, interaction.channel)
    if log_channel:
        try:
            await log_channel.send(embed=attempt_embed(interaction.guild, attempt))
        except Exception:
            pass
    await refresh_leaderboard_now(interaction.guild)
    await interaction.response.send_message(f"Logged `{int(total_tried):,}` attempts with no vanity claimed. Attempt ID: `{attempt['id']}`.", ephemeral=True)


class HunterAttemptModal(discord.ui.Modal, title="Log Hunter Attempts"):
    total_tried = discord.ui.TextInput(label="How many attempts?", placeholder="Example: 350", max_length=12)
    rate_limited = discord.ui.TextInput(label="Rate limited?", placeholder="yes / no", required=False, max_length=20)
    vanity = discord.ui.TextInput(label="Vanity pulled? Optional", placeholder="Leave blank if no pull. Example: make", required=False, max_length=80)
    attempted_date = discord.ui.TextInput(label="Date optional", placeholder="YYYY-MM-DD, leave blank for today", required=False, max_length=20)
    notes = discord.ui.TextInput(label="Notes optional", style=discord.TextStyle.paragraph, placeholder="List name, proof, what happened, etc.", required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_attempt_from_panel(interaction, str(self.total_tried), str(self.rate_limited), str(self.vanity), str(self.attempted_date), str(self.notes))


class HunterClaimModal(discord.ui.Modal, title="Log Vanity Claim"):
    vanity = discord.ui.TextInput(label="Vanity claimed", placeholder="Example: discord.gg/make or make", max_length=80)
    claimed_date = discord.ui.TextInput(label="Claim date", placeholder="YYYY-MM-DD", max_length=20)
    total_tried = discord.ui.TextInput(label="Attempts used for this pull", placeholder="Example: 275", max_length=12)
    notes = discord.ui.TextInput(label="Notes optional", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await create_claim_from_panel(interaction, str(self.vanity), str(self.claimed_date), str(self.total_tried), str(self.notes))


class HunterEditClaimModal(discord.ui.Modal, title="Edit Your Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", placeholder="Paste the Claim ID", max_length=40)
    vanity = discord.ui.TextInput(label="New vanity optional", placeholder="Leave blank to keep same", required=False, max_length=80)
    claimed_date = discord.ui.TextInput(label="New date optional", placeholder="YYYY-MM-DD or blank", required=False, max_length=20)
    total_tried = discord.ui.TextInput(label="New attempts optional", placeholder="Example: 300 or blank", required=False, max_length=12)
    notes = discord.ui.TextInput(label="New notes optional", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        claims = claims_for(interaction.guild.id)
        claim = find_claim(claims, claim_id=str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
        if int(claim.get("user_id", 0)) != interaction.user.id:
            return await interaction.response.send_message("You can only edit claims that you logged yourself.", ephemeral=True)
        changed = []
        if str(self.vanity).strip():
            code = clean_code(str(self.vanity))
            if not code:
                return await interaction.response.send_message("Use a valid vanity code or invite link.", ephemeral=True)
            claim["code"] = code
            changed.append("vanity")
        if str(self.claimed_date).strip():
            claimed_ts, err = parse_date(str(self.claimed_date).strip())
            if err:
                return await interaction.response.send_message(err, ephemeral=True)
            claim["claimed_ts"] = claimed_ts
            changed.append("date")
        if str(self.total_tried).strip():
            try:
                total = int(str(self.total_tried).replace(",", "").strip())
            except Exception:
                return await interaction.response.send_message("Attempts must be a number.", ephemeral=True)
            claim["total_tried"] = total
            changed.append("attempts")
        if str(self.notes).strip():
            claim["notes"] = str(self.notes).strip()[:1000]
            changed.append("notes")
        if not changed:
            return await interaction.response.send_message("Nothing was changed.", ephemeral=True)
        claim["edited_ts"] = now_ts()
        claim["edited_by"] = interaction.user.id
        save_claims(interaction.guild.id, claims)
        log_channel = await get_claim_log_channel(interaction.guild, interaction.channel)
        edit_embed = claim_embed(interaction.guild, claim)
        edit_embed.title = "✏️ Vanity Claim Edited"
        edit_embed.add_field(name="Edited Fields", value=", ".join(f"`{x}`" for x in changed), inline=False)
        await send_claim_embed_replacing_previous(interaction.guild, claim, edit_embed, fallback_channel=log_channel, ping_role=False)
        save_claims(interaction.guild.id, claims)
        await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message(f"Updated claim `{claim.get('id')}`. Edited: {', '.join(changed)}.", ephemeral=True)


class HunterRemoveClaimModal(discord.ui.Modal, title="Remove Your Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", placeholder="Paste the Claim ID", max_length=40)
    reason = discord.ui.TextInput(label="Reason optional", style=discord.TextStyle.paragraph, required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        claims = claims_for(interaction.guild.id)
        claim = find_claim(claims, claim_id=str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
        if int(claim.get("user_id", 0)) != interaction.user.id:
            return await interaction.response.send_message("You can only remove claims that you logged yourself.", ephemeral=True)
        claims.remove(claim)
        save_claims(interaction.guild.id, claims)
        await delete_previous_claim_embed(interaction.guild, claim)
        log_channel = await get_claim_log_channel(interaction.guild, interaction.channel)
        removed_embed = embed("🗑️ Vanity Claim Removed", color=RED)
        removed_embed.description = (
            f"**Vanity:** `discord.gg/{claim.get('code')}`\n"
            f"**Hunter:** {interaction.user.mention}\n"
            f"**Claim ID:** `{claim.get('id')}`\n"
            f"**Original Attempts:** `{int(claim.get('total_tried', 0)):,}`\n"
            f"**Removed:** <t:{now_ts()}:R>"
        )
        removed_embed.add_field(name="Reason", value=(str(self.reason) or "No reason provided.")[:1024], inline=False)
        if log_channel:
            await log_channel.send(embed=removed_embed)
        await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message(f"Removed your claim `{claim.get('id')}`.", ephemeral=True)


class ManagerValueModal(discord.ui.Modal, title="Value A Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", placeholder="Paste the Claim ID", max_length=40)
    value = discord.ui.TextInput(label="Claim value", placeholder="Example: 50 or $50", max_length=20)
    cut_percent = discord.ui.TextInput(label="Hunter cut percent", placeholder="Example: 40", max_length=10)
    notes = discord.ui.TextInput(label="Manager notes optional", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        claims = claims_for(interaction.guild.id)
        claim = find_claim(claims, claim_id=str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
        try:
            cut = float(str(self.cut_percent).replace("%", "").strip())
        except Exception:
            return await interaction.response.send_message("Cut percent must be a number, like `40`.", ephemeral=True)
        if cut < 0 or cut > 100:
            return await interaction.response.send_message("Cut percent must be from `0` to `100`.", ephemeral=True)
        await apply_claim_value(interaction, claim, claims, str(self.value), cut, str(self.notes) or None)


class ManagerSetupModal(discord.ui.Modal, title="Setup Vanity System"):
    hunter_log_channel_id = discord.ui.TextInput(label="Hunter log channel ID", placeholder="Paste channel ID", required=False, max_length=25)
    hunter_ping_role_id = discord.ui.TextInput(label="Hunter ping role ID optional", placeholder="Paste role ID or leave blank", required=False, max_length=25)
    value_log_channel_id = discord.ui.TextInput(label="Value log channel ID optional", placeholder="Paste channel ID or leave blank", required=False, max_length=25)
    leaderboard_channel_id = discord.ui.TextInput(label="Leaderboard channel ID optional", placeholder="Paste channel ID or leave blank", required=False, max_length=25)
    refresh_minutes = discord.ui.TextInput(label="Leaderboard refresh minutes optional", placeholder="Example: 10", required=False, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        cfg = config(interaction.guild.id)
        changed = []
        hunter_log = re.search(r"\d{15,25}", str(self.hunter_log_channel_id))
        if hunter_log:
            cfg["hunter_log_channel_id"] = int(hunter_log.group(0)); changed.append("hunter log")
        ping_role = re.search(r"\d{15,25}", str(self.hunter_ping_role_id))
        if ping_role:
            cfg["hunter_ping_role_id"] = int(ping_role.group(0)); changed.append("claim ping role")
        elif str(self.hunter_ping_role_id).strip().lower() in {"none", "off", "disable", "disabled"}:
            cfg["hunter_ping_role_id"] = None; changed.append("claim ping role disabled")
        value_log = re.search(r"\d{15,25}", str(self.value_log_channel_id))
        if value_log:
            cfg["value_log_channel_id"] = int(value_log.group(0)); changed.append("value log")
        leaderboard = re.search(r"\d{15,25}", str(self.leaderboard_channel_id))
        if leaderboard:
            cfg["leaderboard_channel_id"] = int(leaderboard.group(0)); changed.append("leaderboard")
        if str(self.refresh_minutes).strip():
            try:
                cfg["leaderboard_refresh_minutes"] = max(1, min(1440, int(str(self.refresh_minutes).strip())))
                changed.append("leaderboard refresh")
            except Exception:
                return await interaction.response.send_message("Refresh minutes must be a number.", ephemeral=True)
        if not changed:
            return await interaction.response.send_message("Nothing changed. Paste at least one channel/role ID.", ephemeral=True)
        cfg["leaderboard_last_update"] = 0
        save_config(interaction.guild.id, cfg)
        if cfg.get("leaderboard_channel_id"):
            await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message("Updated setup: " + ", ".join(changed) + ".", ephemeral=True)


class HunterPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Log Attempt", style=discord.ButtonStyle.primary, emoji="📝")
    async def log_attempt(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterAttemptModal())

    @discord.ui.button(label="Log Claim", style=discord.ButtonStyle.success, emoji="🏷️")
    async def log_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterClaimModal())

    @discord.ui.button(label="My Stats", style=discord.ButtonStyle.secondary, emoji="📊")
    async def my_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        target = interaction.user
        stats = calculate_hunter_stats(interaction.guild.id).get(str(target.id), {"claims": 0, "total_attempts": 0, "total_value": 0, "total_cut": 0, "codes": []})
        best = stats.get("most_valuable_claim")
        e = embed(f"🏹 Hunter Stats — {target.display_name}", color=PURPLE)
        claimed_codes = stats.get("codes", [])
        claimed_lines = [f"`discord.gg/{c}`" for c in claimed_codes[-15:]]
        if len(claimed_codes) > 15:
            claimed_lines.append(f"...and `{len(claimed_codes) - 15}` more")
        e.description = (
            f"**Claims Pulled:** `{int(stats.get('claims', 0)):,}`\n"
            f"**Total Attempts:** `{int(stats.get('total_attempts', 0)):,}`\n"
            f"**No-Pull Attempt Logs:** `{int(stats.get('attempt_sessions', 0)):,}`\n"
            f"**Rate Limits Logged:** `{int(stats.get('rate_limits', 0)):,}`\n"
            f"**Total Value:** `{money(float(stats.get('total_value', 0)))}`\n"
            f"**Calculated Cut:** `{money(float(stats.get('total_cut', 0)))}`\n"
            f"**Elite Progress:** `{min(int(stats.get('claims', 0)), 10)}/10 claims`"
        )
        if best:
            e.add_field(name="Most Valuable Claim", value=f"`discord.gg/{best['code']}` — `{money(float(best['value']))}`", inline=False)
        e.add_field(name="Claimed Vanities", value="\n".join(claimed_lines) or "None yet.", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, emoji="🏆")
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        await interaction.response.send_message(embed=leaderboard_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Edit Claim", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def edit_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterEditClaimModal())

    @discord.ui.button(label="Remove Claim", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterRemoveClaimModal())


class ManagerPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="Value Claim", style=discord.ButtonStyle.success, emoji="💸")
    async def value_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(ManagerValueModal())

    @discord.ui.button(label="Recent Claims", style=discord.ButtonStyle.primary, emoji="📜")
    async def recent_claims(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        claims = list(reversed(claims_for(interaction.guild.id)))[:15]
        if not claims:
            return await interaction.response.send_message("No claims found.", ephemeral=True)
        lines = []
        for c in claims:
            value = money(float(c.get("value") or 0)) if float(c.get("value") or 0) > 0 else "No value"
            lines.append(f"`{c.get('id')}` — `discord.gg/{c.get('code')}` • <@{c.get('user_id')}> • `{int(c.get('total_tried', 0)):,}` attempts • {value}")
        await interaction.response.send_message(embed=embed("Recent Hunter Claims", "\n".join(lines), GOLD), ephemeral=True)

    @discord.ui.button(label="Value History", style=discord.ButtonStyle.secondary, emoji="🧾")
    async def value_history_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        logs = list(reversed(value_logs_for(interaction.guild.id)))[:15]
        if not logs:
            return await interaction.response.send_message("No value updates found.", ephemeral=True)
        lines = []
        for log in logs:
            lines.append(f"`{log.get('claim_id')}` — `discord.gg/{log.get('code')}` • <@{log.get('user_id')}> • `{money(float(log.get('value', 0)))}` • cut `{float(log.get('cut_percent', 0)):g}%` • <t:{int(log.get('logged_ts', now_ts()))}:R>")
        await interaction.response.send_message(embed=embed("Recent Value Updates", "\n".join(lines), GREEN), ephemeral=True)

    @discord.ui.button(label="Setup Logs", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def setup_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_admin(interaction):
            return
        await interaction.response.send_modal(ManagerSetupModal())

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, emoji="🏆")
    async def manager_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_message(embed=leaderboard_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Manager Guide", style=discord.ButtonStyle.secondary, emoji="❔")
    async def manager_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        e = embed("Manager Workflow", color=PURPLE)
        e.description = (
            "**1. Check Recent Claims** to get the Claim ID.\n"
            "**2. Value Claim** with sale/value and hunter cut percent.\n"
            "**3. The bot replaces the old claim embed for that same Claim ID.**\n"
            "**4. Value History** keeps a manager audit log.\n\n"
            "For setup, use **Setup Logs** and paste channel/role IDs."
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="hunter_panel", description="Open the clean hunter panel with buttons for attempts, claims, stats, and edits.")
async def hunter_panel(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    await interaction.response.send_message(embed=panel_hunter_embed(interaction.guild), view=HunterPanelView(), ephemeral=True)


@bot.tree.command(name="manager_panel", description="Open the clean manager panel for values, recent claims, logs, setup, and leaderboard.")
async def manager_panel(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.response.send_message(embed=panel_manager_embed(interaction.guild), view=ManagerPanelView(), ephemeral=True)


@bot.tree.command(name="post_hunter_panel", description="Managers: post a public hunter panel in a channel.")
async def post_hunter_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not await require_manager(interaction):
        return
    target = channel or interaction.channel
    await target.send(embed=panel_hunter_embed(interaction.guild), view=HunterPanelView())
    await interaction.response.send_message(f"Posted the hunter panel in {target.mention}.", ephemeral=True)


@bot.tree.command(name="post_manager_panel", description="Admins: post a manager panel in a channel.")
async def post_manager_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not await require_admin(interaction):
        return
    target = channel or interaction.channel
    await target.send(embed=panel_manager_embed(interaction.guild), view=ManagerPanelView())
    await interaction.response.send_message(f"Posted the manager panel in {target.mention}.", ephemeral=True)


async def maybe_give_elite_hunter(member: discord.Member, claims_count: int) -> Optional[discord.Role]:
    if claims_count < 10:
        return None
    role = discord.utils.get(member.guild.roles, name="elite hunter") or discord.utils.get(member.guild.roles, name="Elite Hunter")
    if not role:
        return None
    if role not in member.roles:
        try:
            await member.add_roles(role, reason="Reached 10+ logged vanity claims")
        except Exception:
            return None
    return role


async def apply_claim_value(interaction: discord.Interaction, claim: dict, claims: list, value_raw: str, cut_percent: float, notes: Optional[str]) -> None:
    amount, err = parse_money(value_raw)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    hunter_cut = round(float(amount) * (cut_percent / 100.0), 2)
    owner_cut = round(float(amount) - hunter_cut, 2)
    claim["value"] = amount
    claim["cut_percent"] = cut_percent
    claim["hunter_cut"] = hunter_cut
    claim["owner_cut"] = owner_cut
    claim["value_updated_by"] = interaction.user.id
    claim["value_updated_ts"] = now_ts()
    if notes:
        claim["value_notes"] = notes.strip()[:1000]
    save_claims(interaction.guild.id, claims)

    log = {
        "id": make_id(),
        "claim_id": claim.get("id"),
        "guild_id": interaction.guild.id,
        "user_id": claim.get("user_id"),
        "code": claim.get("code"),
        "value": amount,
        "cut_percent": cut_percent,
        "hunter_cut": hunter_cut,
        "owner_cut": owner_cut,
        "updated_by": interaction.user.id,
        "notes": notes,
        "logged_ts": now_ts(),
    }
    logs = value_logs_for(interaction.guild.id)
    logs.append(log)
    save_value_logs(interaction.guild.id, logs[-5000:])

    cfg = config(interaction.guild.id)
    claim_log = await get_claim_log_channel(interaction.guild, interaction.channel)
    updated_claim_embed = claim_embed(interaction.guild, claim)
    updated_claim_embed.title = "💸 Vanity Claim Updated"
    updated_claim_embed.add_field(
        name="Manager Update",
        value=f"Updated by <@{interaction.user.id}> • <t:{now_ts()}:R>",
        inline=False,
    )
    await send_claim_embed_replacing_previous(
        interaction.guild,
        claim,
        updated_claim_embed,
        fallback_channel=claim_log,
        ping_role=False,
    )
    save_claims(interaction.guild.id, claims)

    value_channel = interaction.guild.get_channel(int(cfg.get("value_log_channel_id") or 0)) or interaction.channel
    await value_channel.send(embed=value_update_embed(interaction.guild, claim, interaction.user.id, notes))
    await refresh_leaderboard_now(interaction.guild)
    await interaction.response.send_message(
        f"Updated `discord.gg/{claim.get('code')}` for <@{claim.get('user_id')}>. Value `{money(amount)}` • cut `{cut_percent:g}%` = `{money(hunter_cut)}`.",
        ephemeral=True,
    )


@bot.tree.command(name="info_roles", description="Public info: view Manager and Elite Hunter role guides.")
@app_commands.describe(role="Choose which role guide to show, or show both.")
@app_commands.choices(role=[
    app_commands.Choice(name="All Roles", value="all"),
    app_commands.Choice(name="Manager", value="manager"),
    app_commands.Choice(name="Elite Hunter", value="elite_hunter"),
])
async def info_roles(interaction: discord.Interaction, role: Optional[app_commands.Choice[str]] = None):
    choice = role.value if role else "all"

    title = "📋 Vanity Role Information"
    if choice == "manager":
        title = "📌 Vanity Manager Guide"
    elif choice == "elite_hunter":
        title = "🏹 Elite Hunter Role Guide"

    e = embed(title, color=GOLD if choice == "elite_hunter" else PURPLE)

    if choice in ("all", "manager"):
        e.add_field(
            name="📌 Manager Role",
            value=(
                "Managers keep the vanity hunting job organized, fair, and easy to follow.\n\n"
                "**What managers do:**\n"
                "• Run vanity checks and schedule list watches.\n"
                "• Post updated lists for hunters to attempt.\n"
                "• Verify claimed vanities when needed.\n"
                "• Add prices, values, cut percentages, and notes to existing hunter claims.\n"
                "• Watch for fake logs, duplicated claims, or suspicious attempt counts.\n"
                "• Help new hunters understand how to claim and log correctly.\n\n"
                "**Manager workflow:**\n"
                "1. Hunter logs a claim from the **Hunter Panel**.\n"
                "2. Manager reviews the claim if needed.\n"
                "3. Manager adds value info from the **Manager Panel** using the Claim ID.\n"
                "4. Bot updates stats, best claim, and leaderboard automatically.\n\n"
                "**Good manager habits:**\n"
                "• Keep values consistent.\n"
                "• Leave clear notes when editing claims.\n"
                "• Do not favorite certain hunters.\n"
                "• Make update instructions simple so hunters know what to attempt."
            ),
            inline=False,
        )

    if choice in ("all", "elite_hunter"):
        e.add_field(
            name="🏹 Elite Hunter Role",
            value=(
                "Elite Hunter is a reward role for consistent, trusted vanity hunters.\n\n"
                "**How to earn it:**\n"
                "• Log **10+ real vanity claims** using the **Hunter Panel**.\n"
                "• Claims must be honest and not duplicated.\n"
                "• Attempt counts should be realistic.\n\n"
                "**What the bot tracks:**\n"
                "• Total claimed vanities.\n"
                "• Total attempts submitted.\n"
                "• Most valuable claim after a manager adds value.\n"
                "• Best claim of the week.\n"
                "• Leaderboard placement.\n\n"
                "**How to stand out:**\n"
                "1. Attempt updated lists quickly.\n"
                "2. Log claims right after you get them.\n"
                "3. Use useful notes, not random filler.\n"
                "4. Stay consistent instead of only trying once."
            ),
            inline=False,
        )

    e.set_footer(text="Use /info_roles role:manager, /info_roles role:elite_hunter, or /info_roles role:all")
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="info_vanity_job", description="Public info: full guide for the vanity hunting job.")
async def info_vanity_job(interaction: discord.Interaction):
    e = embed("🔎 Vanity Hunting Job — Full Guide", color=GREEN)
    e.description = (
        "This job is about attempting available Discord vanity/invite codes from updated lists and logging the ones you successfully claim.\n\n"
        "**Goal of the job:**\n"
        "Find and claim useful, rare, clean, or valuable vanities before someone else gets them. Managers can later add value/cut info to your claim if it becomes sellable or worth tracking.\n\n"
        "**Step-by-step workflow:**\n"
        "**1. Watch for list updates.**\n"
        "When the bot posts an update like **lists updated, make sure to go attempt**, start checking the newest list.\n\n"
        "**2. Attempt the list carefully.**\n"
        "Work through the list without skipping randomly. Keep track of roughly how many vanities you attempted.\n\n"
        "**3. Claim the vanity.**\n"
        "If you successfully claim one, make sure you know the exact vanity/code before logging it.\n\n"
        "**4. Log your claim from the Hunter Panel.**\n"
        "Only enter the required information: vanity, date, total attempts, and notes.\n\n"
        "**5. Wait for manager value updates.**\n"
        "Managers can later add a price/value, cut percentage, payout notes, or other details to your already logged claim.\n\n"
        "**How to log after claiming:**\n"
        "Open the **Hunter Panel**, press **Log Claim**, and fill it like this:\n"
        "`vanity:` the code you claimed, without needing the full link.\n"
        "`date:` the date you claimed it. Example: `2026-05-01`.\n"
        "`attempts:` how many total vanities you tried before or during that claim session.\n"
        "`notes:` short proof/context, such as list name, time, or anything managers should know.\n\n"
        "**Good notes examples:**\n"
        "• `claimed from morning update list`\n"
        "• `claimed after trying 300 from 4-letter list`\n"
        "• `manager told me to attempt shop list`\n\n"
        "**Do not do this:**\n"
        "• Do not fake claims.\n"
        "• Do not log someone else's vanity.\n"
        "• Do not exaggerate attempts.\n"
        "• Do not spam the same claim multiple times.\n\n"
        "**⚠️ Logging rule:**\n"
        "**Not logging correct attempts or claims will get you suspended from the job.**\n"
        "This keeps payouts, leaderboards, and manager reviews fair for everyone.\n\n"
        "**Editing/removing logs:**\n"
        "If you make a mistake, open the **Hunter Panel** and press **Edit Claim** or **Remove Claim**. You can only edit or remove claims you personally logged.\n\n"
        "**Leaderboard info:**\n"
        "The leaderboard shows top hunters by claims, attempts, and best claim value. It also highlights the best claim of the week so active hunters can stand out.\n\n"
        "**Tip:**\n"
        "Speed matters, but clean logging matters too. The better your logs are, the easier it is for managers to value your claims and track your progress."
    )
    e.add_field(
        name="Example claim log",
        value="Hunter Panel → Log Claim → vanity: `rare` • date: `2026-05-01` • attempts: `275` • notes: `claimed from updated short list`",
        inline=False,
    )
    e.add_field(
        name="Fixing your own logs",
        value="Made a typo? Use **Edit Claim** in the Hunter Panel. Logged the wrong claim by accident? Use **Remove Claim**. You cannot edit or remove another member's claim.",
        inline=False,
    )
    await interaction.response.send_message(embed=e)
# RUN
# =========================================================
if not TOKEN:
    raise RuntimeError("Missing TOKEN environment variable. Add TOKEN to your .env or host variables.")
bot.run(TOKEN)
