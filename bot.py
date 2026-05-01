from __future__ import annotations

import os
import re
import json
import time
import asyncio
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
# SETTINGS / STORAGE
# =========================================================
TOKEN = os.getenv("TOKEN")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CONFIG_FILE = DATA_DIR / "config.json"
CLAIMS_FILE = DATA_DIR / "claims.json"
VALUE_LOGS_FILE = DATA_DIR / "value_logs.json"
LISTS_FILE = DATA_DIR / "vanity_word_lists.json"
LIST_CHECKS_FILE = DATA_DIR / "vanity_list_checks.json"
INVALID_STATE_FILE = DATA_DIR / "vanity_invalid_state.json"

LEADERBOARD_REFRESH_MINUTES = int(os.getenv("LEADERBOARD_REFRESH_MINUTES", "10"))
DEFAULT_CLAIM_COOLDOWN_SECONDS = int(os.getenv("CLAIM_COOLDOWN_SECONDS", "60"))
DEFAULT_MAX_CLAIMS_PER_HOUR = int(os.getenv("MAX_CLAIMS_PER_HOUR", "5"))

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

claim_locks: Dict[Tuple[int, int], asyncio.Lock] = {}
list_check_lock = asyncio.Lock()

# =========================================================
# JSON HELPERS
# =========================================================
def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


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


def parse_money(raw: str) -> Tuple[Optional[float], Optional[str]]:
    text = str(raw or "").replace("$", "").replace(",", "").strip()
    try:
        value = round(float(text), 2)
    except Exception:
        return None, "Use a valid value like `25`, `25.50`, or `$25`."
    if value < 0:
        return None, "Value cannot be negative."
    return value, None


def parse_int(raw: str, field_name: str, min_value: int = 0, max_value: Optional[int] = None) -> Tuple[Optional[int], Optional[str]]:
    try:
        value = int(str(raw or "").replace(",", "").strip())
    except Exception:
        return None, f"{field_name} must be a number."
    if value < min_value:
        return None, f"{field_name} must be at least `{min_value}`."
    if max_value is not None and value > max_value:
        return None, f"{field_name} cannot be over `{max_value}`."
    return value, None

# =========================================================
# DATA MODELS
# =========================================================
def default_config() -> dict:
    return {
        "manager_users": [],
        "manager_roles": [],
        "claim_log_channel_id": None,
        "value_log_channel_id": None,
        "leaderboard_channel_id": None,
        "leaderboard_message_id": None,
        "leaderboard_refresh_minutes": LEADERBOARD_REFRESH_MINUTES,
        "leaderboard_last_update": 0,
        "claim_ping_role_id": None,
        "claim_cooldown_seconds": DEFAULT_CLAIM_COOLDOWN_SECONDS,
        "max_claims_per_hour": DEFAULT_MAX_CLAIMS_PER_HOUR,
        "autoroles": [],  # [{"role_id": int, "claims_required": int}]
        "list_checker_default_delay": 3.0,
    }


def config(guild_id: int) -> dict:
    data = load_json(CONFIG_FILE, {})
    cfg = data.setdefault(gkey(guild_id), default_config())
    # add new keys if old data exists
    for k, v in default_config().items():
        cfg.setdefault(k, v)
    return cfg


def save_config(guild_id: int, cfg: dict) -> None:
    data = load_json(CONFIG_FILE, {})
    data[gkey(guild_id)] = cfg
    save_json(CONFIG_FILE, data)


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
# VANITY WORD LIST CHECKER STORAGE
# =========================================================
def parse_codes(raw: str) -> List[str]:
    out, seen = [], set()
    for item in re.split(r"[,\n\s]+", raw or ""):
        code = clean_code(item)
        if code and code not in seen:
            out.append(code)
            seen.add(code)
    return out


