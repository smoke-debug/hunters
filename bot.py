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


def calculate_hunter_stats(guild_id: int) -> Dict[str, dict]:
    stats: Dict[str, dict] = {}
    for claim in claims_for(guild_id):
        uid = str(claim.get("user_id"))
        st = stats.setdefault(uid, {
            "claims": 0,
            "total_attempts": 0,
            "total_value": 0.0,
            "total_cut": 0.0,
            "most_valuable_claim": None,
            "last_claim_ts": None,
            "codes": [],
        })
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
        lines.append(
            f"{medal} <@{uid}> — **{int(st.get('claims', 0))}** claims • "
            f"**{int(st.get('total_attempts', 0)):,}** attempts • best {best_text}"
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
    total_attempts = sum(int(c.get("total_tried", 0)) for c in claims_for(guild.id))
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
@bot.tree.command(name="help", description="Full beginner guide for using the vanity hunter bot.")
async def help_command(interaction: discord.Interaction):
    e = embed("📘 Vanity Hunting Guide", color=PURPLE)
    e.description = (
        "**Welcome to the Vanity Hunting System**\n\n"
        "Earn rewards by finding and claiming rare Discord invite links like `discord.gg/example`. "
        "This bot tracks your claims, attempts, values, cuts, and leaderboard progress.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "**⚠️ IMPORTANT RULE**\n"
        "**NOT logging correct attempts or claims WILL get you suspended from the job.**\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    e.add_field(
        name="🧠 How The Job Works",
        value=(
            "```\n"
            "1. Wait for list update alerts or get a list from managers.\n"
            "2. Attempt the vanity links as fast and accurately as possible.\n"
            "3. If you successfully claim one, save the vanity + attempts.\n"
            "4. Log the claim with /hunter_claim.\n"
            "5. Managers review the claim and add value/cut info.\n"
            "6. Your stats and leaderboard position update automatically.\n"
            "```"
        ),
        inline=False,
    )

    e.add_field(
        name="📥 Claim Commands",
        value=(
            "• `/hunter_claim` → Log a vanity you claimed\n"
            "• `/hunter_claim_edit` → Edit **your own** claim only\n"
            "• `/hunter_claim_remove` → Remove **your own** claim only\n"
            "• `/hunter_stats` → View your claims, attempts, best claim, and value stats\n\n"
            "When logging, include:\n"
            "• Vanity code/link\n"
            "• Date claimed\n"
            "• Total vanities attempted\n"
            "• Notes if needed"
        ),
        inline=False,
    )

    e.add_field(
        name="💰 Value & Cut System",
        value=(
            "• `/claim_value_set` → Managers add value/cut to an existing claim by user + vanity\n"
            "• `/claim_value_by_id` → Managers add value/cut by Claim ID\n"
            "• `/value_history` → Managers review recent value updates\n\n"
            "Values can be added after a member logs a claim, so hunters should log first and managers can price it later."
        ),
        inline=False,
    )

    e.add_field(
        name="🏆 Leaderboards & Progress",
        value=(
            "• `/hunter_leaderboard` → View top hunters\n"
            "• Auto leaderboard shows top 10 hunters\n"
            "• Tracks total claims, attempts, best claim, total value, and weekly best claim\n"
            "• Reaching **10+ claims** can earn the `elite hunter` role"
        ),
        inline=False,
    )

    e.add_field(
        name="📊 Info Commands",
        value=(
            "• `/info_vanity_job` → Full job guide with rules and logging steps\n"
            "• `/info_roles role:all` → Manager + Elite Hunter info\n"
            "• `/info_roles role:manager` → Manager role info only\n"
            "• `/info_roles role:elite_hunter` → Elite Hunter info only\n"
            "• `/vanity_help` → Full command menu"
        ),
        inline=False,
    )

    e.set_footer(text="Accuracy = trust • Consistency = rewards • Incorrect logs can get you suspended")
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="vanity_help", description="Show the full vanity bot command menu.")
async def vanity_help(interaction: discord.Interaction):
    e = embed("📚 Vanity Bot Command Menu", color=PURPLE)
    e.description = (
        "A clean command list for hunters, managers, and setup staff.\n\n"
        "**⚠️ Logging Rule:** **NOT logging correct attempts or claims WILL get you suspended from the job.**"
    )
    e.add_field(
        name="Member / Hunter Commands",
        value=(
            "`/hunter_claim` — log a claimed vanity\n"
            "`/hunter_claim_edit` — edit your own claim\n"
            "`/hunter_claim_remove` — remove your own claim\n"
            "`/hunter_stats` — view your stats\n"
            "`/hunter_leaderboard` — view the top hunters"
        ),
        inline=False,
    )
    e.add_field(
        name="Public Info Commands",
        value=(
            "`/help` — beginner guide\n"
            "`/vanity_help` — full command menu\n"
            "`/info_vanity_job` — full job guide\n"
            "`/info_roles` — manager and Elite Hunter role information"
        ),
        inline=False,
    )
    e.add_field(
        name="Manager Claim / Value Commands",
        value=(
            "`/claim_value_set` — value an existing claim by hunter + vanity\n"
            "`/claim_value_by_id` — value an existing claim by Claim ID\n"
            "`/hunter_history` — review claim logs\n"
            "`/value_history` — review value updates"
        ),
        inline=False,
    )
    e.add_field(
        name="Vanity Checking Commands",
        value=(
            "`/vanity_check` — check pasted vanities\n"
            "`/vanity_add_list` — save a named vanity list\n"
            "`/vanity_run_list` — run a saved list\n"
            "`/vanity_stop` — stop the current run\n"
            "`/vanity_lists` — view saved lists"
        ),
        inline=False,
    )
    e.add_field(
        name="Auto Systems / Setup",
        value=(
            "`/vanity_setup` — set result channels and delay\n"
            "`/claim_channel_setup` — set hunter claim log channel\n"
            "`/value_setup` — set value log channel\n"
            "`/leaderboard_setup` — create auto-updating leaderboard\n"
            "`/list_update_setup` — send cross-server update alerts\n"
            "`/vanity_watch_start` — scheduled checks\n"
            "`/vanity_watch_stop` — stop scheduled checks\n"
            "`/vanity_watches` — view active watches"
        ),
        inline=False,
    )
    e.add_field(
        name="Access Commands",
        value=(
            "`/vanity_access_add_user` — permit a user\n"
            "`/vanity_access_add_role` — permit a role"
        ),
        inline=False,
    )
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
# CLAIM COMMANDS
# =========================================================
@bot.tree.command(name="hunter_setup", description="Set the channel where member claim logs are posted.")
async def hunter_setup(interaction: discord.Interaction, log_channel: discord.TextChannel):
    if not await require_admin(interaction):
        return
    cfg = config(interaction.guild.id)
    cfg["hunter_log_channel_id"] = log_channel.id
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Hunter claims will post in {log_channel.mention}.", ephemeral=True)


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


