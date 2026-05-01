"""
Drop-in Vanity Hunter + Vanity Checker cog for discord.py 2.x

Setup in your main bot file:
1) Put this file beside your main bot file.
2) Add this near your other imports:
   from vanity_hunter_cog import setup_vanity_hunter
3) In on_ready, before bot.tree.sync(), add once:
   await setup_vanity_hunter(bot)

This cog adds slash commands for:
- vanity checking valid/invalid codes
- recent changes embeds
- scheduled auto checks for multiple named lists
- channel IDs + role pings
- invalid txt files grouped by length
- hunter sessions, assignments, attempt logs, claims, and leaderboards
"""

import os
import re
import json
import time
import math
import asyncio
import random
from pathlib import Path
from collections import defaultdict
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

# =========================
# STORAGE / LIMITS
# =========================
DATA_DIR = Path("data")
VANITY_DIR = DATA_DIR / "vanity_hunter"
INVALID_DIR = VANITY_DIR / "invalid_vanities"
CONFIG_FILE = VANITY_DIR / "config.json"
LISTS_FILE = VANITY_DIR / "lists.json"
SCHEDULES_FILE = VANITY_DIR / "schedules.json"
STATE_FILE = VANITY_DIR / "state.json"
HUNTERS_FILE = VANITY_DIR / "hunters.json"
SESSIONS_FILE = VANITY_DIR / "sessions.json"
ATTEMPTS_FILE = VANITY_DIR / "attempts.json"
CLAIMS_FILE = VANITY_DIR / "claims.json"
SALES_FILE = VANITY_DIR / "sales.json"
WATCHES_FILE = VANITY_DIR / "watches.json"

TRACKED_LENGTHS = range(1, 33)
DEFAULT_DELAY_SECONDS = 3.0
DEFAULT_BACKOFF_SECONDS = 60.0
MAX_CODES_PER_MANUAL_CHECK = 1000
MAX_CODES_PER_AUTOCHECK = 2500
MAX_EMBED_LINES = 30
PRIORITY_KEYWORDS = {
    "god", "ego", "vip", "og", "pmo", "oml", "wtf", "lmao", "rich", "rare", "heal", "prey",
    "void", "envy", "aura", "glow", "cute", "kiss", "love", "shop", "cash", "bank", "drip",
    "evil", "dark", "soft", "mean", "cold", "fame", "viral", "star", "user", "trap", "plug"
}

# =========================
# SMALL HELPERS
# =========================
def ensure_dirs() -> None:
    VANITY_DIR.mkdir(parents=True, exist_ok=True)
    INVALID_DIR.mkdir(parents=True, exist_ok=True)
    for length in TRACKED_LENGTHS:
        p = INVALID_DIR / f"invalid_{length}_letters.txt"
        p.touch(exist_ok=True)


def load_json(path: Path, default):
    ensure_dirs()
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def now_ts() -> int:
    return int(time.time())


def guild_key(guild_id: int) -> str:
    return str(guild_id)


def user_key(user_id: int) -> str:
    return str(user_id)


def clean_invite_code(item: str) -> str:
    return (
        str(item)
        .replace("https://discord.gg/", "")
        .replace("http://discord.gg/", "")
        .replace("discord.gg/", "")
        .replace("https://discord.com/invite/", "")
        .replace("http://discord.com/invite/", "")
        .replace("discord.com/invite/", "")
        .strip()
        .strip("/")
    )


def normalize_code(code: str) -> str:
    code = clean_invite_code(code).strip().lower()
    code = re.sub(r"[^a-z0-9_-]", "", code)
    return code[:32]


def parse_codes(raw: str) -> List[str]:
    if not raw:
        return []
    pieces = re.split(r"[,\n\s]+", raw)
    out, seen = [], set()
    for item in pieces:
        code = normalize_code(item)
        if code and code not in seen:
            out.append(code)
            seen.add(code)
    return out


def parse_role_ids(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    ids = []
    for item in re.findall(r"\d{15,25}", raw):
        rid = int(item)
        if rid not in ids:
            ids.append(rid)
    return ids


def mention_roles(guild: discord.Guild, role_ids: List[int]) -> str:
    mentions = []
    for rid in role_ids:
        role = guild.get_role(int(rid))
        if role:
            mentions.append(role.mention)
    return " ".join(mentions)


def chunk_text(items: List[str], limit: int = 900) -> str:
    if not items:
        return "None"
    lines, total = [], 0
    for item in items:
        line = str(item)
        if total + len(line) + 1 > limit:
            lines.append(f"...and {len(items) - len(lines)} more")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) if lines else "None"


def color(rgb: Tuple[int, int, int]) -> discord.Color:
    return discord.Color.from_rgb(*rgb)


DARK = color((35, 35, 42))
GREEN = color((62, 180, 100))
RED = color((220, 75, 75))
GOLD = color((245, 185, 75))
PURPLE = color((155, 95, 255))


def make_embed(title: str, description: str = "", c: discord.Color = DARK) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=c)
    e.timestamp = discord.utils.utcnow()
    return e