def vanity_lists_for(guild_id: int) -> dict:
    data = load_json(LISTS_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_vanity_lists(guild_id: int, lists: dict) -> None:
    data = load_json(LISTS_FILE, {})
    data[gkey(guild_id)] = lists
    save_json(LISTS_FILE, data)


def list_checks_for(guild_id: int) -> dict:
    data = load_json(LIST_CHECKS_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_list_checks(guild_id: int, checks: dict) -> None:
    data = load_json(LIST_CHECKS_FILE, {})
    data[gkey(guild_id)] = checks
    save_json(LIST_CHECKS_FILE, data)


def invalid_state_for(guild_id: int) -> dict:
    data = load_json(INVALID_STATE_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_invalid_state(guild_id: int, state: dict) -> None:
    data = load_json(INVALID_STATE_FILE, {})
    data[gkey(guild_id)] = state
    save_json(INVALID_STATE_FILE, data)


def list_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", str(name or "").strip().lower().replace(" ", "-"))[:40]


def format_code_lines(codes: List[str], limit: int = 80) -> str:
    if not codes:
        return "No current invalid vanities for this length."
    shown = [f"`discord.gg/{c}`" for c in codes[:limit]]
    if len(codes) > limit:
        shown.append(f"...and `{len(codes) - limit}` more")
    return "\n".join(shown)[:3900]


def length_title(length: int) -> str:
    return f"{length} lettered vanities"


async def fetch_messageable(channel_id: int):
    channel = bot.get_channel(int(channel_id))
    if channel:
        return channel
    return await bot.fetch_channel(int(channel_id))


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


def invalid_group_embed(list_name: str, length: int, codes: List[str], last_run: Optional[int] = None, became_valid_count: int = 0) -> discord.Embed:
    unique_codes = sorted(set(clean_code(c) for c in codes if clean_code(c)))
    e = embed(length_title(length), color=RED if unique_codes else DARK)
    e.description = format_code_lines(unique_codes)
    e.add_field(name="List", value=f"`{list_name}`", inline=True)
    e.add_field(name="Current Invalids", value=f"`{len(unique_codes)}`", inline=True)
    e.add_field(name="Went Invalid -> Valid", value=f"`{became_valid_count}`", inline=True)
    if last_run:
        e.add_field(name="Last Updated", value=f"<t:{int(last_run)}:R>", inline=True)
    e.set_footer(text="Fresh list update - old list messages are deleted after every refresh")
    return e


def list_update_summary_embed(list_name: str, checked: int, invalid_count: int, became_valid: List[str], errors: List[str], last_run: Optional[int] = None) -> discord.Embed:
    e = embed("Vanity List Update", color=BLUE)
    e.description = (
        f"**List:** `{list_name}`\n"
        f"**Checked:** `{checked:,}`\n"
        f"**Current Invalids:** `{invalid_count:,}`\n"
        f"**Went Invalid -> Valid:** `{len(set(became_valid)):,}`\n"
        f"**Errors:** `{len(errors):,}`"
    )
    if became_valid:
        e.add_field(name="No Longer Invalid", value=format_code_lines(sorted(set(became_valid)), 40), inline=False)
    if errors:
        e.add_field(name="Errors", value="\n".join(errors[:10])[:1000], inline=False)
    if last_run:
        e.add_field(name="Updated", value=f"<t:{int(last_run)}:R>", inline=True)
    e.set_footer(text="Checked in the private checker server - posted to the hunter server")
    return e


def valid_results_embed(list_name: str, valid: List[str], became_valid: List[str], errors: List[str]) -> discord.Embed:
    e = embed("Valid / No Longer Invalid", color=GREEN)
    e.description = f"**List:** `{list_name}`\n**Currently Valid:** `{len(set(valid))}`\n**Went Invalid -> Valid:** `{len(set(became_valid))}`\n**Errors:** `{len(errors)}`"
    if valid:
        e.add_field(name="Currently Valid", value=format_code_lines(sorted(set(valid)), 40), inline=False)
    if became_valid:
        e.add_field(name="No Longer Invalid", value=format_code_lines(sorted(set(became_valid)), 40), inline=False)
    if errors:
        e.add_field(name="Errors", value="\n".join(errors[:10])[:1000], inline=False)
    return e


async def safe_delete_message(channel, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        msg = await channel.fetch_message(int(message_id))
        await msg.delete()
    except discord.NotFound:
        pass
    except Exception:
        pass


async def send_fresh_invalid_embeds(check_guild_id: int, list_name: str, check_cfg: dict, invalid_by_length: Dict[str, List[str]], became_valid: List[str], errors: List[str], ping_role_id: Optional[int] = None) -> None:
    invalid_channel_id = int(check_cfg.get("invalid_channel_id") or check_cfg.get("hunter_invalid_channel_id") or 0)
    if not invalid_channel_id:
        return
    channel = await fetch_messageable(invalid_channel_id)
    old_message_ids = list(check_cfg.get("invalid_message_ids", {}).values())
    old_summary_id = check_cfg.get("summary_message_id")
    last_run = int(check_cfg.get("last_run", now_ts()))

    clean_invalid = {
        str(length): sorted(set(clean_code(c) for c in codes if clean_code(c)))
        for length, codes in invalid_by_length.items()
        if str(length).isdigit() and codes
    }
    total_invalid = sum(len(v) for v in clean_invalid.values())
    checked = int(check_cfg.get("last_counts", {}).get("checked", 0) or 0)
    content = f"<@&{int(ping_role_id)}>" if ping_role_id else None
    allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)
    new_message_ids = {}

    try:
        summary_msg = await channel.send(content=content, embed=list_update_summary_embed(list_name, checked, total_invalid, became_valid, errors, last_run), allowed_mentions=allowed)
    except Exception:
        return

    became_valid_by_length = {}
    for code in set(became_valid):
        c = clean_code(code)
        if c:
            became_valid_by_length[str(len(c))] = became_valid_by_length.get(str(len(c)), 0) + 1

    for length in sorted(int(x) for x in clean_invalid.keys()):
        try:
            msg = await channel.send(embed=invalid_group_embed(list_name, length, clean_invalid[str(length)], last_run, became_valid_by_length.get(str(length), 0)), allowed_mentions=allowed)
            new_message_ids[str(length)] = msg.id
        except Exception:
            pass

    await safe_delete_message(channel, old_summary_id)
    for old_id in old_message_ids:
        if old_id not in new_message_ids.values():
            await safe_delete_message(channel, old_id)

    check_cfg["summary_message_id"] = summary_msg.id
    check_cfg["invalid_message_ids"] = new_message_ids


async def run_list_check(guild: discord.Guild, name: str, *, manual: bool = False) -> dict:
    name = list_key(name)
    lists = vanity_lists_for(guild.id)
    checks = list_checks_for(guild.id)
    if name not in lists:
        return {"ok": False, "error": "List does not exist."}
    if name not in checks:
        return {"ok": False, "error": "List checker is not set up for that list."}

    check_cfg = checks[name]
    codes = list(dict.fromkeys([clean_code(c) for c in lists.get(name, []) if clean_code(c)]))
    max_per_run = max(1, int(check_cfg.get("max_per_run", 2500)))
    delay = max(1.0, float(check_cfg.get("delay_seconds", 3.0)))
    codes = codes[:max_per_run]

    state = invalid_state_for(guild.id)
    list_state = state.setdefault(name, {})
    current_invalid = {str(k): set(clean_code(x) for x in v if clean_code(x)) for k, v in list_state.items()}
    valid, invalid, became_valid, errors = [], [], [], []

    for i, code in enumerate(codes, start=1):
        status = await invite_status(code)
        length = str(len(code))
        current_invalid.setdefault(length, set())
        if status == "valid":
            valid.append(code)
            if code in current_invalid[length]:
                current_invalid[length].remove(code)
                became_valid.append(code)
        elif status == "invalid":
            invalid.append(code)
            current_invalid[length].add(code)
        else:
            errors.append(f"{code}: {status}")
        if i < len(codes):
            await asyncio.sleep(delay)

    clean_state = {str(k): sorted(set(v)) for k, v in current_invalid.items() if v}
    state[name] = clean_state
    check_cfg["last_run"] = now_ts()
    check_cfg["next_run"] = now_ts() + int(check_cfg.get("interval_minutes", 30)) * 60
    check_cfg["last_counts"] = {
        "valid": len(set(valid)),
        "invalid": len(set(invalid)),
        "became_valid": len(set(became_valid)),
        "errors": len(errors),
        "checked": len(codes),
        "current_invalid": sum(len(v) for v in clean_state.values()),
    }
    checks[name] = check_cfg
    save_invalid_state(guild.id, state)

    valid_channel_id = int(check_cfg.get("valid_channel_id") or 0)
    if valid_channel_id:
        try:
            valid_channel = await fetch_messageable(valid_channel_id)
            if valid or became_valid or errors or manual:
                await valid_channel.send(embed=valid_results_embed(name, valid, became_valid, errors))
        except Exception:
            pass

    await send_fresh_invalid_embeds(guild.id, name, check_cfg, clean_state, sorted(set(became_valid)), errors, check_cfg.get("ping_role_id"))
    checks[name] = check_cfg
    save_list_checks(guild.id, checks)
    return {"ok": True, "checked": len(codes), "valid": len(set(valid)), "invalid": len(set(invalid)), "current_invalid": sum(len(v) for v in clean_state.values()), "became_valid": len(set(became_valid)), "errors": len(errors)}

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
        await interaction.response.send_message("You need manager access, Manage Server, or Administrator.", ephemeral=True)
        return False
    return True

# =========================================================
# CLAIM STATS / AUTOROLES
# =========================================================
def find_claim(claims: list, claim_id: Optional[str] = None, hunter_id: Optional[int] = None, code: Optional[str] = None) -> Optional[dict]:
    code = clean_code(code or "") if code else None
    for claim in reversed(claims):
        if claim_id and str(claim.get("id")) == str(claim_id):
            return claim
        if hunter_id and code and int(claim.get("user_id", 0)) == int(hunter_id) and clean_code(claim.get("code")) == code:
            return claim
    return None


def stats_for(guild_id: int) -> Dict[str, dict]:
    stats: Dict[str, dict] = {}
    for claim in claims_for(guild_id):
        uid = str(claim.get("user_id"))
        st = stats.setdefault(uid, {
            "claims": 0,
            "codes": [],
            "total_value": 0.0,
            "total_cut": 0.0,
            "most_valuable_claim": None,
            "last_claim_ts": 0,
        })
        st["claims"] += 1
        code = clean_code(claim.get("code", ""))
        if code and code not in st["codes"]:
            st["codes"].append(code)
        value = float(claim.get("value") or 0)
        cut = float(claim.get("hunter_cut") or 0)
        st["total_value"] = round(float(st["total_value"]) + value, 2)
        st["total_cut"] = round(float(st["total_cut"]) + cut, 2)
        st["last_claim_ts"] = max(int(st.get("last_claim_ts") or 0), int(claim.get("claimed_ts") or claim.get("logged_ts") or 0))
        best = st.get("most_valuable_claim")
        if value > 0 and (not best or value > float(best.get("value", 0))):
            st["most_valuable_claim"] = {"code": code, "value": value, "claim_id": claim.get("id")}
    return stats


def claim_count_for(guild_id: int, user_id: int) -> int:
    return int(stats_for(guild_id).get(str(user_id), {}).get("claims", 0))


async def sync_autoroles(member: discord.Member) -> None:
    cfg = config(member.guild.id)
    rules = sorted(cfg.get("autoroles", []), key=lambda r: int(r.get("claims_required", 0)))
    if not rules:
        return
    count = claim_count_for(member.guild.id, member.id)
    for rule in rules:
        role = member.guild.get_role(int(rule.get("role_id", 0)))
        needed = int(rule.get("claims_required", 0))
        if not role or role >= member.guild.me.top_role:
            continue
        try:
            if count >= needed and role not in member.roles:
                await member.add_roles(role, reason=f"Reached {needed} vanity claims")
            elif count < needed and role in member.roles:
                await member.remove_roles(role, reason=f"Below {needed} vanity claims")
        except Exception:
            pass


async def sync_all_autoroles(guild: discord.Guild) -> int:
    changed = 0
    for member in guild.members:
        before = {r.id for r in member.roles}
        await sync_autoroles(member)
        after = {r.id for r in member.roles}
        if before != after:
            changed += 1
    return changed


def top_claim_lines(guild: discord.Guild, limit: int = 10) -> List[str]:
    stats = stats_for(guild.id)
    ranked = sorted(stats.items(), key=lambda kv: (int(kv[1].get("claims", 0)), float(kv[1].get("total_value", 0))), reverse=True)[:limit]
    lines = []
    for pos, (uid, st) in enumerate(ranked, start=1):
        medal = "🥇" if pos == 1 else "🥈" if pos == 2 else "🥉" if pos == 3 else f"`#{pos}`"
        best = st.get("most_valuable_claim")
        best_text = f"`discord.gg/{best['code']}` ({money(float(best['value']))})" if best else "`No value yet`"
        lines.append(f"{medal} <@{uid}> — **{int(st.get('claims', 0))}** claims • best {best_text}")
    return lines

# =========================================================
# ANTI-SPAM
# =========================================================
def claim_spam_check(guild_id: int, user_id: int, code: str) -> Optional[str]:
    cfg = config(guild_id)
    claims = claims_for(guild_id)
    user_claims = [c for c in claims if int(c.get("user_id", 0)) == int(user_id)]
    last = max([int(c.get("logged_ts", 0)) for c in user_claims] or [0])
    cooldown = max(0, int(cfg.get("claim_cooldown_seconds", DEFAULT_CLAIM_COOLDOWN_SECONDS)))
    if cooldown and now_ts() - last < cooldown:
        remaining = cooldown - (now_ts() - last)
        return f"Slow down. You can log another claim in `{remaining}s`."
    max_per_hour = max(1, int(cfg.get("max_claims_per_hour", DEFAULT_MAX_CLAIMS_PER_HOUR)))
    recent = [c for c in user_claims if now_ts() - int(c.get("logged_ts", 0)) <= 3600]
    if len(recent) >= max_per_hour:
        return f"You hit the claim limit of `{max_per_hour}` claims per hour. Ask a manager if this is a mistake."
    for c in claims:
        if clean_code(c.get("code", "")) == code:
            return f"`discord.gg/{code}` was already logged. Ask a manager if this needs to be fixed."
    return None

# =========================================================
# EMBEDS
# =========================================================
def claim_embed(guild: discord.Guild, claim: dict) -> discord.Embed:
    e = embed("🏷️ Vanity Claim Logged", color=GOLD)
    e.description = (
        f"**Vanity:** `discord.gg/{claim.get('code')}`\n"
        f"**Hunter:** <@{claim.get('user_id')}>\n"
        f"**Logged:** <t:{int(claim.get('logged_ts', now_ts()))}:R>"
    )
    if float(claim.get("value") or 0) > 0:
        e.add_field(
            name="Value",
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
    total_claims = len(claims_for(guild.id))
    total_value = sum(float(c.get("value") or 0) for c in claims_for(guild.id))
    e = embed("🏆 Vanity Hunter Claim Leaderboard", color=GOLD)
    e.description = "\n".join(lines) if lines else "No claims logged yet."
    e.add_field(name="Server Totals", value=f"Claims: `{total_claims:,}` • Value: `{money(total_value)}`", inline=False)
    e.set_footer(text="Ranks are based on claim count. Attempts are not tracked.")
    return e


def hunter_panel_embed() -> discord.Embed:
    e = embed("🏹 Hunter Claim Panel", color=PURPLE)
    e.description = (
        "Use this panel to log successful vanity pulls and check your claim stats.\n\n"
        "**Claims only:** attempts/no-pulls are not tracked anymore.\n"
        "**Only log real pulls:** fake, duplicate, or spammed claims are blocked and may get you suspended."
    )
    e.add_field(
        name="What hunters use",
        value=(
            "**Log Claim** — submit a successful pull like `discord.gg/make`\n"
            "**My Stats** — view your claim count, values, cuts, and claimed vanities\n"
            "**Leaderboard** — see top hunters by claim count\n"
            "**Edit Claim / Remove Claim** — fix your own claim logs"
        ),
        inline=False,
    )
    e.add_field(name="Quick rule", value="**Do not log guesses, attempts, no-pulls, or rate-limit sessions. Claims only.**", inline=False)
    return e


def manager_panel_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    e = embed("🛠️ Manager Claim Panel", color=BLUE)
    e.description = "Manager tools for claim values, buyer finding, sales follow-up, logs, setup, anti-spam, and claim-count autoroles."
    e.add_field(
        name="Main Tools",
        value=(
            "**Value Claim** — add value/cut by Claim ID\n"
            "**Recent Claims** — review recent logs\n"
            "**Setup** — claim log channel, ping role, cooldown, hourly claim limit, leaderboard\n"
            "**Add/Remove Autorole** — automatically reward roles based on claim count\n"
            "**Autoroles** — view current role milestones\n"
            "**Buyer Responsibility** — managers are responsible for helping find buyers for claimed vanities\n"
            "**List Checker** — auto-check multiple word lists and update valid/invalid channels across servers"
        ),
        inline=False,
    )
    e.add_field(name="Recommended anti-spam defaults", value="Cooldown: `60s` • Max claims/hour: `6` • Duplicate claim blocking: `always on`", inline=False)
    if guild:
        cfg = config(guild.id)
        rules = sorted(cfg.get("autoroles", []), key=lambda r: int(r.get("claims_required", 0)))
        if rules:
            e.add_field(name="Current Autoroles", value="\n".join(f"<@&{r['role_id']}> at `{r['claims_required']}` claims" for r in rules)[:1024], inline=False)
        else:
            e.add_field(name="Current Autoroles", value="None set yet. Use **Add Autorole** to make claim milestones.", inline=False)
    return e


def help_home_embed() -> discord.Embed:
    e = embed("📘 Vanity Hunter Help Center", color=PURPLE)
    e.description = (
        "This bot is **claim-only**. It does not track attempts, no-pulls, or rate-limit sessions.\n\n"
        "Use the buttons below for the hunter guide, manager guide, rules, buyer responsibilities, and claim-role rewards."
    )
    e.add_field(name="Start here", value="Hunters should use the posted **Hunter Claim Panel**. Managers should use the **Manager Claim Panel** for setup, buyer follow-up, values, logs, and rewards.", inline=False)
    e.add_field(name="Recommended setup", value="Post a hunter panel in your job channel, set a claim-log channel, set a claim ping role, then add autoroles for claim milestones. Managers should also help find buyers for strong claims.", inline=False)
    return e


def hunter_guide_embed() -> discord.Embed:
    e = embed("🏹 Hunter Guide", color=PURPLE)
    e.description = "Use this if you are hired to pull vanities."
    e.add_field(name="How to log", value="Press **Log Claim** only after you successfully pull a vanity. Enter the vanity as `make` or `discord.gg/make` and add proof/details if needed.", inline=False)
    e.add_field(name="Your stats", value="Stats are based on **total successful claims**, not attempts. Your stats show claim count, value, cut, and claimed links.", inline=False)
    e.add_field(name="Do not log", value="Do not log attempts, failed pulls, rate limits, guesses, test claims, or someone else’s claim.", inline=False)
    return e


def manager_guide_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    e = embed("🛠️ Manager Guide", color=BLUE)
    e.description = "Use this if you manage hunters, values, claim logs, buyer outreach, sales follow-up, or rewards."
    e.add_field(name="Setup order", value="1. Post `/post_hunter_panel` in the hunter channel.\n2. Open `/manager_panel`.\n3. Press **Setup** and set channels/ping role.\n4. Press **Add Autorole** for claim milestones.\n5. Use **Recent Claims** and **Value Claim** to manage logs.\n6. Help find buyers for good claims and follow up until the vanity is sold or assigned.", inline=False)
    e.add_field(name="Manager responsibilities", value="• Review claim logs for accuracy\n• Add value/cut information\n• Help find buyers for claimed vanities\n• Follow up on strong pulls until they are sold, held, or assigned\n• Keep hunters from spam/fake logging", inline=False)
    e.add_field(name="Recommended anti-spam", value="Cooldown: `60 seconds`\nMax claims/hour: `6`\nDuplicate claim blocking: always enabled in the code.", inline=False)
    if guild:
        cfg = config(guild.id)
        e.add_field(name="Current settings", value=f"Claim cooldown: `{cfg.get('claim_cooldown_seconds', DEFAULT_CLAIM_COOLDOWN_SECONDS)}s`\nMax claims/hour: `{cfg.get('max_claims_per_hour', DEFAULT_MAX_CLAIMS_PER_HOUR)}`", inline=False)
    return e


def rules_embed() -> discord.Embed:
    e = embed("⚠️ Claim Rules & Punishments", color=RED)
    e.description = "These rules keep the claim system fair and stop spam/fake logs."
    e.add_field(name="Required", value="Log only real successful pulls. Use accurate vanity spelling. Add proof/details when needed. Fix mistakes with **Edit Claim** quickly.", inline=False)
    e.add_field(name="Not allowed", value="Fake claims, duplicate claims, spam logging, logging attempts/no-pulls, logging someone else’s pull, or editing claims to steal credit.", inline=False)
    e.add_field(name="Punishment note", value="**Incorrect or fake claim logging can get you suspended from the job.**", inline=False)
    return e


def rewards_embed(guild: Optional[discord.Guild] = None) -> discord.Embed:
    e = embed("🏅 Claim Autoroles & Rewards", color=GOLD)
    e.description = "Managers can automatically give roles when hunters reach a certain number of successful claims."
    if guild:
        rules = sorted(config(guild.id).get("autoroles", []), key=lambda r: int(r.get("claims_required", 0)))
        if rules:
            e.add_field(name="Current milestones", value="\n".join(f"<@&{r['role_id']}> — `{r['claims_required']}` claims" for r in rules)[:1024], inline=False)
        else:
            e.add_field(name="Current milestones", value="No autoroles set yet. Managers can add them from **Manager Panel → Add Autorole**.", inline=False)
    e.add_field(name="Recommended milestones", value="`3 claims` — Trial Hunter\n`10 claims` — Elite Hunter\n`25 claims` — Senior Hunter\n`50 claims` — Top Hunter", inline=False)
    return e

# =========================================================
# MESSAGE POST/REPLACE HELPERS
# =========================================================
async def post_or_replace_claim_embed(guild: discord.Guild, claim: dict, *, ping: bool = False) -> None:
    cfg = config(guild.id)
    channel_id = cfg.get("claim_log_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    old_message_id = claim.get("claim_message_id")
    if old_message_id:
        try:
            old = await channel.fetch_message(int(old_message_id))
            await old.delete()
        except Exception:
            pass
    content = None
    allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)
    if ping and cfg.get("claim_ping_role_id"):
        role = guild.get_role(int(cfg["claim_ping_role_id"]))
        if role:
            content = role.mention
    msg = await channel.send(content=content, embed=claim_embed(guild, claim), allowed_mentions=allowed)
    claim["claim_message_id"] = msg.id


async def refresh_leaderboard_now(guild: discord.Guild) -> None:
    cfg = config(guild.id)
    channel_id = cfg.get("leaderboard_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    lb = leaderboard_embed(guild)
    msg_id = cfg.get("leaderboard_message_id")
    try:
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(embed=lb)
            except Exception:
                msg = await channel.send(embed=lb)
                cfg["leaderboard_message_id"] = msg.id
        else:
            msg = await channel.send(embed=lb)
            cfg["leaderboard_message_id"] = msg.id
        cfg["leaderboard_last_update"] = now_ts()
        save_config(guild.id, cfg)
    except Exception:
        pass

# =========================================================
# BACKGROUND TASKS
# =========================================================
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
        if guild:
            await refresh_leaderboard_now(guild)


@leaderboard_loop.before_loop
async def before_leaderboard_loop():
    await bot.wait_until_ready()

# =========================================================
# MODALS
# =========================================================
class HunterClaimModal(discord.ui.Modal, title="Log Vanity Claim"):
    vanity = discord.ui.TextInput(label="Vanity claimed", placeholder="make or discord.gg/make", max_length=80)
    notes = discord.ui.TextInput(label="Notes / proof / details", placeholder="Optional proof, context, or manager notes", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        code = clean_code(str(self.vanity.value))
        if not code:
            return await interaction.response.send_message("Enter a real vanity like `make` or `discord.gg/make`.", ephemeral=True)
        lock_key = (interaction.guild.id, interaction.user.id)
        lock = claim_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            problem = claim_spam_check(interaction.guild.id, interaction.user.id, code)
            if problem:
                return await interaction.response.send_message(problem, ephemeral=True)
            claim = {
                "id": make_id(),
                "guild_id": interaction.guild.id,
                "user_id": interaction.user.id,
                "code": code,
                "notes": str(self.notes.value or "").strip(),
                "logged_ts": now_ts(),
                "claimed_ts": now_ts(),
                "value": 0.0,
                "cut_percent": 0.0,
                "hunter_cut": 0.0,
                "owner_cut": 0.0,
                "claim_message_id": None,
            }
            claims = claims_for(interaction.guild.id)
            claims.append(claim)
            save_claims(interaction.guild.id, claims)
            try:
                await post_or_replace_claim_embed(interaction.guild, claim, ping=True)
                # save message id back
                claims = claims_for(interaction.guild.id)
                found = find_claim(claims, claim_id=claim["id"])
                if found:
                    found.update({"claim_message_id": claim.get("claim_message_id")})
                    save_claims(interaction.guild.id, claims)
            except Exception:
                pass
            await sync_autoroles(interaction.user)
            await refresh_leaderboard_now(interaction.guild)
            await interaction.response.send_message(f"Logged claim `discord.gg/{code}`. Claim ID: `{claim['id']}`", ephemeral=True)


class HunterEditClaimModal(discord.ui.Modal, title="Edit Your Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", placeholder="Paste the Claim ID", max_length=40)
    vanity = discord.ui.TextInput(label="New vanity", placeholder="make or discord.gg/make", max_length=80)
    notes = discord.ui.TextInput(label="New notes", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        claims = claims_for(interaction.guild.id)
        claim = find_claim(claims, claim_id=str(self.claim_id.value).strip())
        if not claim:
            return await interaction.response.send_message("Claim not found.", ephemeral=True)
        if int(claim.get("user_id", 0)) != interaction.user.id and not has_manager_access(interaction.user):
            return await interaction.response.send_message("You can only edit your own claims.", ephemeral=True)
        new_code = clean_code(str(self.vanity.value))
        if not new_code:
            return await interaction.response.send_message("Enter a valid vanity.", ephemeral=True)
        # prevent changing to a code already used by another claim
        for other in claims:
            if str(other.get("id")) != str(claim.get("id")) and clean_code(other.get("code", "")) == new_code:
                return await interaction.response.send_message(f"`discord.gg/{new_code}` is already logged on another claim.", ephemeral=True)
        claim["code"] = new_code
        claim["notes"] = str(self.notes.value or "").strip()
        claim["edited_ts"] = now_ts()
        save_claims(interaction.guild.id, claims)
        try:
            await post_or_replace_claim_embed(interaction.guild, claim, ping=False)
            save_claims(interaction.guild.id, claims)
        except Exception:
            pass
        await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message(f"Updated claim `{claim.get('id')}`.", ephemeral=True)


class HunterRemoveClaimModal(discord.ui.Modal, title="Remove Your Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", placeholder="Paste the Claim ID", max_length=40)
    confirm = discord.ui.TextInput(label="Type REMOVE", placeholder="REMOVE", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if str(self.confirm.value).strip().upper() != "REMOVE":
            return await interaction.response.send_message("Removal cancelled. You must type `REMOVE`.", ephemeral=True)
        claims = claims_for(interaction.guild.id)
        claim = find_claim(claims, claim_id=str(self.claim_id.value).strip())
        if not claim:
            return await interaction.response.send_message("Claim not found.", ephemeral=True)
        if int(claim.get("user_id", 0)) != interaction.user.id and not has_manager_access(interaction.user):
            return await interaction.response.send_message("You can only remove your own claims.", ephemeral=True)
        channel_id = config(interaction.guild.id).get("claim_log_channel_id")
        if channel_id and claim.get("claim_message_id"):
            channel = interaction.guild.get_channel(int(channel_id))
            if channel:
                try:
                    msg = await channel.fetch_message(int(claim["claim_message_id"]))
                    await msg.delete()
                except Exception:
                    pass
        claims = [c for c in claims if str(c.get("id")) != str(claim.get("id"))]
        save_claims(interaction.guild.id, claims)
        member = interaction.guild.get_member(int(claim.get("user_id", 0)))
        if member:
            await sync_autoroles(member)
        await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message(f"Removed claim `{claim.get('id')}`.", ephemeral=True)


class ManagerValueModal(discord.ui.Modal, title="Value A Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", placeholder="Paste the Claim ID", max_length=40)
    value = discord.ui.TextInput(label="Total value", placeholder="Example: 100", max_length=20)
    cut_percent = discord.ui.TextInput(label="Hunter cut percent", placeholder="Example: 50", max_length=10)
    notes = discord.ui.TextInput(label="Manager notes", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        claims = claims_for(interaction.guild.id)
        claim = find_claim(claims, claim_id=str(self.claim_id.value).strip())
        if not claim:
            return await interaction.response.send_message("Claim not found.", ephemeral=True)
        value, err = parse_money(str(self.value.value))
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        cut_percent, err = parse_money(str(self.cut_percent.value))
        if err or cut_percent is None or cut_percent > 100:
            return await interaction.response.send_message("Cut percent must be from `0` to `100`.", ephemeral=True)
        hunter_cut = round(float(value) * (float(cut_percent) / 100.0), 2)
        owner_cut = round(float(value) - hunter_cut, 2)
        claim.update({
            "value": float(value),
            "cut_percent": float(cut_percent),
            "hunter_cut": hunter_cut,
            "owner_cut": owner_cut,
            "valued_by": interaction.user.id,
            "valued_ts": now_ts(),
        })
        save_claims(interaction.guild.id, claims)
        log = {"id": make_id(), "claim_id": claim.get("id"), "user_id": claim.get("user_id"), "code": claim.get("code"), "value": value, "cut_percent": cut_percent, "updated_by": interaction.user.id, "notes": str(self.notes.value or ""), "logged_ts": now_ts()}
        logs = value_logs_for(interaction.guild.id)
        logs.append(log)
        save_value_logs(interaction.guild.id, logs)
        try:
            await post_or_replace_claim_embed(interaction.guild, claim, ping=False)
            save_claims(interaction.guild.id, claims)
        except Exception:
            pass
        cfg = config(interaction.guild.id)
        if cfg.get("value_log_channel_id"):
            channel = interaction.guild.get_channel(int(cfg["value_log_channel_id"]))
            if channel:
                try:
                    await channel.send(embed=value_update_embed(interaction.guild, claim, interaction.user.id, str(self.notes.value or "")))
                except Exception:
                    pass
        await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message(f"Valued claim `{claim.get('id')}` at `{money(value)}`.", ephemeral=True)


class ManagerSetupModal(discord.ui.Modal, title="Setup Claim System"):
    claim_log_channel = discord.ui.TextInput(label="Claim log channel ID", placeholder="1234567890", required=False, max_length=30)
    value_log_channel = discord.ui.TextInput(label="Value log channel ID", placeholder="1234567890", required=False, max_length=30)
    leaderboard_channel = discord.ui.TextInput(label="Leaderboard channel ID", placeholder="1234567890", required=False, max_length=30)
    ping_role = discord.ui.TextInput(label="Claim ping role ID", placeholder="Optional role ID to ping on new claims", required=False, max_length=30)
    anti_spam = discord.ui.TextInput(label="Cooldown seconds, max claims/hour", placeholder="60, 5", required=False, max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        cfg = config(interaction.guild.id)
        def snowflake(raw: str) -> Optional[int]:
            m = re.search(r"\d{15,25}", str(raw or ""))
            return int(m.group(0)) if m else None
        if self.claim_log_channel.value:
            cfg["claim_log_channel_id"] = snowflake(str(self.claim_log_channel.value))
        if self.value_log_channel.value:
            cfg["value_log_channel_id"] = snowflake(str(self.value_log_channel.value))
        if self.leaderboard_channel.value:
            cfg["leaderboard_channel_id"] = snowflake(str(self.leaderboard_channel.value))
            cfg["leaderboard_last_update"] = 0
        if self.ping_role.value:
            cfg["claim_ping_role_id"] = snowflake(str(self.ping_role.value))
        if self.anti_spam.value:
            parts = [p.strip() for p in str(self.anti_spam.value).split(",")]
            if len(parts) >= 1 and parts[0]:
                cooldown, err = parse_int(parts[0], "Cooldown", 0, 86400)
                if err:
                    return await interaction.response.send_message(err, ephemeral=True)
                cfg["claim_cooldown_seconds"] = cooldown
            if len(parts) >= 2 and parts[1]:
                max_hour, err = parse_int(parts[1], "Max claims/hour", 1, 100)
                if err:
                    return await interaction.response.send_message(err, ephemeral=True)
                cfg["max_claims_per_hour"] = max_hour
        save_config(interaction.guild.id, cfg)
        await refresh_leaderboard_now(interaction.guild)
        await interaction.response.send_message("Saved claim system setup.", ephemeral=True)


class AutoroleAddModal(discord.ui.Modal, title="Add Claim Autorole"):
    role_id = discord.ui.TextInput(label="Role ID", placeholder="Role to give", max_length=30)
    claims_required = discord.ui.TextInput(label="Claims required", placeholder="Example: 10", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        rid_match = re.search(r"\d{15,25}", str(self.role_id.value))
        if not rid_match:
            return await interaction.response.send_message("Paste a valid role ID or role mention.", ephemeral=True)
        role_id = int(rid_match.group(0))
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("I cannot find that role in this server.", ephemeral=True)
        needed, err = parse_int(str(self.claims_required.value), "Claims required", 1, 100000)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        cfg = config(interaction.guild.id)
        rules = [r for r in cfg.get("autoroles", []) if int(r.get("role_id", 0)) != role_id]
        rules.append({"role_id": role_id, "claims_required": int(needed)})
        cfg["autoroles"] = sorted(rules, key=lambda r: int(r["claims_required"]))
        save_config(interaction.guild.id, cfg)
        changed = await sync_all_autoroles(interaction.guild)
        await interaction.response.send_message(f"Autorole saved: {role.mention} at `{needed}` claims. Synced `{changed}` members.", ephemeral=True)


class AutoroleRemoveModal(discord.ui.Modal, title="Remove Claim Autorole"):
    role_id = discord.ui.TextInput(label="Role ID", placeholder="Role to remove from autorole rules", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        rid_match = re.search(r"\d{15,25}", str(self.role_id.value))
        if not rid_match:
            return await interaction.response.send_message("Paste a valid role ID or role mention.", ephemeral=True)
        role_id = int(rid_match.group(0))
        cfg = config(interaction.guild.id)
        before = len(cfg.get("autoroles", []))
        cfg["autoroles"] = [r for r in cfg.get("autoroles", []) if int(r.get("role_id", 0)) != role_id]
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message("Removed autorole rule." if len(cfg["autoroles"]) < before else "No matching autorole rule found.", ephemeral=True)

# =========================================================
# VIEWS
# =========================================================
class HunterPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Log Claim", style=discord.ButtonStyle.success, emoji="🏷️", custom_id="vh_claim_log")
    async def log_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterClaimModal())

    @discord.ui.button(label="My Stats", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="vh_claim_stats")
    async def my_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = stats_for(interaction.guild.id).get(str(interaction.user.id), {"claims": 0, "codes": [], "total_value": 0, "total_cut": 0})
        codes = st.get("codes", [])
        shown = ", ".join(f"`discord.gg/{c}`" for c in codes[:25]) if codes else "No claims yet."
        if len(codes) > 25:
            shown += f"\n...and `{len(codes)-25}` more."
        e = embed("📊 Your Claim Stats", color=BLUE)
        e.description = (
            f"**Claims:** `{int(st.get('claims', 0))}`\n"
            f"**Total Value:** `{money(float(st.get('total_value', 0)))}`\n"
            f"**Your Cut:** `{money(float(st.get('total_cut', 0)))}`\n\n"
            f"**Claimed Vanities:**\n{shown}"
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="vh_claim_leaderboard")
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=leaderboard_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Edit Claim", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="vh_claim_edit")
    async def edit_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterEditClaimModal())

    @discord.ui.button(label="Remove Claim", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="vh_claim_remove")
    async def remove_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HunterRemoveClaimModal())


class ManagerPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Value Claim", style=discord.ButtonStyle.success, emoji="💸", custom_id="vh_manager_value")
    async def value_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(ManagerValueModal())

    @discord.ui.button(label="Recent Claims", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="vh_manager_recent")
    async def recent_claims(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        recent = list(reversed(claims_for(interaction.guild.id)))[:15]
        e = embed("📋 Recent Claims", color=PURPLE)
        if not recent:
            e.description = "No claims yet."
        else:
            e.description = "\n".join(f"`{c.get('id')}` — `discord.gg/{c.get('code')}` by <@{c.get('user_id')}> • <t:{int(c.get('logged_ts', now_ts()))}:R>" for c in recent)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="Setup", style=discord.ButtonStyle.primary, emoji="⚙️", custom_id="vh_manager_setup")
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(ManagerSetupModal())

    @discord.ui.button(label="Add Autorole", style=discord.ButtonStyle.primary, emoji="➕", custom_id="vh_autorole_add")
    async def add_autorole(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(AutoroleAddModal())

    @discord.ui.button(label="Remove Autorole", style=discord.ButtonStyle.danger, emoji="➖", custom_id="vh_autorole_remove")
    async def remove_autorole(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(AutoroleRemoveModal())

    @discord.ui.button(label="Autoroles", style=discord.ButtonStyle.secondary, emoji="🏅", custom_id="vh_autorole_view")
    async def view_autoroles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        cfg = config(interaction.guild.id)
        rules = sorted(cfg.get("autoroles", []), key=lambda r: int(r.get("claims_required", 0)))
        e = embed("🏅 Claim Autoroles", color=GOLD)
        e.description = "\n".join(f"<@&{r['role_id']}> — `{r['claims_required']}` claims" for r in rules) if rules else "No autoroles set."
        await interaction.response.send_message(embed=e, ephemeral=True)

class HelpPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Hunter Guide", style=discord.ButtonStyle.primary, emoji="🏹", custom_id="vh_help_hunter")
    async def hunter_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=hunter_guide_embed(), ephemeral=True)

    @discord.ui.button(label="Manager Guide", style=discord.ButtonStyle.primary, emoji="🛠️", custom_id="vh_help_manager")
    async def manager_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=manager_guide_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Rules", style=discord.ButtonStyle.danger, emoji="⚠️", custom_id="vh_help_rules")
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=rules_embed(), ephemeral=True)

    @discord.ui.button(label="Claim Roles", style=discord.ButtonStyle.secondary, emoji="🏅", custom_id="vh_help_rewards")
    async def rewards(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=rewards_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Recommended Setup", style=discord.ButtonStyle.secondary, emoji="✅", custom_id="vh_help_recommended")
    async def recommended(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = embed("✅ Recommended Manager Settings", color=GREEN)
        e.description = "A clean starting setup for your claim-only vanity system."
        e.add_field(name="Anti-spam", value="Cooldown: `60 seconds`\nMax claims/hour: `6`\nDuplicate claim blocking: `on by default`", inline=False)
        e.add_field(name="Channels", value="Set one private/staff **claim log channel** and one public **leaderboard channel**. Use a ping role only if managers need instant alerts.", inline=False)
        e.add_field(name="Buyer workflow", value="Managers should watch recent claims, identify valuable pulls, contact potential buyers, post/surface the vanity where appropriate, and update the team when a buyer is found.", inline=False)
        e.add_field(name="Auto list checker", value="Use `/vanity_list_add` to save lists, then `/vanity_list_setup` to choose separate valid/invalid channels, interval, optional ping role, and auto-updating grouped invalid embeds.", inline=False)
        e.add_field(name="Autorole milestones", value="`3` claims = Trial Hunter\n`10` claims = Elite Hunter\n`25` claims = Senior Hunter\n`50` claims = Top Hunter", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


# =========================================================
# SLASH COMMANDS
# =========================================================
@bot.tree.command(name="help", description="Open the claim-only vanity hunter help center.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=help_home_embed(), view=HelpPanelView(), ephemeral=True)


@bot.tree.command(name="post_help_panel", description="Managers: post a public help panel for hunters and managers.")
async def post_help_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not await require_manager(interaction):
        return
    target = channel or interaction.channel
    await target.send(embed=help_home_embed(), view=HelpPanelView())
    await interaction.response.send_message(f"Posted help panel in {target.mention}.", ephemeral=True)


@bot.tree.command(name="hunter_panel", description="Open your private claim-only hunter panel.")
async def hunter_panel(interaction: discord.Interaction):
    await interaction.response.send_message(embed=hunter_panel_embed(), view=HunterPanelView(), ephemeral=True)


@bot.tree.command(name="manager_panel", description="Open the private manager panel.")
async def manager_panel(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.response.send_message(embed=manager_panel_embed(interaction.guild), view=ManagerPanelView(), ephemeral=True)


@bot.tree.command(name="post_hunter_panel", description="Managers: post a public hunter claim panel.")
async def post_hunter_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not await require_manager(interaction):
        return
    target = channel or interaction.channel
    await target.send(embed=hunter_panel_embed(), view=HunterPanelView())
    await interaction.response.send_message(f"Posted hunter claim panel in {target.mention}.", ephemeral=True)


@bot.tree.command(name="post_manager_panel", description="Admins: post a public manager panel.")
async def post_manager_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not await require_admin(interaction):
        return
    target = channel or interaction.channel
    await target.send(embed=manager_panel_embed(interaction.guild), view=ManagerPanelView())
    await interaction.response.send_message(f"Posted manager panel in {target.mention}.", ephemeral=True)


@bot.tree.command(name="vanity_access_add_user", description="Allow a user to use manager claim tools.")
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


@bot.tree.command(name="vanity_access_add_role", description="Allow a role to use manager claim tools.")
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
# VANITY LIST CHECKER COMMANDS / LOOP
# =========================================================
@bot.tree.command(name="vanity_list_add", description="Add or update a saved vanity word list for auto-checking.")
@app_commands.describe(name="Short list name", words="Comma, space, or newline separated vanity words/links", replace_existing="Replace the old list instead of merging")
async def vanity_list_add(interaction: discord.Interaction, name: str, words: str, replace_existing: bool = False):
    if not await require_manager(interaction):
        return
    key = list_key(name)
    codes = parse_codes(words)
    if not key or not codes:
        return await interaction.response.send_message("Give me a list name and at least one valid word/code.", ephemeral=True)
    lists = vanity_lists_for(interaction.guild.id)
    merged = codes if replace_existing else list(dict.fromkeys(list(lists.get(key, [])) + codes))
    lists[key] = merged
    save_vanity_lists(interaction.guild.id, lists)
    await interaction.response.send_message(f"Saved list `{key}` with `{len(merged)}` total words.", ephemeral=True)


@bot.tree.command(name="vanity_list_remove", description="Remove a saved vanity word list and its checker settings.")
async def vanity_list_remove(interaction: discord.Interaction, name: str):
    if not await require_manager(interaction):
        return
    key = list_key(name)
    lists = vanity_lists_for(interaction.guild.id); checks = list_checks_for(interaction.guild.id); state = invalid_state_for(interaction.guild.id)
    existed = key in lists or key in checks or key in state
    lists.pop(key, None); checks.pop(key, None); state.pop(key, None)
    save_vanity_lists(interaction.guild.id, lists); save_list_checks(interaction.guild.id, checks); save_invalid_state(interaction.guild.id, state)
    await interaction.response.send_message((f"Removed `{key}`." if existed else "That list was not found."), ephemeral=True)


@bot.tree.command(name="vanity_list_setup", description="Set private checker output to the Vanity Hunters server by channel/role IDs.")
@app_commands.describe(
    name="Saved list name from this private checker server",
    valid_channel_id="Channel ID for valid/became-valid results. Can be in either server.",
    hunter_invalid_channel_id="Vanity Hunters channel ID where the fresh invalid list embeds get posted.",
    hunter_ping_role_id="Optional Vanity Hunters role ID to ping when the fresh list posts.",
    interval_minutes="How often to auto-check",
    delay_seconds="Delay between checks",
    max_per_run="Max words checked per run"
)
async def vanity_list_setup(
    interaction: discord.Interaction,
    name: str,
    valid_channel_id: str,
    hunter_invalid_channel_id: str,
    hunter_ping_role_id: Optional[str] = None,
    interval_minutes: app_commands.Range[int, 5, 10080] = 30,
    delay_seconds: app_commands.Range[float, 1.0, 30.0] = 3.0,
    max_per_run: app_commands.Range[int, 1, 5000] = 2500,
):
    if not await require_manager(interaction):
        return
    key = list_key(name)
    lists = vanity_lists_for(interaction.guild.id)
    if key not in lists:
        return await interaction.response.send_message("That list does not exist yet. Use `/vanity_list_add` in the private checker server first.", ephemeral=True)

    valid_match = re.search(r"\d{15,25}", str(valid_channel_id or ""))
    invalid_match = re.search(r"\d{15,25}", str(hunter_invalid_channel_id or ""))
    ping_match = re.search(r"\d{15,25}", str(hunter_ping_role_id or ""))
    if not valid_match or not invalid_match:
        return await interaction.response.send_message("Paste valid channel IDs for both `valid_channel_id` and `hunter_invalid_channel_id`.", ephemeral=True)

    valid_id = int(valid_match.group(0))
    invalid_id = int(invalid_match.group(0))
    ping_id = int(ping_match.group(0)) if ping_match else None

    try:
        await fetch_messageable(valid_id)
        await fetch_messageable(invalid_id)
    except Exception:
        return await interaction.response.send_message(
            "I could not access one of those channels. Make sure the bot is in both servers and has View Channel, Send Messages, Embed Links, Mention Roles, and Read Message History.",
            ephemeral=True,
        )

    checks = list_checks_for(interaction.guild.id)
    old = checks.get(key, {})
    checks[key] = {
        "enabled": True,
        "valid_channel_id": valid_id,
        "invalid_channel_id": invalid_id,
        "hunter_invalid_channel_id": invalid_id,
        "ping_role_id": ping_id,
        "interval_minutes": int(interval_minutes),
        "delay_seconds": float(delay_seconds),
        "max_per_run": int(max_per_run),
        "last_run": int(old.get("last_run", 0) or 0),
        "next_run": now_ts() + int(interval_minutes) * 60,
        "summary_message_id": old.get("summary_message_id"),
        "invalid_message_ids": old.get("invalid_message_ids", {}),
        "last_counts": old.get("last_counts", {}),
    }
    save_list_checks(interaction.guild.id, checks)
    await interaction.response.send_message(
        f"Setup saved for `{key}`.\n"
        f"Private checker server: `{interaction.guild.name}`\n"
        f"Valid/became-valid results channel: <#{valid_id}>\n"
        f"Fresh invalid list channel in Vanity Hunters: <#{invalid_id}>\n"
        f"Ping role in Vanity Hunters: {f'<@&{ping_id}>' if ping_id else '`none`'}\n"
        f"Interval: `{interval_minutes}m` • Delay: `{delay_seconds}s` • Max/run: `{max_per_run}`\n\n"
        "Every refresh sends the new list first, then deletes the old list messages so workers only see the newest list.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="vanity_list_run", description="Run a saved vanity list check now.")
async def vanity_list_run(interaction: discord.Interaction, name: str):
    if not await require_manager(interaction):
        return
    key = list_key(name)
    if list_check_lock.locked():
        return await interaction.response.send_message("A list check is already running. Try again after it finishes.", ephemeral=True)
    await interaction.response.send_message(f"Running list check for `{key}` now. Results will post in the configured channels.", ephemeral=True)
    async with list_check_lock:
        result = await run_list_check(interaction.guild, key, manual=True)
    if not result.get("ok"):
        await interaction.followup.send(f"Could not run `{key}`: {result.get('error')}", ephemeral=True)
    else:
        await interaction.followup.send(f"Finished `{key}` — checked `{result['checked']}`, invalid `{result['invalid']}`, valid `{result['valid']}`, became valid `{result['became_valid']}`.", ephemeral=True)


@bot.tree.command(name="vanity_lists", description="Show saved vanity lists and auto-check settings.")
async def vanity_lists(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    lists = vanity_lists_for(interaction.guild.id); checks = list_checks_for(interaction.guild.id)
    e = embed("📋 Vanity Word Lists", color=PURPLE)
    if not lists:
        e.description = "No lists yet. Use `/vanity_list_add`."
    else:
        lines = []
        for name, codes in sorted(lists.items()):
            c = checks.get(name, {})
            status = "enabled" if c.get("enabled") else "not setup"
            next_run = f" • next <t:{int(c.get('next_run', 0))}:R>" if c.get("next_run") else ""
            lines.append(f"`{name}` — `{len(codes)}` words • `{status}`{next_run}")
        e.description = "\n".join(lines)[:3900]
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="vanity_list_disable", description="Pause auto-checking for a saved list.")
async def vanity_list_disable(interaction: discord.Interaction, name: str):
    if not await require_manager(interaction):
        return
    key = list_key(name); checks = list_checks_for(interaction.guild.id)
    if key not in checks:
        return await interaction.response.send_message("That list checker is not set up.", ephemeral=True)
    checks[key]["enabled"] = False; save_list_checks(interaction.guild.id, checks)
    await interaction.response.send_message(f"Disabled auto-checking for `{key}`.", ephemeral=True)


@bot.tree.command(name="vanity_list_enable", description="Resume auto-checking for a saved list.")
async def vanity_list_enable(interaction: discord.Interaction, name: str):
    if not await require_manager(interaction):
        return
    key = list_key(name); checks = list_checks_for(interaction.guild.id)
    if key not in checks:
        return await interaction.response.send_message("That list checker is not set up.", ephemeral=True)
    checks[key]["enabled"] = True; checks[key]["next_run"] = now_ts() + int(checks[key].get("interval_minutes", 30)) * 60
    save_list_checks(interaction.guild.id, checks)
    await interaction.response.send_message(f"Enabled auto-checking for `{key}`.", ephemeral=True)


@tasks.loop(seconds=30)
async def list_checker_loop():
    if list_check_lock.locked():
        return
    all_checks = load_json(LIST_CHECKS_FILE, {})
    for guild_id, checks in list(all_checks.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        for name, cfg in list(checks.items()):
            if not cfg.get("enabled", True) or now_ts() < int(cfg.get("next_run", 0) or 0):
                continue
            async with list_check_lock:
                await run_list_check(guild, name, manual=False)


@list_checker_loop.before_loop
async def before_list_checker_loop():
    await bot.wait_until_ready()

# =========================================================
# EVENTS
# =========================================================
@bot.event
async def on_ready():
    ensure_dirs()
    bot.add_view(HunterPanelView())
    bot.add_view(ManagerPanelView())
    bot.add_view(HelpPanelView())
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Slash sync failed: {e}")
    if not leaderboard_loop.is_running():
        leaderboard_loop.start()
    if not list_checker_loop.is_running():
        list_checker_loop.start()


if __name__ == "__main__":
    ensure_dirs()
    if not TOKEN:
        raise SystemExit("Missing TOKEN environment variable.")
    bot.run(TOKEN)