@bot.tree.command(name="hunter_claim", description="Member: log a claimed vanity and update your stats.")
@app_commands.describe(vanity="Vanity code or invite link", claimed_date="YYYY-MM-DD", total_tried="How many vanities you tried", notes="Optional notes/proof/context")
async def hunter_claim(interaction: discord.Interaction, vanity: str, claimed_date: str, total_tried: app_commands.Range[int, 0, 1000000], notes: Optional[str] = None):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    code = clean_code(vanity)
    if not code:
        return await interaction.response.send_message("Use a valid vanity code or invite link.", ephemeral=True)
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

    cfg = config(interaction.guild.id)
    log_channel = interaction.guild.get_channel(int(cfg.get("hunter_log_channel_id") or 0)) or interaction.channel
    await log_channel.send(embed=claim_embed(interaction.guild, claim))

    stats = calculate_hunter_stats(interaction.guild.id).get(str(interaction.user.id), {})
    elite = await maybe_give_elite_hunter(interaction.user, int(stats.get("claims", 0)))
    await refresh_leaderboard_now(interaction.guild)

    msg = f"Logged `discord.gg/{code}`. Claim ID: `{claim['id']}` • Total claims: `{int(stats.get('claims', 0))}`."
    if elite:
        msg += f" You earned {elite.mention}."
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="hunter_claim_edit", description="Member: edit one of your own logged claims by Claim ID.")
@app_commands.describe(
    claim_id="Claim ID from your claim log",
    vanity="New vanity code/link, optional",
    claimed_date="New claim date as YYYY-MM-DD, optional",
    total_tried="New total vanities tried, optional",
    notes="New notes, optional"
)
async def hunter_claim_edit(
    interaction: discord.Interaction,
    claim_id: str,
    vanity: Optional[str] = None,
    claimed_date: Optional[str] = None,
    total_tried: Optional[app_commands.Range[int, 0, 1000000]] = None,
    notes: Optional[str] = None,
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)

    claims = claims_for(interaction.guild.id)
    claim = find_claim(claims, claim_id=claim_id)
    if not claim:
        return await interaction.response.send_message("Claim ID not found. Ask a manager to check `/hunter_history` if you lost it.", ephemeral=True)
    if int(claim.get("user_id", 0)) != interaction.user.id:
        return await interaction.response.send_message("You can only edit claims that you logged yourself.", ephemeral=True)

    changed = []
    if vanity is not None:
        code = clean_code(vanity)
        if not code:
            return await interaction.response.send_message("Use a valid vanity code or invite link.", ephemeral=True)
        claim["code"] = code
        changed.append("vanity")

    if claimed_date is not None:
        claimed_ts, err = parse_date(claimed_date)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        claim["claimed_ts"] = claimed_ts
        changed.append("date")

    if total_tried is not None:
        claim["total_tried"] = int(total_tried)
        changed.append("attempts")

    if notes is not None:
        claim["notes"] = notes.strip()[:1000] or None
        changed.append("notes")

    if not changed:
        return await interaction.response.send_message("Nothing was changed. Fill in at least one optional field to edit your claim.", ephemeral=True)

    claim["edited_ts"] = now_ts()
    claim["edited_by"] = interaction.user.id
    save_claims(interaction.guild.id, claims)

    cfg = config(interaction.guild.id)
    log_channel = interaction.guild.get_channel(int(cfg.get("hunter_log_channel_id") or 0)) or interaction.channel
    edit_embed = claim_embed(interaction.guild, claim)
    edit_embed.title = "✏️ Vanity Claim Edited"
    edit_embed.add_field(name="Edited Fields", value=", ".join(f"`{x}`" for x in changed), inline=False)
    await log_channel.send(embed=edit_embed)

    await refresh_leaderboard_now(interaction.guild)
    await interaction.response.send_message(f"Updated your claim `{claim_id}`. Edited: {', '.join(changed)}.", ephemeral=True)