def is_adminish(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild


def parse_snowflake(value) -> Optional[int]:
    if value is None:
        return None
    match = re.search(r"\d{15,25}", str(value))
    return int(match.group(0)) if match else None


def vanity_score(code: str, known_invalid: bool = False) -> int:
    code = normalize_code(code)
    if not code:
        return 0
    score = 0
    length = len(code)
    if length <= 2:
        score += 100
    elif length == 3:
        score += 85
    elif length == 4:
        score += 65
    elif length == 5:
        score += 45
    elif length <= 8:
        score += 25
    else:
        score += 5
    if code.isalpha():
        score += 12
    if code.isalnum() and not any(ch in code for ch in "_-"):
        score += 8
    if code in PRIORITY_KEYWORDS:
        score += 35
    if any(word in code for word in PRIORITY_KEYWORDS):
        score += 10
    if known_invalid:
        score += 25
    if re.search(r"(.)\1\1", code):
        score -= 8
    if "_" in code or "-" in code:
        score -= 10
    return max(score, 0)


def parse_money(value: str) -> float:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        return round(float(text), 2)
    except Exception:
        return 0.0


def get_vanity_config(guild_id: int) -> dict:
    data = load_json(CONFIG_FILE, {})
    return data.setdefault(guild_key(guild_id), {"manager_users": [], "manager_roles": []})


def save_vanity_config(guild_id: int, cfg: dict) -> None:
    data = load_json(CONFIG_FILE, {})
    data[guild_key(guild_id)] = cfg
    save_json(CONFIG_FILE, data)


def has_vanity_access(member: discord.Member) -> bool:
    if is_adminish(member):
        return True
    cfg = get_vanity_config(member.guild.id)
    if str(member.id) in {str(x) for x in cfg.get("manager_users", [])}:
        return True
    member_roles = {str(role.id) for role in member.roles}
    allowed_roles = {str(x) for x in cfg.get("manager_roles", [])}
    return bool(member_roles & allowed_roles)


async def require_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not is_adminish(interaction.user):
        await interaction.response.send_message("You need Administrator or Manage Server permission.", ephemeral=True)
        return False
    return True


async def require_vanity_access(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not has_vanity_access(interaction.user):
        await interaction.response.send_message("You need vanity manager access or Manage Server/Admin permission.", ephemeral=True)
        return False
    return True


# =========================
# COG
# =========================
class VanityHunterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_dirs()
        self.invalid_cache: Dict[int, set[str]] = defaultdict(set)
        self.check_lock = asyncio.Lock()
        self.stop_requested = False
        self.running_label: Optional[str] = None
        self.load_invalid_cache()
        self.auto_check_loop.start()

    def cog_unload(self):
        self.auto_check_loop.cancel()

    # ---------- files/cache ----------
    def invalid_path(self, length: int) -> Path:
        ensure_dirs()
        return INVALID_DIR / f"invalid_{length}_letters.txt"

    def load_invalid_cache(self) -> None:
        self.invalid_cache.clear()
        ensure_dirs()
        for length in TRACKED_LENGTHS:
            path = self.invalid_path(length)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        code = normalize_code(line)
                        if code and len(code) == length:
                            self.invalid_cache[length].add(code)
            except Exception:
                pass

    def rewrite_invalid_file(self, length: int) -> None:
        path = self.invalid_path(length)
        codes = sorted(self.invalid_cache[length])
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for code in codes:
                f.write(code + "\n")

    def get_lists(self, guild_id: int) -> dict:
        data = load_json(LISTS_FILE, {})
        return data.setdefault(guild_key(guild_id), {})

    def save_lists(self, guild_id: int, lists: dict) -> None:
        data = load_json(LISTS_FILE, {})
        data[guild_key(guild_id)] = lists
        save_json(LISTS_FILE, data)

    def get_schedules(self, guild_id: int) -> dict:
        data = load_json(SCHEDULES_FILE, {})
        return data.setdefault(guild_key(guild_id), {})

    def save_schedules(self, guild_id: int, schedules: dict) -> None:
        data = load_json(SCHEDULES_FILE, {})
        data[guild_key(guild_id)] = schedules
        save_json(SCHEDULES_FILE, data)

    def get_watches(self, guild_id: int) -> dict:
        data = load_json(WATCHES_FILE, {})
        return data.setdefault(guild_key(guild_id), {})

    def save_watches(self, guild_id: int, watches: dict) -> None:
        data = load_json(WATCHES_FILE, {})
        data[guild_key(guild_id)] = watches
        save_json(WATCHES_FILE, data)

    def get_state(self) -> dict:
        return load_json(STATE_FILE, {})

    def save_state(self, state: dict) -> None:
        save_json(STATE_FILE, state)

    # ---------- checking ----------
    async def sleep_stop(self, seconds: float) -> bool:
        remaining = float(seconds)
        while remaining > 0:
            if self.stop_requested:
                return True
            step = min(0.5, remaining)
            await asyncio.sleep(step)
            remaining -= step
        return self.stop_requested

    async def safe_fetch_invite(self, code: str, backoff_seconds: float = DEFAULT_BACKOFF_SECONDS):
        for attempt in range(1, 3):
            if self.stop_requested:
                return "stopped", None
            try:
                invite = await self.bot.fetch_invite(code)
                return "valid", invite
            except discord.NotFound:
                return "invalid", None
            except discord.Forbidden as e:
                return "fatal_error", f"Forbidden: {e}"
            except discord.HTTPException as e:
                if attempt < 2:
                    stopped = await self.sleep_stop(backoff_seconds * attempt)
                    if stopped:
                        return "stopped", None
                    continue
                return "temporary_error", str(e)
            except Exception as e:
                if attempt < 2:
                    stopped = await self.sleep_stop(backoff_seconds * attempt)
                    if stopped:
                        return "stopped", None
                    continue
                return "temporary_error", f"{type(e).__name__}: {e}"
        return "temporary_error", "Unknown error"

    async def run_vanity_check(
        self,
        *,
        guild: discord.Guild,
        label: str,
        codes: List[str],
        valid_channel_id: Optional[int] = None,
        invalid_channel_id: Optional[int] = None,
        result_channel_id: Optional[int] = None,
        ping_role_ids: Optional[List[int]] = None,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        max_codes: int = MAX_CODES_PER_AUTOCHECK,
        started_by: Optional[int] = None,
        status_message: Optional[discord.Message] = None,
        alert_only_recent: bool = False,
    ) -> dict:
        ping_role_ids = ping_role_ids or []
        codes = codes[:max_codes]
        valid_channel_id = valid_channel_id or result_channel_id
        invalid_channel_id = invalid_channel_id or result_channel_id or valid_channel_id
        valid_channel = self.bot.get_channel(int(valid_channel_id)) or await self.bot.fetch_channel(int(valid_channel_id))
        invalid_channel = self.bot.get_channel(int(invalid_channel_id)) or await self.bot.fetch_channel(int(invalid_channel_id))
        before_invalid = {length: set(values) for length, values in self.invalid_cache.items()}

        stats = {
            "label": label,
            "processed": 0,
            "valid": [],
            "invalid": [],
            "recent_invalid": [],
            "became_valid": [],
            "errors": [],
            "started_at": now_ts(),
            "ended_at": None,
            "stopped": False,
        }

        self.running_label = label
        affected_lengths = set()

        try:
            for idx, code in enumerate(codes, start=1):
                if self.stop_requested:
                    stats["stopped"] = True
                    break

                result, payload = await self.safe_fetch_invite(code, backoff_seconds)
                stats["processed"] = idx
                length = len(code)
                affected_lengths.add(length)

                if result == "valid":
                    stats["valid"].append(code)
                    if code in self.invalid_cache[length]:
                        self.invalid_cache[length].remove(code)
                        stats["became_valid"].append(code)
                elif result == "invalid":
                    stats["invalid"].append(code)
                    if code not in before_invalid.get(length, set()):
                        stats["recent_invalid"].append(code)
                    self.invalid_cache[length].add(code)
                elif result == "stopped":
                    stats["stopped"] = True
                    break
                else:
                    stats["errors"].append(f"{code}: {payload}")

                if status_message and (idx == 1 or idx % 10 == 0 or idx == len(codes)):
                    try:
                        await status_message.edit(content=f"Checking `{label}`: `{idx}/{len(codes)}` processed • valid `{len(stats['valid'])}` • invalid `{len(stats['invalid'])}` • recent `{len(stats['recent_invalid'])}` • errors `{len(stats['errors'])}`")
                    except Exception:
                        pass

                if idx < len(codes):
                    stopped = await self.sleep_stop(delay_seconds)
                    if stopped:
                        stats["stopped"] = True
                        break
        finally:
            for length in affected_lengths:
                if length in TRACKED_LENGTHS:
                    self.rewrite_invalid_file(length)
            stats["ended_at"] = now_ts()
            self.running_label = None

        await self.send_check_summary(guild, valid_channel, invalid_channel, stats, ping_role_ids, alert_only_recent=alert_only_recent)
        return stats

    async def send_check_summary(
        self,
        guild: discord.Guild,
        valid_channel: discord.abc.Messageable,
        invalid_channel: discord.abc.Messageable,
        stats: dict,
        ping_role_ids: List[int],
        alert_only_recent: bool = False,
    ):
        if alert_only_recent and not stats.get("recent_invalid") and not stats.get("became_valid") and not stats.get("errors"):
            return
        title = "Vanity Check Stopped" if stats.get("stopped") else ("Vanity Watch Alert" if alert_only_recent else "Vanity Check Finished")
        base_desc = (
            f"**List:** `{stats['label']}`\n"
            f"**Processed:** `{stats['processed']}`\n"
            f"**Valid:** `{len(stats['valid'])}` • **Invalid:** `{len(stats['invalid'])}` • **Errors:** `{len(stats['errors'])}`\n"
            f"**Recently Became Invalid:** `{len(stats['recent_invalid'])}`\n"
            f"**Became Valid Again:** `{len(stats['became_valid'])}`\n"
            f"**Runtime:** `{max(0, int(stats['ended_at'] - stats['started_at']))}s`"
        )

        valid_embed = make_embed(f"✅ Valid Vanity Results — {title}", base_desc, c=GREEN if not stats.get("stopped") else GOLD)
        valid_lines = [f"`discord.gg/{x}`" for x in stats["valid"][:MAX_EMBED_LINES]]
        became_valid_lines = [f"`discord.gg/{x}`" for x in stats["became_valid"][:MAX_EMBED_LINES]]
        valid_embed.add_field(name="Currently Valid", value=chunk_text(valid_lines), inline=False)
        if stats["became_valid"]:
            valid_embed.add_field(name="Removed From Invalid File", value=chunk_text(became_valid_lines), inline=False)

        invalid_embed = make_embed(f"🔥 Invalid / Target Results — {title}", base_desc, c=RED if stats["recent_invalid"] else PURPLE)
        recent_lines = [f"`discord.gg/{x}`" for x in stats["recent_invalid"][:MAX_EMBED_LINES]]
        invalid_lines = [f"`discord.gg/{x}`" for x in stats["invalid"][:MAX_EMBED_LINES]]
        invalid_embed.add_field(name="Recent Invalids / Potential Targets", value=chunk_text(recent_lines), inline=False)
        invalid_embed.add_field(name="All Invalids From This Run", value=chunk_text(invalid_lines), inline=False)
        if stats["errors"]:
            invalid_embed.add_field(name="Errors", value=chunk_text(stats["errors"][:10]), inline=False)

        content = mention_roles(guild, ping_role_ids)
        allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)
        try:
            await valid_channel.send(embed=valid_embed)
        except Exception:
            pass
        try:
            await invalid_channel.send(content=content or None, embed=invalid_embed, allowed_mentions=allowed)
        except Exception:
            pass

    # =========================
    # VANITY MANAGER ACCESS COMMANDS
    # =========================
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="vanity_manager_add_user", description="Allow a user to use vanity slash commands.")
    async def vanity_manager_add_user(self, interaction: discord.Interaction, user: discord.Member):
        if not await require_admin(interaction):
            return
        cfg = get_vanity_config(interaction.guild.id)
        users = [str(x) for x in cfg.get("manager_users", [])]
        if str(user.id) not in users:
            users.append(str(user.id))
        cfg["manager_users"] = users
        save_vanity_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Added {user.mention} as a vanity manager.", ephemeral=True)

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="vanity_manager_remove_user", description="Remove a user's vanity slash command access.")
    async def vanity_manager_remove_user(self, interaction: discord.Interaction, user: discord.Member):
        if not await require_admin(interaction):
            return
        cfg = get_vanity_config(interaction.guild.id)
        cfg["manager_users"] = [str(x) for x in cfg.get("manager_users", []) if str(x) != str(user.id)]
        save_vanity_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Removed {user.mention} from vanity managers.", ephemeral=True)

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="vanity_manager_add_role", description="Allow a role to use vanity slash commands.")
    async def vanity_manager_add_role(self, interaction: discord.Interaction, role: discord.Role):
        if not await require_admin(interaction):
            return
        cfg = get_vanity_config(interaction.guild.id)
        roles = [str(x) for x in cfg.get("manager_roles", [])]
        if str(role.id) not in roles:
            roles.append(str(role.id))
        cfg["manager_roles"] = roles
        save_vanity_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Added {role.mention} as a vanity manager role.", ephemeral=True)

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="vanity_manager_remove_role", description="Remove a role's vanity slash command access.")
    async def vanity_manager_remove_role(self, interaction: discord.Interaction, role: discord.Role):
        if not await require_admin(interaction):
            return
        cfg = get_vanity_config(interaction.guild.id)
        cfg["manager_roles"] = [str(x) for x in cfg.get("manager_roles", []) if str(x) != str(role.id)]
        save_vanity_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Removed {role.mention} from vanity manager roles.", ephemeral=True)

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(name="vanity_managers", description="Show who can use vanity slash commands.")
    async def vanity_managers(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        cfg = get_vanity_config(interaction.guild.id)
        e = make_embed("Vanity Managers", c=PURPLE)
        users = cfg.get("manager_users", [])
        roles = cfg.get("manager_roles", [])
        e.add_field(name="Users", value="\n".join(f"<@{x}>" for x in users) or "None", inline=False)
        e.add_field(name="Roles", value="\n".join(f"<@&{x}>" for x in roles) or "None", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # =========================
    # VANITY CHECK SLASH COMMANDS
    # =========================
    @app_commands.command(name="vanity_check", description="Check comma-separated vanities and post results to channel IDs.")
    @app_commands.describe(codes="Comma/newline separated vanities or invite links", valid_channel_id="Channel ID for valid results", invalid_channel_id="Channel ID for invalid/recent target results", ping_roles="Optional role IDs/mentions to ping in the invalid channel", delay_seconds="Delay between checks. Default 3 seconds.")
    async def vanity_check(self, interaction: discord.Interaction, codes: str, valid_channel_id: str, invalid_channel_id: str, ping_roles: Optional[str] = None, delay_seconds: Optional[float] = 3.0):
        if not await require_vanity_access(interaction):
            return
        if self.check_lock.locked():
            return await interaction.response.send_message(f"A vanity check is already running: `{self.running_label}`. Use `/vanity_stop` first.", ephemeral=True)
        cleaned = parse_codes(codes)
        valid_id = parse_snowflake(valid_channel_id)
        invalid_id = parse_snowflake(invalid_channel_id)
        if not valid_id or not invalid_id:
            return await interaction.response.send_message("Paste valid channel IDs for both `valid_channel_id` and `invalid_channel_id`.", ephemeral=True)
        if not cleaned:
            return await interaction.response.send_message("No usable vanity codes found.", ephemeral=True)
        if len(cleaned) > MAX_CODES_PER_MANUAL_CHECK:
            return await interaction.response.send_message(f"Too many codes. Max is `{MAX_CODES_PER_MANUAL_CHECK}` per manual check.", ephemeral=True)
        await interaction.response.send_message(f"Started checking `{len(cleaned)}` vanities. Valid: <#{valid_id}> • Invalid: <#{invalid_id}>.", ephemeral=True)
        msg = await interaction.followup.send(f"Checking manual list: `0/{len(cleaned)}`", ephemeral=True, wait=True)
        async with self.check_lock:
            self.stop_requested = False
            await self.run_vanity_check(guild=interaction.guild, label="manual", codes=cleaned, valid_channel_id=valid_id, invalid_channel_id=invalid_id, ping_role_ids=parse_role_ids(ping_roles), delay_seconds=max(1.0, float(delay_seconds or 3.0)), started_by=interaction.user.id, status_message=msg)
            self.stop_requested = False

    @app_commands.command(name="vanity_stop", description="Safely stop the current vanity check.")
    async def vanity_stop(self, interaction: discord.Interaction):
        if not await require_vanity_access(interaction):
            return
        if not self.check_lock.locked():
            return await interaction.response.send_message("No vanity check is running right now.", ephemeral=True)
        self.stop_requested = True
        await interaction.response.send_message("Stop requested. The check will stop after the current step.", ephemeral=True)

    @app_commands.command(name="vanity_list_add", description="Create/update a saved vanity list.")
    @app_commands.describe(name="List name", codes="Comma/newline separated vanities or invite links")
    async def vanity_list_add(self, interaction: discord.Interaction, name: str, codes: str):
        if not await require_vanity_access(interaction):
            return
        name = name.strip().lower().replace(" ", "-")[:40]
        cleaned = parse_codes(codes)
        if not name or not cleaned:
            return await interaction.response.send_message("Use a list name and at least one vanity.", ephemeral=True)
        lists = self.get_lists(interaction.guild.id)
        existing = lists.get(name, [])
        merged, seen = [], set()
        for code in existing + cleaned:
            if code not in seen:
                merged.append(code)
                seen.add(code)
        lists[name] = merged
        self.save_lists(interaction.guild.id, lists)
        await interaction.response.send_message(f"Saved list `{name}` with `{len(merged)}` total codes. Added `{len(cleaned)}` from this command.", ephemeral=True)

    @app_commands.command(name="vanity_list_remove", description="Delete a saved vanity list.")
    async def vanity_list_remove(self, interaction: discord.Interaction, name: str):
        if not await require_vanity_access(interaction):
            return
        lists = self.get_lists(interaction.guild.id)
        name = name.strip().lower().replace(" ", "-")[:40]
        if name not in lists:
            return await interaction.response.send_message("That list does not exist.", ephemeral=True)
        lists.pop(name, None)
        self.save_lists(interaction.guild.id, lists)
        await interaction.response.send_message(f"Deleted list `{name}`.", ephemeral=True)

    @app_commands.command(name="vanity_lists", description="Show saved vanity lists.")
    async def vanity_lists(self, interaction: discord.Interaction):
        if not await require_vanity_access(interaction):
            return
        lists = self.get_lists(interaction.guild.id)
        e = make_embed("Saved Vanity Lists", c=PURPLE)
        if not lists:
            e.description = "No saved lists yet. Use `/vanity_list_add`."
        else:
            e.description = "\n".join(f"`{name}` — `{len(codes)}` codes" for name, codes in sorted(lists.items()))[:3900]
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="vanity_autocheck_add", description="Auto-check a saved list every X minutes.")
    @app_commands.describe(list_name="Saved list name", interval_minutes="How often to check", valid_channel_id="Channel ID for valid results", invalid_channel_id="Channel ID for invalid/recent target results", ping_roles="Optional role IDs/mentions to ping in the invalid channel", delay_seconds="Delay between each vanity check")
    async def vanity_autocheck_add(self, interaction: discord.Interaction, list_name: str, interval_minutes: app_commands.Range[int, 5, 10080], valid_channel_id: str, invalid_channel_id: str, ping_roles: Optional[str] = None, delay_seconds: Optional[float] = 3.0):
        if not await require_vanity_access(interaction):
            return
        list_name = list_name.strip().lower().replace(" ", "-")[:40]
        valid_id = parse_snowflake(valid_channel_id)
        invalid_id = parse_snowflake(invalid_channel_id)
        if not valid_id or not invalid_id:
            return await interaction.response.send_message("Paste valid channel IDs for both `valid_channel_id` and `invalid_channel_id`.", ephemeral=True)
        lists = self.get_lists(interaction.guild.id)
        if list_name not in lists:
            return await interaction.response.send_message("That saved list does not exist. Use `/vanity_list_add` first.", ephemeral=True)
        schedules = self.get_schedules(interaction.guild.id)
        schedules[list_name] = {
            "list_name": list_name,
            "interval_minutes": int(interval_minutes),
            "valid_channel_id": valid_id,
            "invalid_channel_id": invalid_id,
            "ping_role_ids": parse_role_ids(ping_roles),
            "delay_seconds": max(1.0, float(delay_seconds or 3.0)),
            "enabled": True,
            "last_run": 0,
            "next_run": now_ts() + int(interval_minutes) * 60,
            "created_by": interaction.user.id,
        }
        self.save_schedules(interaction.guild.id, schedules)
        await interaction.response.send_message(f"Auto-check enabled for `{list_name}` every `{interval_minutes}` minutes. Valid: <#{valid_id}> • Invalid: <#{invalid_id}>.", ephemeral=True)

    @app_commands.command(name="vanity_autocheck_remove", description="Remove an auto-check schedule.")
    async def vanity_autocheck_remove(self, interaction: discord.Interaction, list_name: str):
        if not await require_vanity_access(interaction):
            return
        list_name = list_name.strip().lower().replace(" ", "-")[:40]
        schedules = self.get_schedules(interaction.guild.id)
        if list_name not in schedules:
            return await interaction.response.send_message("No schedule found for that list.", ephemeral=True)
        schedules.pop(list_name, None)
        self.save_schedules(interaction.guild.id, schedules)
        await interaction.response.send_message(f"Removed auto-check schedule for `{list_name}`.", ephemeral=True)

    @app_commands.command(name="vanity_autochecks", description="Show auto-check schedules.")
    async def vanity_autochecks(self, interaction: discord.Interaction):
        if not await require_vanity_access(interaction):
            return
        schedules = self.get_schedules(interaction.guild.id)
        e = make_embed("Vanity Auto-Checks", c=PURPLE)
        if not schedules:
            e.description = "No auto-checks yet. Use `/vanity_autocheck_add`."
        else:
            lines = []
            for name, s in sorted(schedules.items()):
                lines.append(f"`{name}` — every `{s['interval_minutes']}m` • next <t:{int(s.get('next_run', 0))}:R> • valid <#{s.get('valid_channel_id', s.get('result_channel_id', 0))}> • invalid <#{s.get('invalid_channel_id', s.get('result_channel_id', 0))}>")
            e.description = "\n".join(lines)[:3900]
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="vanity_invalid_file", description="Send the invalid txt file for a specific vanity length.")
    async def vanity_invalid_file(self, interaction: discord.Interaction, length: app_commands.Range[int, 1, 32]):
        if not await require_vanity_access(interaction):
            return
        self.load_invalid_cache()
        self.rewrite_invalid_file(int(length))
        path = self.invalid_path(int(length))
        await interaction.response.send_message(content=f"Invalid file for `{length}` letters. Entries: `{len(self.invalid_cache[int(length)])}`", file=discord.File(str(path), filename=path.name), ephemeral=True)


    @app_commands.command(name="vanity_watch_add", description="Fast watch: check a list often and only alert when something changes.")
    @app_commands.describe(list_name="Saved list name", interval_minutes="How often to check. Keep this reasonable to avoid rate limits.", valid_channel_id="Channel ID for became-valid alerts", invalid_channel_id="Channel ID for recent-invalid alerts", ping_roles="Optional role IDs/mentions to ping on alerts", delay_seconds="Delay between each vanity check")
    async def vanity_watch_add(self, interaction: discord.Interaction, list_name: str, interval_minutes: app_commands.Range[int, 2, 10080], valid_channel_id: str, invalid_channel_id: str, ping_roles: Optional[str] = None, delay_seconds: Optional[float] = 3.0):
        if not await require_vanity_access(interaction):
            return
        list_name = list_name.strip().lower().replace(" ", "-")[:40]
        lists = self.get_lists(interaction.guild.id)
        if list_name not in lists:
            return await interaction.response.send_message("That saved list does not exist. Use `/vanity_list_add` first.", ephemeral=True)
        valid_id = parse_snowflake(valid_channel_id)
        invalid_id = parse_snowflake(invalid_channel_id)
        if not valid_id or not invalid_id:
            return await interaction.response.send_message("Paste valid channel IDs for both channel options.", ephemeral=True)
        watches = self.get_watches(interaction.guild.id)
        watches[list_name] = {"list_name": list_name, "interval_minutes": int(interval_minutes), "valid_channel_id": valid_id, "invalid_channel_id": invalid_id, "ping_role_ids": parse_role_ids(ping_roles), "delay_seconds": max(1.0, float(delay_seconds or 3.0)), "enabled": True, "last_run": 0, "next_run": now_ts() + int(interval_minutes) * 60, "created_by": interaction.user.id}
        self.save_watches(interaction.guild.id, watches)
        await interaction.response.send_message(f"Fast watch enabled for `{list_name}` every `{interval_minutes}` minutes. It will only post when something changes.", ephemeral=True)

    @app_commands.command(name="vanity_watch_remove", description="Remove a fast watch.")
    async def vanity_watch_remove(self, interaction: discord.Interaction, list_name: str):
        if not await require_vanity_access(interaction):
            return
        list_name = list_name.strip().lower().replace(" ", "-")[:40]
        watches = self.get_watches(interaction.guild.id)
        if list_name not in watches:
            return await interaction.response.send_message("No fast watch found for that list.", ephemeral=True)
        watches.pop(list_name, None)
        self.save_watches(interaction.guild.id, watches)
        await interaction.response.send_message(f"Removed fast watch for `{list_name}`.", ephemeral=True)

    @app_commands.command(name="vanity_watches", description="Show fast watch schedules.")
    async def vanity_watches(self, interaction: discord.Interaction):
        if not await require_vanity_access(interaction):
            return
        watches = self.get_watches(interaction.guild.id)
        e = make_embed("Vanity Fast Watches", c=PURPLE)
        if not watches:
            e.description = "No fast watches yet. Use `/vanity_watch_add`."
        else:
            e.description = "\n".join(f"`{name}` — every `{s['interval_minutes']}m` • next <t:{int(s.get('next_run', 0))}:R> • valid <#{s.get('valid_channel_id', 0)}> • invalid <#{s.get('invalid_channel_id', 0)}>" for name, s in sorted(watches.items()))[:3900]
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="vanity_priority", description="Create a smart priority list from another saved list.")
    @app_commands.describe(source_list="Saved list to rank", output_list="New/updated priority list name", limit="How many top words to keep")
    async def vanity_priority(self, interaction: discord.Interaction, source_list: str, output_list: str = "priority", limit: app_commands.Range[int, 5, 500] = 100):
        if not await require_vanity_access(interaction):
            return
        source_list = source_list.strip().lower().replace(" ", "-")[:40]
        output_list = output_list.strip().lower().replace(" ", "-")[:40]
        lists = self.get_lists(interaction.guild.id)
        if source_list not in lists:
            return await interaction.response.send_message("Source list not found.", ephemeral=True)
        ranked = sorted(lists[source_list], key=lambda c: vanity_score(c, c in self.invalid_cache[len(c)]), reverse=True)[:int(limit)]
        lists[output_list] = ranked
        self.save_lists(interaction.guild.id, lists)
        preview = [f"`{c}` — score `{vanity_score(c, c in self.invalid_cache[len(c)])}`" for c in ranked[:20]]
        e = make_embed("Smart Priority List Created", c=GOLD)
        e.description = f"**Source:** `{source_list}`\n**Output:** `{output_list}`\n**Saved:** `{len(ranked)}` codes"
        e.add_field(name="Top Preview", value=chunk_text(preview), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # =========================
    # HUNTER SYSTEM SLASH COMMANDS
    # =========================
    @app_commands.command(name="vh_session_start", description="Start a timed vanity hunting session.")
    @app_commands.describe(name="Session name", list_name="Saved list to assign from", duration_minutes="Session length", channel="Channel to announce in", ping_roles="Optional role IDs/mentions to ping", hunters="Optional hunter mentions/IDs to auto-assign", words_per_hunter="Words each hunter gets if hunters are provided")
    async def vh_session_start(self, interaction: discord.Interaction, name: str, list_name: str, duration_minutes: app_commands.Range[int, 5, 240], channel: discord.TextChannel, ping_roles: Optional[str] = None, hunters: Optional[str] = None, words_per_hunter: app_commands.Range[int, 1, 50] = 5):
        if not await require_vanity_access(interaction):
            return
        lists = self.get_lists(interaction.guild.id)
        list_name = list_name.strip().lower().replace(" ", "-")[:40]
        if list_name not in lists:
            return await interaction.response.send_message("That saved list does not exist.", ephemeral=True)
        sessions = load_json(SESSIONS_FILE, {})
        gid = guild_key(interaction.guild.id)
        sessions.setdefault(gid, {})
        session_id = str(int(time.time() * 1000))
        session_obj = {
            "id": session_id,
            "name": name[:80],
            "list_name": list_name,
            "guild_id": interaction.guild.id,
            "channel_id": channel.id,
            "created_by": interaction.user.id,
            "created_at": now_ts(),
            "ends_at": now_ts() + int(duration_minutes) * 60,
            "status": "active",
            "assignments": {},
        }
        hunter_ids = [int(x) for x in re.findall(r"\d{15,25}", hunters or "")]
        if hunter_ids:
            codes = sorted(lists[list_name], key=lambda c: vanity_score(c, c in self.invalid_cache[len(c)]), reverse=True)
            cursor = 0
            for uid in hunter_ids:
                session_obj["assignments"][str(uid)] = codes[cursor:cursor + int(words_per_hunter)]
                cursor += int(words_per_hunter)
        sessions[gid][session_id] = session_obj
        save_json(SESSIONS_FILE, sessions)
        e = make_embed("🔥 Vanity Hunting Session Started", c=GOLD)
        e.description = f"**Session:** `{name}`\n**ID:** `{session_id}`\n**List:** `{list_name}`\n**Ends:** <t:{sessions[gid][session_id]['ends_at']}:R>\n\nUse `/vh_assign` or `/vh_autoassign` to split words between hunters."
        if session_obj["assignments"]:
            e.description += "\n\n**Auto-assignments created:**"
            for uid, words in session_obj["assignments"].items():
                e.add_field(name=f"<@{uid}>", value=", ".join(f"`{w}`" for w in words) or "None", inline=False)
        content = mention_roles(interaction.guild, parse_role_ids(ping_roles))
        await channel.send(content=content or None, embed=e, allowed_mentions=discord.AllowedMentions(roles=True))
        await interaction.response.send_message(f"Session `{session_id}` started in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="vh_assign", description="Assign chunks of a session list to hunters.")
    @app_commands.describe(session_id="Session ID", hunters="Mention/user IDs separated by spaces", words_per_hunter="How many words each hunter gets")
    async def vh_assign(self, interaction: discord.Interaction, session_id: str, hunters: str, words_per_hunter: app_commands.Range[int, 1, 50] = 5):
        if not await require_vanity_access(interaction):
            return
        ids = [int(x) for x in re.findall(r"\d{15,25}", hunters)]
        if not ids:
            return await interaction.response.send_message("Paste at least one hunter mention or user ID.", ephemeral=True)
        sessions = load_json(SESSIONS_FILE, {})
        gid = guild_key(interaction.guild.id)
        session = sessions.get(gid, {}).get(session_id)
        if not session:
            return await interaction.response.send_message("Session not found.", ephemeral=True)
        lists = self.get_lists(interaction.guild.id)
        codes = list(lists.get(session["list_name"], []))
        random.shuffle(codes)
        assignments = {}
        cursor = 0
        for uid in ids:
            assignments[str(uid)] = codes[cursor:cursor + int(words_per_hunter)]
            cursor += int(words_per_hunter)
        session["assignments"] = assignments
        sessions[gid][session_id] = session
        save_json(SESSIONS_FILE, sessions)

        e = make_embed("Vanity Assignments", c=PURPLE)
        e.description = f"**Session:** `{session_id}`\nOnly try your assigned words to avoid overlap."
        for uid, words in assignments.items():
            e.add_field(name=f"<@{uid}>", value=", ".join(f"`{w}`" for w in words) or "None", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="vh_attempt", description="Hunter: log attempted words for a session.")
    @app_commands.describe(session_id="Session ID", words="Words you attempted", cooldown="Did you hit a cooldown?", note="Optional note")
    async def vh_attempt(self, interaction: discord.Interaction, session_id: str, words: str, cooldown: bool = False, note: Optional[str] = None):
        if not interaction.guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        cleaned = parse_codes(words)
        if not cleaned:
            return await interaction.response.send_message("No usable words found.", ephemeral=True)
        attempts = load_json(ATTEMPTS_FILE, {})
        gid = guild_key(interaction.guild.id)
        attempts.setdefault(gid, [])
        attempts[gid].append({
            "session_id": session_id,
            "user_id": interaction.user.id,
            "words": cleaned,
            "cooldown": bool(cooldown),
            "note": note or "",
            "created_at": now_ts(),
        })
        save_json(ATTEMPTS_FILE, attempts)
        await interaction.response.send_message(f"Logged `{len(cleaned)}` attempt(s). Cooldown: `{cooldown}`.", ephemeral=True)

    @app_commands.command(name="vh_claim", description="Log a successful vanity claim.")
    @app_commands.describe(session_id="Session ID", vanity="Vanity claimed", claimed_by="Who claimed it", estimated_value="Optional estimated sale value")
    async def vh_claim(self, interaction: discord.Interaction, session_id: str, vanity: str, claimed_by: Optional[discord.Member] = None, estimated_value: Optional[str] = None):
        if not await require_vanity_access(interaction):
            return
        claimed_by = claimed_by or interaction.user
        code = normalize_code(vanity)
        claims = load_json(CLAIMS_FILE, {})
        gid = guild_key(interaction.guild.id)
        claims.setdefault(gid, [])
        claims[gid].append({
            "session_id": session_id,
            "vanity": code,
            "claimed_by": claimed_by.id,
            "logged_by": interaction.user.id,
            "estimated_value": estimated_value or "",
            "created_at": now_ts(),
        })
        save_json(CLAIMS_FILE, claims)
        e = make_embed("🏆 Vanity Claimed", c=GOLD)
        e.description = f"**Vanity:** `discord.gg/{code}`\n**Claimed by:** {claimed_by.mention}\n**Session:** `{session_id}`"
        if estimated_value:
            e.add_field(name="Estimated Value", value=estimated_value, inline=True)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="vh_leaderboard", description="Show vanity hunter leaderboard.")
    async def vh_leaderboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        gid = guild_key(interaction.guild.id)
        attempts = load_json(ATTEMPTS_FILE, {}).get(gid, [])
        claims = load_json(CLAIMS_FILE, {}).get(gid, [])
        stats: Dict[str, dict] = {}
        for a in attempts:
            uid = str(a["user_id"])
            s = stats.setdefault(uid, {"attempts": 0, "sessions": set(), "claims": 0})
            s["attempts"] += len(a.get("words", []))
            s["sessions"].add(a.get("session_id"))
        for c in claims:
            uid = str(c["claimed_by"])
            s = stats.setdefault(uid, {"attempts": 0, "sessions": set(), "claims": 0})
            s["claims"] += 1
        ranked = sorted(stats.items(), key=lambda kv: (kv[1]["claims"], kv[1]["attempts"], len(kv[1]["sessions"])), reverse=True)[:10]
        e = make_embed("🏆 Vanity Hunter Leaderboard", c=GOLD)
        if not ranked:
            e.description = "No hunter stats yet."
        else:
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, (uid, s) in enumerate(ranked, 1):
                prefix = medals[i - 1] if i <= 3 else f"`{i}.`"
                lines.append(f"{prefix} <@{uid}> — `{s['claims']}` claims • `{s['attempts']}` attempts • `{len(s['sessions'])}` sessions")
            e.description = "\n".join(lines)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="vh_stats", description="Show hunter stats for yourself or another user.")
    async def vh_stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        if not interaction.guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        user = user or interaction.user
        gid = guild_key(interaction.guild.id)
        attempts = [a for a in load_json(ATTEMPTS_FILE, {}).get(gid, []) if int(a.get("user_id", 0)) == user.id]
        claims = [c for c in load_json(CLAIMS_FILE, {}).get(gid, []) if int(c.get("claimed_by", 0)) == user.id]
        sessions = {a.get("session_id") for a in attempts}
        attempted_words = sum(len(a.get("words", [])) for a in attempts)
        cooldowns = sum(1 for a in attempts if a.get("cooldown"))
        e = make_embed(f"Hunter Stats — {user.display_name}", c=PURPLE)
        e.description = f"**Claims:** `{len(claims)}`\n**Attempts Logged:** `{attempted_words}`\n**Sessions:** `{len(sessions)}`\n**Cooldowns Reported:** `{cooldowns}`"
        if claims:
            e.add_field(name="Recent Claims", value=chunk_text([f"`discord.gg/{c['vanity']}` • <t:{int(c['created_at'])}:R>" for c in claims[-10:]][::-1]), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


    @app_commands.command(name="vh_sale_log", description="Log a vanity sale/profit for hunter payouts.")
    @app_commands.describe(vanity="Sold vanity", sale_amount="Sale amount, example: 100 or $100", claimed_by="Hunter who claimed it", payout_percent="Hunter payout percent", note="Optional note")
    async def vh_sale_log(self, interaction: discord.Interaction, vanity: str, sale_amount: str, claimed_by: discord.Member, payout_percent: app_commands.Range[float, 0, 100] = 30.0, note: Optional[str] = None):
        if not await require_vanity_access(interaction):
            return
        amount = parse_money(sale_amount)
        if amount <= 0:
            return await interaction.response.send_message("Enter a valid sale amount like `100` or `$100`.", ephemeral=True)
        code = normalize_code(vanity)
        payout = round(amount * (float(payout_percent) / 100), 2)
        profit = round(amount - payout, 2)
        sales = load_json(SALES_FILE, {})
        gid = guild_key(interaction.guild.id)
        sales.setdefault(gid, [])
        sales[gid].append({"vanity": code, "sale_amount": amount, "claimed_by": claimed_by.id, "payout_percent": float(payout_percent), "hunter_payout": payout, "owner_profit": profit, "note": note or "", "logged_by": interaction.user.id, "created_at": now_ts()})
        save_json(SALES_FILE, sales)
        e = make_embed("💰 Vanity Sale Logged", c=GOLD)
        e.description = f"**Vanity:** `discord.gg/{code}`\n**Sale:** `${amount:,.2f}`\n**Hunter:** {claimed_by.mention}\n**Hunter Payout:** `${payout:,.2f}` (`{payout_percent}%`)\n**Owner Profit:** `${profit:,.2f}`"
        if note:
            e.add_field(name="Note", value=note[:1000], inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="vh_profits", description="Show profit and payout totals by hunter.")
    async def vh_profits(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        if not await require_vanity_access(interaction):
            return
        gid = guild_key(interaction.guild.id)
        rows = load_json(SALES_FILE, {}).get(gid, [])
        if user:
            rows = [r for r in rows if int(r.get("claimed_by", 0)) == user.id]
        totals: Dict[str, dict] = {}
        for r in rows:
            uid = str(r.get("claimed_by"))
            t = totals.setdefault(uid, {"sales": 0, "revenue": 0.0, "payouts": 0.0, "profit": 0.0})
            t["sales"] += 1
            t["revenue"] += float(r.get("sale_amount", 0))
            t["payouts"] += float(r.get("hunter_payout", 0))
            t["profit"] += float(r.get("owner_profit", 0))
        e = make_embed("💰 Vanity Profit Tracker", c=GOLD)
        if not totals:
            e.description = "No sales logged yet. Use `/vh_sale_log`."
        else:
            ranked = sorted(totals.items(), key=lambda kv: kv[1]["revenue"], reverse=True)[:15]
            e.description = "\n".join(f"<@{uid}> — `{t['sales']}` sales • revenue `${t['revenue']:,.2f}` • payouts `${t['payouts']:,.2f}` • profit `${t['profit']:,.2f}`" for uid, t in ranked)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="vh_autoassign", description="Smart-assign priority words from a session to hunters.")
    @app_commands.describe(session_id="Session ID", hunters="Hunter mentions/IDs", words_per_hunter="Words per hunter")
    async def vh_autoassign(self, interaction: discord.Interaction, session_id: str, hunters: str, words_per_hunter: app_commands.Range[int, 1, 50] = 5):
        if not await require_vanity_access(interaction):
            return
        ids = [int(x) for x in re.findall(r"\d{15,25}", hunters)]
        if not ids:
            return await interaction.response.send_message("Paste at least one hunter mention or user ID.", ephemeral=True)
        sessions = load_json(SESSIONS_FILE, {})
        gid = guild_key(interaction.guild.id)
        session = sessions.get(gid, {}).get(session_id)
        if not session:
            return await interaction.response.send_message("Session not found.", ephemeral=True)
        lists = self.get_lists(interaction.guild.id)
        codes = sorted(lists.get(session["list_name"], []), key=lambda c: vanity_score(c, c in self.invalid_cache[len(c)]), reverse=True)
        assignments = {}
        cursor = 0
        for uid in ids:
            assignments[str(uid)] = codes[cursor:cursor + int(words_per_hunter)]
            cursor += int(words_per_hunter)
        session["assignments"] = assignments
        sessions[gid][session_id] = session
        save_json(SESSIONS_FILE, sessions)
        e = make_embed("Smart Vanity Assignments", c=PURPLE)
        e.description = f"**Session:** `{session_id}`\nPriority words assigned first to reduce overlap."
        for uid, words in assignments.items():
            e.add_field(name=f"<@{uid}>", value=", ".join(f"`{w}`" for w in words) or "None", inline=False)
        await interaction.response.send_message(embed=e)

    # =========================
    # AUTO LOOP
    # =========================
    @tasks.loop(minutes=1)
    async def auto_check_loop(self):
        await self.bot.wait_until_ready()
        if self.check_lock.locked():
            return
        all_schedules = load_json(SCHEDULES_FILE, {})
        for gid, schedules in list(all_schedules.items()):
            guild = self.bot.get_guild(int(gid))
            if not guild:
                continue
            lists = self.get_lists(guild.id)
            changed = False
            for name, s in list(schedules.items()):
                if not s.get("enabled", True):
                    continue
                if now_ts() < int(s.get("next_run", 0)):
                    continue
                codes = lists.get(name, [])
                if not codes:
                    s["next_run"] = now_ts() + int(s.get("interval_minutes", 60)) * 60
                    changed = True
                    continue
                async with self.check_lock:
                    self.stop_requested = False
                    await self.run_vanity_check(
                        guild=guild,
                        label=f"auto:{name}",
                        codes=codes,
                        valid_channel_id=int(s.get("valid_channel_id", s.get("result_channel_id", 0))),
                        invalid_channel_id=int(s.get("invalid_channel_id", s.get("result_channel_id", 0))),
                        ping_role_ids=[int(x) for x in s.get("ping_role_ids", [])],
                        delay_seconds=float(s.get("delay_seconds", DEFAULT_DELAY_SECONDS)),
                        max_codes=MAX_CODES_PER_AUTOCHECK,
                    )
                    self.stop_requested = False
                s["last_run"] = now_ts()
                s["next_run"] = now_ts() + int(s.get("interval_minutes", 60)) * 60
                schedules[name] = s
                changed = True
            if changed:
                all_schedules[gid] = schedules
                save_json(SCHEDULES_FILE, all_schedules)

        all_watches = load_json(WATCHES_FILE, {})
        for gid, watches in list(all_watches.items()):
            if self.check_lock.locked():
                return
            guild = self.bot.get_guild(int(gid))
            if not guild:
                continue
            lists = self.get_lists(guild.id)
            changed = False
            for name, s in list(watches.items()):
                if not s.get("enabled", True) or now_ts() < int(s.get("next_run", 0)):
                    continue
                codes = lists.get(name, [])
                if not codes:
                    s["next_run"] = now_ts() + int(s.get("interval_minutes", 10)) * 60
                    changed = True
                    continue
                codes = sorted(codes, key=lambda c: vanity_score(c, c in self.invalid_cache[len(c)]), reverse=True)
                async with self.check_lock:
                    self.stop_requested = False
                    await self.run_vanity_check(guild=guild, label=f"watch:{name}", codes=codes, valid_channel_id=int(s.get("valid_channel_id", 0)), invalid_channel_id=int(s.get("invalid_channel_id", 0)), ping_role_ids=[int(x) for x in s.get("ping_role_ids", [])], delay_seconds=float(s.get("delay_seconds", DEFAULT_DELAY_SECONDS)), max_codes=MAX_CODES_PER_AUTOCHECK, alert_only_recent=True)
                    self.stop_requested = False
                s["last_run"] = now_ts()
                s["next_run"] = now_ts() + int(s.get("interval_minutes", 10)) * 60
                watches[name] = s
                changed = True
            if changed:
                all_watches[gid] = watches
                save_json(WATCHES_FILE, all_watches)

    @auto_check_loop.before_loop
    async def before_auto_check_loop(self):
        await self.bot.wait_until_ready()


async def setup_vanity_hunter(bot: commands.Bot):
    if bot.get_cog("VanityHunterCog") is None:
        await bot.add_cog(VanityHunterCog(bot))