@bot.tree.command(name="hunter_claim_remove", description="Member: remove one of your own logged claims by Claim ID.")
@app_commands.describe(claim_id="Claim ID from your claim log", reason="Optional reason for removing it")
async def hunter_claim_remove(interaction: discord.Interaction, claim_id: str, reason: Optional[str] = None):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)

    claims = claims_for(interaction.guild.id)
    claim = find_claim(claims, claim_id=claim_id)
    if not claim:
        return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
    if int(claim.get("user_id", 0)) != interaction.user.id:
        return await interaction.response.send_message("You can only remove claims that you logged yourself.", ephemeral=True)

    claims.remove(claim)
    save_claims(interaction.guild.id, claims)

    cfg = config(interaction.guild.id)
    log_channel = interaction.guild.get_channel(int(cfg.get("hunter_log_channel_id") or 0)) or interaction.channel
    removed_embed = embed("🗑️ Vanity Claim Removed", color=RED)
    removed_embed.description = (
        f"**Vanity:** `discord.gg/{claim.get('code')}`\n"
        f"**Hunter:** {interaction.user.mention}\n"
        f"**Claim ID:** `{claim.get('id')}`\n"
        f"**Original Attempts:** `{int(claim.get('total_tried', 0)):,}`\n"
        f"**Removed:** <t:{now_ts()}:R>"
    )
    removed_embed.add_field(name="Reason", value=(reason or "No reason provided.")[:1024], inline=False)
    await log_channel.send(embed=removed_embed)

    await refresh_leaderboard_now(interaction.guild)
    await interaction.response.send_message(f"Removed your claim `{claim_id}`.", ephemeral=True)


@bot.tree.command(name="claim_value_set", description="Managers: add/update value for a member's already logged claim by hunter + vanity.")
@app_commands.describe(hunter="Hunter who logged it", vanity="Vanity code/link they logged", value="Value like $50", cut_percent="Hunter cut percent, example 40", notes="Optional manager notes")
async def claim_value_set(interaction: discord.Interaction, hunter: discord.Member, vanity: str, value: str, cut_percent: app_commands.Range[float, 0.0, 100.0], notes: Optional[str] = None):
    if not await require_manager(interaction):
        return
    claims = claims_for(interaction.guild.id)
    claim = find_claim(claims, hunter_id=hunter.id, code=vanity)
    if not claim:
        return await interaction.response.send_message("I could not find a logged claim for that hunter and vanity. Use `/hunter_history` or `/claim_value_by_id` if needed.", ephemeral=True)
    await apply_claim_value(interaction, claim, claims, value, float(cut_percent), notes)


@bot.tree.command(name="claim_value_by_id", description="Managers: add/update value for a logged claim by Claim ID.")
async def claim_value_by_id(interaction: discord.Interaction, claim_id: str, value: str, cut_percent: app_commands.Range[float, 0.0, 100.0], notes: Optional[str] = None):
    if not await require_manager(interaction):
        return
    claims = claims_for(interaction.guild.id)
    claim = find_claim(claims, claim_id=claim_id)
    if not claim:
        return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
    await apply_claim_value(interaction, claim, claims, value, float(cut_percent), notes)


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
    value_channel = interaction.guild.get_channel(int(cfg.get("value_log_channel_id") or 0)) or interaction.channel
    await value_channel.send(embed=value_update_embed(interaction.guild, claim, interaction.user.id, notes))
    await refresh_leaderboard_now(interaction.guild)
    await interaction.response.send_message(
        f"Updated `discord.gg/{claim.get('code')}` for <@{claim.get('user_id')}>. Value `{money(amount)}` • cut `{cut_percent:g}%` = `{money(hunter_cut)}`.",
        ephemeral=True,
    )


@bot.tree.command(name="value_setup", description="Set the channel where manager value updates are posted.")
async def value_setup(interaction: discord.Interaction, log_channel: discord.TextChannel):
    if not await require_admin(interaction):
        return
    cfg = config(interaction.guild.id)
    cfg["value_log_channel_id"] = log_channel.id
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Value updates will post in {log_channel.mention}.", ephemeral=True)


@bot.tree.command(name="hunter_history", description="Managers: show recent logged claims and claim IDs.")
async def hunter_history(interaction: discord.Interaction, hunter: Optional[discord.Member] = None, limit: app_commands.Range[int, 1, 20] = 10):
    if not await require_manager(interaction):
        return
    claims = list(reversed(claims_for(interaction.guild.id)))
    if hunter:
        claims = [c for c in claims if int(c.get("user_id", 0)) == hunter.id]
    claims = claims[:int(limit)]
    if not claims:
        return await interaction.response.send_message("No claims found.", ephemeral=True)
    lines = []
    for c in claims:
        value = money(float(c.get("value") or 0)) if float(c.get("value") or 0) > 0 else "No value"
        lines.append(f"`{c.get('id')}` — `discord.gg/{c.get('code')}` • <@{c.get('user_id')}> • `{int(c.get('total_tried', 0)):,}` attempts • {value}")
    await interaction.response.send_message(embed=embed("Recent Hunter Claims", "\n".join(lines), GOLD), ephemeral=True)


@bot.tree.command(name="hunter_stats", description="Show hunter stats for yourself or another member.")
async def hunter_stats(interaction: discord.Interaction, hunter: Optional[discord.Member] = None):
    if not interaction.guild:
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    target = hunter or interaction.user
    stats = calculate_hunter_stats(interaction.guild.id).get(str(target.id), {"claims": 0, "total_attempts": 0, "total_value": 0, "total_cut": 0, "codes": []})
    best = stats.get("most_valuable_claim")
    e = embed(f"🏹 Hunter Stats — {target.display_name}", color=PURPLE)
    e.description = (
        f"**Claims:** `{int(stats.get('claims', 0)):,}`\n"
        f"**Total Attempts:** `{int(stats.get('total_attempts', 0)):,}`\n"
        f"**Total Value:** `{money(float(stats.get('total_value', 0)))}`\n"
        f"**Calculated Cut:** `{money(float(stats.get('total_cut', 0)))}`\n"
        f"**Elite Progress:** `{min(int(stats.get('claims', 0)), 10)}/10 claims`"
    )
    if best:
        e.add_field(name="Most Valuable Claim", value=f"`discord.gg/{best['code']}` — `{money(float(best['value']))}`", inline=False)
    e.add_field(name="Recent Codes", value="\n".join(f"`discord.gg/{c}`" for c in stats.get("codes", [])[-10:]) or "None yet.", inline=False)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="hunter_leaderboard", description="Show the top 10 hunters by claims, best claim, and attempts.")
async def hunter_leaderboard(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    await interaction.response.send_message(embed=leaderboard_embed(interaction.guild))


@bot.tree.command(name="value_history", description="Managers: show recent manager value updates.")
async def value_history(interaction: discord.Interaction, hunter: Optional[discord.Member] = None, limit: app_commands.Range[int, 1, 20] = 10):
    if not await require_manager(interaction):
        return
    logs = list(reversed(value_logs_for(interaction.guild.id)))
    if hunter:
        logs = [x for x in logs if int(x.get("user_id", 0)) == hunter.id]
    logs = logs[:int(limit)]
    if not logs:
        return await interaction.response.send_message("No value updates found.", ephemeral=True)
    lines = []
    for log in logs:
        lines.append(f"`{log.get('claim_id')}` — `discord.gg/{log.get('code')}` • <@{log.get('user_id')}> • `{money(float(log.get('value', 0)))}` • cut `{money(float(log.get('hunter_cut', 0)))}`")
    await interaction.response.send_message(embed=embed("Recent Value Updates", "\n".join(lines), GREEN), ephemeral=True)

# =========================================================
# =========================================================
# INFO EMBEDS - PUBLIC
# =========================================================
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
                "1. Hunter logs a claim with `/hunter_claim`.\n"
                "2. Manager reviews the claim if needed.\n"
                "3. Manager adds value info with `/claim_value_set` or `/claim_value_by_id`.\n"
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
                "• Log **10+ real vanity claims** using `/hunter_claim`.\n"
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
        "**4. Log your claim with `/hunter_claim`.**\n"
        "Only enter the required information: vanity, date, total attempts, and notes.\n\n"
        "**5. Wait for manager value updates.**\n"
        "Managers can later add a price/value, cut percentage, payout notes, or other details to your already logged claim.\n\n"
        "**How to log after claiming:**\n"
        "Use `/hunter_claim` and fill it like this:\n"
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
        "If you make a mistake, use `/hunter_claim_edit` or `/hunter_claim_remove`. You can only edit or remove claims you personally logged.\n\n"
        "**Leaderboard info:**\n"
        "The leaderboard shows top hunters by claims, attempts, and best claim value. It also highlights the best claim of the week so active hunters can stand out.\n\n"
        "**Tip:**\n"
        "Speed matters, but clean logging matters too. The better your logs are, the easier it is for managers to value your claims and track your progress."
    )
    e.add_field(
        name="Example claim log",
        value="`/hunter_claim vanity:rare date:2026-05-01 attempts:275 notes:claimed from updated short list`",
        inline=False,
    )
    e.add_field(
        name="Fixing your own logs",
        value="Made a typo? Use `/hunter_claim_edit`. Logged the wrong claim by accident? Use `/hunter_claim_remove`. You cannot edit or remove another member's claim.",
        inline=False,
    )
    await interaction.response.send_message(embed=e)
# RUN
# =========================================================
if not TOKEN:
    raise RuntimeError("Missing TOKEN environment variable. Add TOKEN to your .env or host variables.")
bot.run(TOKEN)
