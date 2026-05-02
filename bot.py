
from __future__ import annotations

import os
import re
import json
import time
import asyncio
import logging
from pathlib import Path
from collections import defaultdict
from typing import Optional, List, Dict, Tuple

import discord
import aiohttp
from discord import app_commands
from discord.ext import commands, tasks

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TOKEN = os.getenv("TOKEN")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

CONFIG_FILE = DATA_DIR / "config.json"
CLAIMS_FILE = DATA_DIR / "claims.json"
PAYMENTS_FILE = DATA_DIR / "payments.json"
LISTS_FILE = DATA_DIR / "vanity_lists.json"
CHECKERS_FILE = DATA_DIR / "checker_setups.json"
CHECKER_STATE_FILE = DATA_DIR / "checker_state.json"
CHANGE_LOG_FILE = DATA_DIR / "checker_change_logs.json"
MANAGER_APPS_FILE = DATA_DIR / "manager_applications.json"

CLAIM_COOLDOWN_SECONDS = int(os.getenv("CLAIM_COOLDOWN_SECONDS", "60"))
MAX_CLAIMS_PER_HOUR = int(os.getenv("MAX_CLAIMS_PER_HOUR", "5"))
CHECK_DELAY_SECONDS = float(os.getenv("CHECK_DELAY_SECONDS", "8"))
CHECKER_BACKOFF_SECONDS = float(os.getenv("CHECKER_BACKOFF_SECONDS", "90"))
CHECKER_MESSAGE_DELAY_SECONDS = float(os.getenv("CHECKER_MESSAGE_DELAY_SECONDS", "1.2"))
CHECKER_MAX_RETRIES = int(os.getenv("CHECKER_MAX_RETRIES", "3"))
CHECKER_MAX_CODES_PER_RUN = int(os.getenv("CHECKER_MAX_CODES_PER_RUN", "2500"))
HUNTER_ROLE_ID = int(os.getenv("HUNTER_ROLE_ID", "0") or 0)
MANAGER_ROLE_ID = int(os.getenv("MANAGER_ROLE_ID", "0") or 0)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

DARK = discord.Color.from_rgb(34, 34, 42)
PURPLE = discord.Color.from_rgb(150, 95, 255)
GREEN = discord.Color.from_rgb(65, 185, 115)
RED = discord.Color.from_rgb(220, 75, 75)
GOLD = discord.Color.from_rgb(245, 185, 80)
BLUE = discord.Color.from_rgb(90, 150, 255)

checker_lock = asyncio.Lock()
logger = logging.getLogger("vanity_checker")
logging.basicConfig(level=logging.INFO)

checker_runtime = {"running": False, "stop_requested": False, "label": None, "total": 0, "current": 0}
invalid_cache = defaultdict(set)

# =========================================================
# STORAGE
# =========================================================
def ensure_data() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "exports").mkdir(parents=True, exist_ok=True)

def load_json(path: Path, default):
    ensure_data()
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: Path, data) -> None:
    ensure_data()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def gkey(guild_id: int) -> str:
    return str(guild_id)

def now_ts() -> int:
    return int(time.time())

def make_id() -> str:
    return str(int(time.time() * 1000))

def make_embed(title: str, desc: str = "", color: discord.Color = DARK) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.timestamp = discord.utils.utcnow()
    return e

def clean_code(text: str) -> str:
    text = str(text or "").strip().lower()
    for p in (
        "https://discord.gg/", "http://discord.gg/", "discord.gg/",
        "https://discord.com/invite/", "http://discord.com/invite/", "discord.com/invite/",
    ):
        text = text.replace(p, "")
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

def parse_snowflake(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    m = re.search(r"\d{15,25}", str(raw))
    return int(m.group(0)) if m else None

def money(value: float) -> str:
    return f"${float(value):,.2f}"

def short(text: str, limit: int = 1024) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 3] + "..."

# =========================================================
# DATA ACCESS
# =========================================================
def get_config(guild_id: int) -> dict:
    data = load_json(CONFIG_FILE, {})
    return data.setdefault(gkey(guild_id), {
        "manager_users": [],
        "manager_roles": [],
        "claim_log_channel_id": None,
        "pending_claim_channel_id": None,
        "claim_cooldown_seconds": CLAIM_COOLDOWN_SECONDS,
        "max_claims_per_hour": MAX_CLAIMS_PER_HOUR,
        "autoroles": [],
        "manager_application_channel_id": None,
        "manager_role_id": MANAGER_ROLE_ID or None,
        "leaderboard_channel_id": None,
        "leaderboard_message_id": None,
        "claim_ping_role_id": None,
    })

def save_config(guild_id: int, cfg: dict) -> None:
    data = load_json(CONFIG_FILE, {})
    data[gkey(guild_id)] = cfg
    save_json(CONFIG_FILE, data)

def get_claims(guild_id: int) -> List[dict]:
    data = load_json(CLAIMS_FILE, {})
    return data.setdefault(gkey(guild_id), [])

def save_claims(guild_id: int, claims: List[dict]) -> None:
    data = load_json(CLAIMS_FILE, {})
    data[gkey(guild_id)] = claims
    save_json(CLAIMS_FILE, data)

def get_payments(guild_id: int) -> Dict[str, dict]:
    data = load_json(PAYMENTS_FILE, {})
    return data.setdefault(gkey(guild_id), {})

def save_payments(guild_id: int, payments: Dict[str, dict]) -> None:
    data = load_json(PAYMENTS_FILE, {})
    data[gkey(guild_id)] = payments
    save_json(PAYMENTS_FILE, data)

def get_lists(guild_id: int) -> dict:
    data = load_json(LISTS_FILE, {})
    return data.setdefault(gkey(guild_id), {})

def save_lists(guild_id: int, lists: dict) -> None:
    data = load_json(LISTS_FILE, {})
    data[gkey(guild_id)] = lists
    save_json(LISTS_FILE, data)

def get_checkers(guild_id: int) -> dict:
    data = load_json(CHECKERS_FILE, {})
    return data.setdefault(gkey(guild_id), {})

def save_checkers(guild_id: int, checkers: dict) -> None:
    data = load_json(CHECKERS_FILE, {})
    data[gkey(guild_id)] = checkers
    save_json(CHECKERS_FILE, data)

def get_checker_state() -> dict:
    return load_json(CHECKER_STATE_FILE, {})

def save_checker_state(state: dict) -> None:
    save_json(CHECKER_STATE_FILE, state)

def get_change_logs(guild_id: int) -> List[dict]:
    data = load_json(CHANGE_LOG_FILE, {})
    return data.setdefault(gkey(guild_id), [])

def save_change_logs(guild_id: int, logs: List[dict]) -> None:
    data = load_json(CHANGE_LOG_FILE, {})
    data[gkey(guild_id)] = logs[-250:]
    save_json(CHANGE_LOG_FILE, data)


def get_manager_apps(guild_id: int) -> List[dict]:
    data = load_json(MANAGER_APPS_FILE, {})
    return data.setdefault(gkey(guild_id), [])

def save_manager_apps(guild_id: int, apps: List[dict]) -> None:
    data = load_json(MANAGER_APPS_FILE, {})
    data[gkey(guild_id)] = apps[-500:]
    save_json(MANAGER_APPS_FILE, data)

# =========================================================
# PERMISSIONS
# =========================================================
def is_adminish(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild

def is_manager(member: discord.Member) -> bool:
    if is_adminish(member):
        return True
    cfg = get_config(member.guild.id)
    if str(member.id) in {str(x) for x in cfg.get("manager_users", [])}:
        return True
    member_roles = {str(r.id) for r in member.roles}
    manager_roles = {str(x) for x in cfg.get("manager_roles", [])}
    return bool(member_roles & manager_roles)

async def require_manager(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not is_manager(interaction.user):
        await interaction.response.send_message("You need manager access for this.", ephemeral=True)
        return False
    return True

async def require_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not is_adminish(interaction.user):
        await interaction.response.send_message("You need Manage Server or Administrator.", ephemeral=True)
        return False
    return True

# =========================================================
# INVITE CHECKING
# =========================================================
async def invite_is_valid(code: str) -> Tuple[bool, str]:
    """
    Robust invite checker.

    Discord sometimes previews an invite in the client while the bot library/API check
    returns NotFound/Forbidden/HTTP errors. This function checks with discord.py first,
    then falls back to the public Discord invite API.

    Return values:
    - (True, "valid") = confirmed valid
    - (False, "not_found") = confirmed invalid/unknown invite
    - (True, "api_uncertain") = API was blocked/rate-limited/error, allow pending manager review
    """
    code = clean_code(code)
    if not code:
        return False, "empty"

    # First try discord.py's built-in invite fetcher.
    try:
        await bot.fetch_invite(code)
        return True, "valid"
    except discord.NotFound:
        pass
    except discord.Forbidden:
        # Forbidden usually means the invite exists but the bot/API cannot access full details.
        return True, "api_uncertain"
    except discord.HTTPException as e:
        # Rate limits/server errors should not hard-block real claims.
        if getattr(e, "status", None) in {403, 429, 500, 502, 503, 504}:
            return True, "api_uncertain"
    except Exception:
        pass

    # Fallback: public Discord invite endpoint.
    url = f"https://discord.com/api/v10/invites/{code}?with_counts=true&with_expiration=true"
    headers = {"User-Agent": "Mozilla/5.0 VanityHunterBot"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    return True, "valid"
                if resp.status == 404:
                    return False, "not_found"
                if resp.status in {403, 429, 500, 502, 503, 504}:
                    return True, "api_uncertain"
                return False, f"http_{resp.status}"
    except Exception:
        # If Discord's public API is unreachable, let managers review instead of blocking.
        return True, "api_uncertain"


async def checker_sleep(seconds: float, chunk: float = 0.5) -> bool:
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        if checker_runtime.get("stop_requested"):
            return True
        step = min(chunk, remaining)
        await asyncio.sleep(step)
        remaining -= step
    return bool(checker_runtime.get("stop_requested"))

def checker_state_path(guild_id: int, list_name: str, length: int) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", list_name)[:40] or "vanities"
    folder = DATA_DIR / "checker_invalid_files" / str(guild_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe}_{length}_available.txt"

def rewrite_available_files(guild_id: int, list_name: str, available: List[str]) -> None:
    by_length: Dict[int, List[str]] = {}
    for code in available:
        by_length.setdefault(len(code), []).append(code)
    for length in range(1, 33):
        path = checker_state_path(guild_id, list_name, length)
        codes = sorted(by_length.get(length, []))
        path.write_text("\\n".join(codes) + ("\\n" if codes else ""), encoding="utf-8")

async def safe_fetch_invite_for_checker(invite_code: str) -> Tuple[str, Optional[str]]:
    code = clean_code(invite_code)

    for attempt in range(1, CHECKER_MAX_RETRIES + 1):
        if checker_runtime.get("stop_requested"):
            return "stopped", None

        try:
            await bot.fetch_invite(code)
            return "taken", None

        except discord.NotFound:
            return "available", None

        except discord.Forbidden as e:
            return "fatal_error", f"Forbidden: {e}"

        except discord.HTTPException as e:
            status = getattr(e, "status", None)
            if status == 404:
                return "available", None

            retry_after = getattr(e, "retry_after", None)
            wait_time = float(retry_after) if retry_after else CHECKER_BACKOFF_SECONDS * attempt

            # Rate-limit / server-side errors: wait longer and retry.
            if status in {429, 500, 502, 503, 504, None} and attempt < CHECKER_MAX_RETRIES:
                logger.warning("Checker backoff on %s attempt %s/%s for %.1fs: %s", code, attempt, CHECKER_MAX_RETRIES, wait_time, e)
                stopped = await checker_sleep(wait_time)
                if stopped:
                    return "stopped", None
                continue

            return "temporary_error", f"HTTPException {status}: {e}"

        except Exception as e:
            logger.exception("Unexpected checker error on %s: %s", code, e)
            if attempt < CHECKER_MAX_RETRIES:
                stopped = await checker_sleep(CHECKER_BACKOFF_SECONDS * attempt)
                if stopped:
                    return "stopped", None
                continue
            return "temporary_error", f"{type(e).__name__}: {e}"

    return "temporary_error", "Unknown error"

async def invite_status(code: str) -> str:
    result, _ = await safe_fetch_invite_for_checker(code)
    if result == "available":
        return "available"
    if result == "taken":
        return "taken"
    if result == "stopped":
        return "stopped"
    return "error"


async def safe_dm(user: discord.abc.User, message: str) -> None:
    try:
        await user.send(message)
    except Exception:
        pass

# =========================================================
# CLAIMS / PAYOUTS / ROLES
# =========================================================
def get_payment_method(guild_id: int, user_id: int) -> Optional[str]:
    item = get_payments(guild_id).get(str(user_id))
    if not item:
        return None
    return str(item.get("method") or "").strip() or None

def find_claim(claims: List[dict], claim_id: str) -> Optional[dict]:
    for claim in claims:
        if str(claim.get("id")) == str(claim_id):
            return claim
    return None

def user_claims(guild_id: int, user_id: int) -> List[dict]:
    return [c for c in get_claims(guild_id) if str(c.get("user_id")) == str(user_id)]

def approved_claim_count(guild_id: int, user_id: int) -> int:
    return len([c for c in user_claims(guild_id, user_id) if c.get("status") in {"approved", "sold", "paid"}])

def has_duplicate_claim(guild_id: int, code: str) -> bool:
    code = clean_code(code)
    return any(clean_code(c.get("code", "")) == code for c in get_claims(guild_id))

def claim_spam_check(guild_id: int, user_id: int) -> Tuple[bool, str]:
    cfg = get_config(guild_id)
    cooldown = int(cfg.get("claim_cooldown_seconds", CLAIM_COOLDOWN_SECONDS))
    max_hour = int(cfg.get("max_claims_per_hour", MAX_CLAIMS_PER_HOUR))
    claims = sorted(user_claims(guild_id, user_id), key=lambda c: int(c.get("created_ts", 0)), reverse=True)
    if claims:
        last = int(claims[0].get("created_ts", 0))
        if now_ts() - last < cooldown:
            return True, f"Slow down. You can log another claim <t:{last + cooldown}:R>."
    recent = [c for c in claims if now_ts() - int(c.get("created_ts", 0)) <= 3600]
    if len(recent) >= max_hour:
        return True, f"You reached the limit of `{max_hour}` claims per hour."
    return False, ""

def hunter_percent_from_claims(count: int) -> float:
    if count >= 25:
        return 0.45
    if count >= 15:
        return 0.40
    if count >= 5:
        return 0.35
    return 0.30

def hunter_tier_from_claims(count: int) -> str:
    if count >= 25:
        return "Top Performer"
    if count >= 15:
        return "Elite"
    if count >= 5:
        return "Active"
    return "Starter"

def payout_embed_for(guild: discord.Guild, user_id: int, sale_price: float, manager_brought_buyer: bool) -> discord.Embed:
    count = approved_claim_count(guild.id, user_id)
    hp = hunter_percent_from_claims(count)
    hunter_cut = round(sale_price * hp, 2)
    manager_cut = round(sale_price * 0.10, 2) if manager_brought_buyer else 0.0
    owner_cut = round(max(sale_price - hunter_cut - manager_cut, 0), 2)
    e = make_embed("💰 Payout Calculator", color=GOLD)
    e.description = (
        f"**Hunter:** <@{user_id}>\n"
        f"**Sale Price:** `{money(sale_price)}`\n"
        f"**Approved Claims:** `{count}`\n"
        f"**Tier:** `{hunter_tier_from_claims(count)}`\n"
        f"**Hunter Rate:** `{int(hp * 100)}%`"
    )
    e.add_field(name="Hunter Cut", value=f"`{money(hunter_cut)}`", inline=True)
    e.add_field(name="Manager Cut", value=f"`{money(manager_cut)}`", inline=True)
    e.add_field(name="Owner Cut", value=f"`{money(owner_cut)}`", inline=True)
    e.set_footer(text="Manager cut only applies if a manager brings the buyer.")
    return e

def payout_info_embed() -> discord.Embed:
    e = make_embed("💰 Payout System & Profit Split", color=GOLD)
    e.description = """**This system rewards hunters while keeping the operation profitable.**

Hunters earn more as they prove consistency. Owners keep the majority of profit so servers, boosts, buyers, risk, and management stay worth running."""

    e.add_field(
        name="📊 Base Hunter Tiers",
        value="""```txt
0–4 approved claims    → 30% hunter cut
5–14 approved claims   → 35% hunter cut
15–24 approved claims  → 40% hunter cut
25+ approved claims    → 45% hunter cut
```
Only **approved / sold / paid** claims count toward tiers.""",
        inline=False,
    )

    e.add_field(
        name="💎 High Value Exception",
        value="""For high-value sales, owners may choose to bonus the hunter depending on:
• how rare the vanity is
• how fast it sells
• the hunter’s consistency
• whether the hunter follows rules cleanly

**High value does not automatically mean 50/50.** Bonuses are owner-approved.""",
        inline=False,
    )

    e.add_field(
        name="🛠️ Manager Cut",
        value="""Managers can earn **5–10%** only when they directly help bring or close a buyer.

Manager cut usually applies when they:
• find the buyer
• negotiate the sale
• help close the deal
• organize payment safely""",
        inline=False,
    )

    e.add_field(
        name="👑 Owner Cut",
        value="""Owner keeps the remaining profit after hunter/manager cuts.

Owner profit covers:
• boosted servers
• setup and organization
• risk and disputes
• advertising / buyer finding
• keeping the system running""",
        inline=False,
    )

    e.add_field(
        name="⚠️ Required Before Payment",
        value="""Hunters **must** set payment with `/set_payment` before submitting claims.

No payment set = no claim submissions and no payout.
Fake claims, spam claims, or denied claims = no payout.""",
        inline=False,
    )

    e.set_footer(text="Use /payout to calculate exact cuts for a sale.")
    return e

def autoroles_clean(guild: discord.Guild) -> List[dict]:
    cfg = get_config(guild.id)
    clean = []
    for r in cfg.get("autoroles", []):
        if not isinstance(r, dict):
            continue
        try:
            role_id = int(r.get("role_id", 0))
            req = int(r.get("claims_required", 0))
        except Exception:
            continue
        if role_id > 0 and req > 0:
            clean.append({"role_id": role_id, "claims_required": req})
    if clean != cfg.get("autoroles", []):
        cfg["autoroles"] = clean
        save_config(guild.id, cfg)
    return clean

def autoroles_embed(guild: discord.Guild) -> discord.Embed:
    e = make_embed("🎖️ Current Claim Autoroles", color=BLUE)
    rules = sorted(autoroles_clean(guild), key=lambda x: x["claims_required"])
    if not rules:
        e.description = "No claim autoroles are set yet."
    else:
        lines = []
        for r in rules:
            role = guild.get_role(r["role_id"])
            role_text = role.mention if role else f"`Missing role {r['role_id']}`"
            lines.append(f"{role_text} — `{r['claims_required']}` approved claims")
        e.description = "\n".join(lines)
    return e

def roles_info_embed(guild: discord.Guild) -> discord.Embed:
    e = make_embed("🎖️ How To Get Roles", color=PURPLE)
    e.description = (
        "**Role progression is based on trust, helpfulness, and approved vanity claims.**\n\n"
        "🛠️ **Manager Role**\n"
        "The Manager role is given by owners to people who are trusted, helpful, active, and able to review claims responsibly or help find buyers.\n\n"
        "🏹 **Claim Roles**\n"
        "The roles below are earned automatically when you reach the required amount of approved vanity claims."
    )
    rules = sorted(autoroles_clean(guild), key=lambda x: x["claims_required"])
    if not rules:
        e.add_field(name="Automatic Claim Roles", value="No automatic claim roles are set yet.", inline=False)
    else:
        lines = []
        for r in rules:
            role = guild.get_role(r["role_id"])
            role_text = role.mention if role else f"`Missing role {r['role_id']}`"
            lines.append(f"{role_text} is obtained by claiming **{r['claims_required']}** approved vanities.")
        e.add_field(name="Automatic Claim Roles", value="\n".join(lines), inline=False)
    e.add_field(name="Important", value="Only approved claims count. Pending or denied claims do not count.", inline=False)
    return e

async def apply_autoroles(guild: discord.Guild, member: discord.Member) -> None:
    total = approved_claim_count(guild.id, member.id)
    for r in sorted(autoroles_clean(guild), key=lambda x: x["claims_required"]):
        role = guild.get_role(r["role_id"])
        if role and total >= r["claims_required"] and role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Reached {r['claims_required']} approved claims")
            except Exception:
                pass

def claim_embed(guild: discord.Guild, claim: dict) -> discord.Embed:
    status = claim.get("status", "pending")
    color = GREEN if status in {"approved", "sold", "paid"} else RED if status == "denied" else GOLD
    e = make_embed("🏷️ Vanity Claim", color=color)
    payment_line = ""
    if status == "pending":
        payment = get_payment_method(guild.id, int(claim.get("user_id", 0))) or "Not set"
        payment_line = f"**Payment:** `{payment}`\n"

    e.description = (
        f"**Vanity:** `discord.gg/{clean_code(claim.get('code'))}`\n"
        f"**Hunter:** <@{claim.get('user_id')}>\n"
        f"**Status:** `{status}`\n"
        f"{payment_line}"
        f"**Logged:** <t:{int(claim.get('created_ts', now_ts()))}:R>"
    )
    if claim.get("validation") == "api_uncertain":
        e.add_field(
            name="Validation",
            value="Discord API could not fully verify this invite. Manager should manually double-check it.",
            inline=False,
        )
    if claim.get("buyer"):
        e.add_field(name="Buyer", value=short(str(claim.get("buyer")), 500), inline=True)
    e.set_footer(text=f"Claim ID: {claim.get('id')} • {guild.name}")
    return e

async def post_claim(guild: discord.Guild, claim: dict, pending: bool) -> None:
    cfg = get_config(guild.id)
    channel_id = cfg.get("pending_claim_channel_id") if pending else cfg.get("claim_log_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return
    key = "pending_message_id" if pending else "message_id"
    old = claim.get(key)
    if old:
        try:
            msg = await channel.fetch_message(int(old))
            await msg.delete()
        except Exception:
            pass
    try:
        ping_role_id = cfg.get("claim_ping_role_id")
        content = f"<@&{ping_role_id}>" if ping_role_id else None
        allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)

        if pending:
            msg = await channel.send(
                content=content,
                embed=claim_embed(guild, claim),
                view=PendingClaimReviewView(str(claim["id"])),
                allowed_mentions=allowed,
            )
        else:
            msg = await channel.send(
                content=content,
                embed=claim_embed(guild, claim),
                allowed_mentions=allowed,
            )
        claim[key] = msg.id
        claims = get_claims(guild.id)
        found = find_claim(claims, claim["id"])
        if found:
            found.update(claim)
            save_claims(guild.id, claims)
    except Exception:
        pass

async def delete_claim_message(guild: discord.Guild, claim: dict, pending: bool) -> None:
    cfg = get_config(guild.id)
    channel_id = cfg.get("pending_claim_channel_id") if pending else cfg.get("claim_log_channel_id")
    key = "pending_message_id" if pending else "message_id"
    if not channel_id or not claim.get(key):
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return
    try:
        msg = await channel.fetch_message(int(claim[key]))
        await msg.delete()
    except Exception:
        pass

def stats_embed(guild: discord.Guild, user_id: int) -> discord.Embed:
    claims = sorted(user_claims(guild.id, user_id), key=lambda c: int(c.get("created_ts", 0)), reverse=True)
    count = approved_claim_count(guild.id, user_id)
    e = make_embed("📊 Hunter Stats", f"Stats for <@{user_id}>", PURPLE)
    e.add_field(name="Approved Claims", value=f"`{count}`", inline=True)
    e.add_field(name="Total Submitted", value=f"`{len(claims)}`", inline=True)
    e.add_field(name="Payment", value=f"`{get_payment_method(guild.id, user_id) or 'Not set'}`", inline=True)
    e.add_field(name="Payout Tier", value=f"`{hunter_tier_from_claims(count)}` • `{int(hunter_percent_from_claims(count)*100)}%`", inline=False)
    lines = [f"`discord.gg/{clean_code(c.get('code'))}` • `{c.get('status', 'pending')}` • ID `{c.get('id')}`" for c in claims[:15]]
    e.add_field(name="Claims", value="\n".join(lines) if lines else "No claims yet.", inline=False)
    return e


# =========================================================
# LEADERBOARD
# =========================================================
def leaderboard_embed(guild: discord.Guild) -> discord.Embed:
    claims = get_claims(guild.id)
    stats = {}

    for claim in claims:
        status = str(claim.get("status", "pending")).lower()
        if status not in {"approved", "sold", "paid"}:
            continue

        user_id = int(claim.get("user_id", 0) or 0)
        if user_id <= 0:
            continue

        entry = stats.setdefault(user_id, {
            "claims": 0,
            "sold": 0,
            "paid": 0,
            "latest_ts": 0,
        })

        entry["claims"] += 1
        if status == "sold":
            entry["sold"] += 1
        if status == "paid":
            entry["paid"] += 1
        entry["latest_ts"] = max(entry["latest_ts"], int(claim.get("created_ts", 0) or 0))

    ranked = sorted(
        stats.items(),
        key=lambda item: (item[1]["claims"], item[1]["sold"], item[1]["paid"], item[1]["latest_ts"]),
        reverse=True,
    )[:10]

    total_claims = sum(v["claims"] for v in stats.values())
    total_hunters = len(stats)

    e = make_embed("🏆 Vanity Hunter Claim Leaderboard", color=GOLD)

    if not ranked:
        e.description = "No approved claims yet."
    else:
        medal = ["🥇", "🥈", "🥉"]
        lines = []
        for index, (user_id, data) in enumerate(ranked, start=1):
            icon = medal[index - 1] if index <= 3 else f"`#{index}`"
            tier = hunter_tier_from_claims(int(data["claims"]))
            rate = int(hunter_percent_from_claims(int(data["claims"])) * 100)
            lines.append(
                f"{icon} <@{user_id}> — **{data['claims']}** claims • `{tier}` • `{rate}%`"
            )

        e.description = "\n".join(lines)

    e.add_field(
        name="Server Totals",
        value=f"Claims: `{total_claims}` • Ranked Hunters: `{total_hunters}`",
        inline=False,
    )

    e.add_field(
        name="Ranking Info",
        value="Ranks are based on approved/sold/paid claim count. Pending and denied claims do not count.",
        inline=False,
    )

    e.set_footer(text="Auto-updates every hour.")
    return e

async def post_or_update_leaderboard(guild: discord.Guild, channel: Optional[discord.TextChannel] = None) -> Optional[discord.Message]:
    cfg = get_config(guild.id)

    if channel is not None:
        cfg["leaderboard_channel_id"] = channel.id

    channel_id = cfg.get("leaderboard_channel_id")
    if not channel_id:
        return None

    target = guild.get_channel(int(channel_id))
    if not target:
        try:
            target = await bot.fetch_channel(int(channel_id))
        except Exception:
            return None

    embed = leaderboard_embed(guild)
    message_id = cfg.get("leaderboard_message_id")

    if message_id:
        try:
            msg = await target.fetch_message(int(message_id))
            await msg.edit(embed=embed)
            save_config(guild.id, cfg)
            return msg
        except Exception:
            pass

    msg = await target.send(embed=embed)
    cfg["leaderboard_channel_id"] = target.id
    cfg["leaderboard_message_id"] = msg.id
    save_config(guild.id, cfg)
    return msg


# =========================================================
# GUIDES
# =========================================================
def hunter_guide_embed() -> discord.Embed:
    e = make_embed("🏹 Vanity Hunter Guide", color=PURPLE)
    e.description = (
        "💰 **Required:** You must run `/set_payment` before submitting claims.\n\n"
        "**How to hunt:**\n"
        "1. Find lists posted in the lists channel. Updates usually happen every `1h30m`.\n"
        "2. Go to the boosted server provided for you. Ping an owner if you do not have one yet.\n"
        "3. Go to **Server Settings → Custom URL**.\n"
        "4. Try as many vanities as you can until rate limited.\n"
        "5. If rate limited, wait `15–30 minutes` before trying again.\n"
        "6. Repeat until you successfully claim a word from the lists.\n\n"
        "**After claiming:**\n"
        "1. Open `/hunter_panel` and click **Log Claim**.\n"
        "2. Enter only the vanity you claimed.\n"
        "3. Wait for manager approval.\n"
        "4. If approved, you get DM’d and wait for next steps.\n"
        "5. If denied, keep trying.\n\n"
        "Fake or spam claims can get you removed."
    )
    return e

def manager_guide_embed(guild: discord.Guild) -> discord.Embed:
    e = make_embed("🛠️ Manager Guide", color=GOLD)
    e.description = (
        "**Manager responsibilities:**\n"
        "• Review pending hunter claims.\n"
        "• Approve or deny claims accurately.\n"
        "• Keep claim logs clean.\n"
        "• Add buyer/status information.\n"
        "• **Find buyers for claimed vanities.**\n"
        "• Make sure hunters have payment methods set before payouts.\n"
        "• Use `/payout` to calculate owner/hunter/manager cuts.\n\n"
        "**Manager role:**\n"
        "The manager role is earned by being trusted, helpful, active, and useful to the server/business."
    )
    e.add_field(name="Autoroles", value=autoroles_embed(guild).description or "No autoroles set.", inline=False)
    return e

def hunter_panel_embed() -> discord.Embed:
    e = make_embed("🏹 Vanity Hunter Panel", color=PURPLE)
    e.description = (
        "Use this panel to log successful vanity pulls, view stats, learn payouts, apply for manager, and see how to earn roles.\n\n"
        "💰 **Required:** run `/set_payment` before claiming.\n"
        "✅ Claims must be approved before they are posted.\n"
        "🎖️ Click **Get Roles** to see what roles you can earn."
    )
    return e

def manager_panel_embed() -> discord.Embed:
    e = make_embed("🛠️ Manager Control Panel", color=GOLD)
    e.description = (
        "Control pending claims, posted claims, buyer/status info, settings, payout rules, and autoroles.\n\n"
        "Managers are responsible for reviewing claims and finding buyers."
    )
    return e


# =========================================================
# MANAGER APPLICATIONS
# =========================================================
def manager_application_info_embed() -> discord.Embed:
    e = make_embed("🛠️ Apply For Manager", color=GOLD)
    e.description = """**The Manager role is not earned automatically from claims.**

Managers are chosen based on trust, helpfulness, activity, maturity, and usefulness to the server/business."""
    e.add_field(
        name="What Managers Do",
        value="""• review and approve/deny claims
• help keep logs clean
• help hunters understand the system
• help find buyers for good vanities
• handle buyer/status info carefully""",
        inline=False,
    )
    e.add_field(
        name="What Owners Look For",
        value="""• active and helpful in chat
• honest and trusted
• understands the vanity system
• does not abuse permissions
• can help bring value, buyers, or organization""",
        inline=False,
    )
    e.set_footer(text="Click Apply for Manager in the Hunter Panel to submit an application.")
    return e

def manager_application_embed(guild: discord.Guild, app: dict) -> discord.Embed:
    e = make_embed("📝 Manager Application", color=BLUE)
    e.description = (
        f"**Applicant:** <@{app['user_id']}>\n"
        f"**Status:** `{app.get('status', 'pending')}`\n"
        f"**Submitted:** <t:{int(app.get('created_ts', now_ts()))}:R>\n"
        f"**Application ID:** `{app['id']}`"
    )
    e.add_field(name="Why They Want Manager", value=short(app.get("why", "No answer."), 1024), inline=False)
    e.add_field(name="How They Can Help", value=short(app.get("help", "No answer."), 1024), inline=False)
    e.add_field(name="Experience / Trust", value=short(app.get("experience", "No answer."), 1024), inline=False)
    if app.get("denial_reason"):
        e.add_field(name="Denial Reason", value=short(app.get("denial_reason"), 1024), inline=False)
    return e

class ManagerApplicationReviewView(discord.ui.View):
    def __init__(self, app_id: str):
        super().__init__(timeout=None)
        self.app_id = str(app_id)

    @discord.ui.button(label="Approve Manager", style=discord.ButtonStyle.success, custom_id="manager_app_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_admin(interaction):
            return
        apps = get_manager_apps(interaction.guild.id)
        app = next((x for x in apps if str(x.get("id")) == self.app_id), None)
        if not app:
            return await interaction.response.send_message("Application not found.", ephemeral=True)
        if app.get("status") != "pending":
            return await interaction.response.send_message("This application was already reviewed.", ephemeral=True)

        cfg = get_config(interaction.guild.id)
        role_id = cfg.get("manager_role_id") or MANAGER_ROLE_ID
        role = interaction.guild.get_role(int(role_id or 0))
        if not role:
            return await interaction.response.send_message("No manager role is configured. Use `/set_manager_role` first.", ephemeral=True)

        member = interaction.guild.get_member(int(app["user_id"]))
        if not member:
            return await interaction.response.send_message("Applicant is not in this server anymore.", ephemeral=True)

        try:
            await member.add_roles(role, reason=f"Manager application approved by {interaction.user}")
        except Exception:
            return await interaction.response.send_message("I could not give the manager role. Check my role position/permissions.", ephemeral=True)

        app["status"] = "approved"
        app["reviewed_by"] = interaction.user.id
        app["reviewed_ts"] = now_ts()
        save_manager_apps(interaction.guild.id, apps)
        await safe_dm(member, "✅ Your manager application was approved. You have been given the Manager role.")
        try:
            await interaction.message.edit(embed=manager_application_embed(interaction.guild, app), view=None)
        except Exception:
            pass
        await interaction.response.send_message("Approved manager application, gave role, and sent DM.", ephemeral=True)

    @discord.ui.button(label="Deny Manager", style=discord.ButtonStyle.danger, custom_id="manager_app_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_admin(interaction):
            return
        await interaction.response.send_modal(DenyManagerApplicationModal(self.app_id))

async def post_manager_application(guild: discord.Guild, app: dict) -> None:
    cfg = get_config(guild.id)
    channel_id = cfg.get("manager_application_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return
    msg = await channel.send(embed=manager_application_embed(guild, app), view=ManagerApplicationReviewView(app["id"]))
    app["message_id"] = msg.id
    apps = get_manager_apps(guild.id)
    found = next((x for x in apps if x.get("id") == app["id"]), None)
    if found:
        found.update(app)
        save_manager_apps(guild.id, apps)

class ManagerApplicationModal(discord.ui.Modal, title="Manager Application"):
    why = discord.ui.TextInput(label="Why do you want manager?", style=discord.TextStyle.paragraph, max_length=900)
    help = discord.ui.TextInput(label="How can you help?", style=discord.TextStyle.paragraph, max_length=900)
    experience = discord.ui.TextInput(label="Why should owners trust you?", style=discord.TextStyle.paragraph, max_length=900)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        apps = get_manager_apps(interaction.guild.id)
        if any(str(a.get("user_id")) == str(interaction.user.id) and a.get("status") == "pending" for a in apps):
            return await interaction.response.send_message("You already have a pending manager application.", ephemeral=True)
        app = {
            "id": make_id(),
            "user_id": interaction.user.id,
            "created_ts": now_ts(),
            "status": "pending",
            "why": str(self.why).strip(),
            "help": str(self.help).strip(),
            "experience": str(self.experience).strip(),
            "message_id": None,
            "reviewed_by": None,
            "reviewed_ts": None,
            "denial_reason": "",
        }
        apps.append(app)
        save_manager_apps(interaction.guild.id, apps)
        await post_manager_application(interaction.guild, app)
        await interaction.response.send_message("✅ Manager application submitted. An owner will review it.", ephemeral=True)

class DenyManagerApplicationModal(discord.ui.Modal, title="Deny Manager Application"):
    reason = discord.ui.TextInput(label="Denial reason", style=discord.TextStyle.paragraph, max_length=900)

    def __init__(self, app_id: str):
        super().__init__()
        self.app_id = app_id

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        apps = get_manager_apps(interaction.guild.id)
        app = next((x for x in apps if str(x.get("id")) == str(self.app_id)), None)
        if not app:
            return await interaction.response.send_message("Application not found.", ephemeral=True)
        if app.get("status") != "pending":
            return await interaction.response.send_message("This application was already reviewed.", ephemeral=True)

        app["status"] = "denied"
        app["reviewed_by"] = interaction.user.id
        app["reviewed_ts"] = now_ts()
        app["denial_reason"] = str(self.reason).strip()
        save_manager_apps(interaction.guild.id, apps)

        user = interaction.guild.get_member(int(app["user_id"])) or await bot.fetch_user(int(app["user_id"]))
        await safe_dm(user, f"❌ Your manager application was denied.\n\nReason: {app['denial_reason']}\n\nStay helpful, active, and trusted, then try again later.")

        try:
            await interaction.message.edit(embed=manager_application_embed(interaction.guild, app), view=None)
        except Exception:
            pass
        await interaction.response.send_message("Denied manager application and sent DM.", ephemeral=True)


# =========================================================
# MODALS
# =========================================================
class LogClaimModal(discord.ui.Modal, title="Log Vanity Claim"):
    code = discord.ui.TextInput(label="Vanity code/link", placeholder="example or discord.gg/example", max_length=80)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)
        if not get_payment_method(interaction.guild.id, interaction.user.id):
            return await interaction.response.send_message("❌ You must run `/set_payment` before claiming. No payment set = no payouts.", ephemeral=True)
        code = clean_code(str(self.code))
        if not code:
            return await interaction.response.send_message("Enter a valid vanity.", ephemeral=True)
        valid, reason = await invite_is_valid(code)
        if not valid:
            return await interaction.response.send_message(
                f"`discord.gg/{code}` could not be confirmed as a valid invite. Double-check the exact invite code and try again.",
                ephemeral=True
            )
        validation_note = ""
        if reason == "api_uncertain":
            validation_note = " Discord's API could not fully verify it, so a manager must double-check it."
        if has_duplicate_claim(interaction.guild.id, code):
            return await interaction.response.send_message("That vanity is already logged.", ephemeral=True)
        spam, reason = claim_spam_check(interaction.guild.id, interaction.user.id)
        if spam and not is_manager(interaction.user):
            return await interaction.response.send_message(reason, ephemeral=True)
        claim = {
            "id": make_id(),
            "code": code,
            "user_id": interaction.user.id,
            "created_by": interaction.user.id,
            "created_ts": now_ts(),
            "updated_ts": now_ts(),
            "status": "pending",
            "buyer": "",
            "message_id": None,
            "pending_message_id": None,
            "validation": reason,
        }
        claims = get_claims(interaction.guild.id)
        claims.append(claim)
        save_claims(interaction.guild.id, claims)
        await post_claim(interaction.guild, claim, pending=True)
        extra = validation_note if validation_note else ""
        await interaction.response.send_message(f"📩 Submitted `discord.gg/{code}` for approval.{extra}", ephemeral=True)

class ManagerEditClaimModal(discord.ui.Modal, title="Edit Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", max_length=40)
    code = discord.ui.TextInput(label="New vanity code/link (optional)", required=False, max_length=80)
    user_id = discord.ui.TextInput(label="New hunter user ID/mention (optional)", required=False, max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction): return
        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
        if str(self.code).strip():
            code = clean_code(str(self.code))
            valid, _ = await invite_is_valid(code)
            if not valid:
                return await interaction.response.send_message("That vanity is not currently valid.", ephemeral=True)
            claim["code"] = code
        uid = parse_snowflake(str(self.user_id))
        if uid:
            claim["user_id"] = uid
        claim["updated_ts"] = now_ts()
        save_claims(interaction.guild.id, claims)
        await post_claim(interaction.guild, claim, pending=(claim.get("status") == "pending"))
        if claim.get("status") in {"approved", "sold", "paid"}:
            await post_claim(interaction.guild, claim, pending=False)
        await interaction.response.send_message(f"Updated Claim ID `{claim['id']}`.", ephemeral=True)

class ManagerStatusClaimModal(discord.ui.Modal, title="Set Claim Status / Buyer"):
    claim_id = discord.ui.TextInput(label="Claim ID", max_length=40)
    status = discord.ui.TextInput(label="Status", placeholder="pending, approved, denied, sold, paid", max_length=20)
    buyer = discord.ui.TextInput(label="Buyer info (optional)", required=False, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction): return
        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
        status = str(self.status).strip().lower()
        if status not in {"pending", "approved", "denied", "sold", "paid"}:
            return await interaction.response.send_message("Status must be pending, approved, denied, sold, or paid.", ephemeral=True)
        old_status = claim.get("status", "pending")
        claim["status"] = status
        if str(self.buyer).strip():
            claim["buyer"] = str(self.buyer).strip()
        claim["updated_ts"] = now_ts()
        save_claims(interaction.guild.id, claims)
        member = interaction.guild.get_member(int(claim["user_id"]))
        user = member or await bot.fetch_user(int(claim["user_id"]))
        if status in {"approved", "sold", "paid"}:
            await delete_claim_message(interaction.guild, claim, pending=True)
            await post_claim(interaction.guild, claim, pending=False)
            if old_status != status:
                await safe_dm(user, f"✅ Your claim `discord.gg/{clean_code(claim.get('code'))}` was approved. An owner/manager will follow up with next steps.")
            if member:
                await apply_autoroles(interaction.guild, member)
        elif status == "denied":
            await delete_claim_message(interaction.guild, claim, pending=True)
            await delete_claim_message(interaction.guild, claim, pending=False)
            if old_status != "denied":
                await safe_dm(user, f"❌ Your claim `discord.gg/{clean_code(claim.get('code'))}` was denied. Keep trying other words from the lists.")
        else:
            await post_claim(interaction.guild, claim, pending=True)
        await interaction.response.send_message(f"Updated Claim ID `{claim['id']}` to `{status}`.", ephemeral=True)

class ManagerDeleteClaimModal(discord.ui.Modal, title="Delete Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", max_length=40)
    confirm = discord.ui.TextInput(label="Type DELETE to confirm", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction): return
        if str(self.confirm).strip().upper() != "DELETE":
            return await interaction.response.send_message("Cancelled.", ephemeral=True)
        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)
        await delete_claim_message(interaction.guild, claim, pending=True)
        await delete_claim_message(interaction.guild, claim, pending=False)
        save_claims(interaction.guild.id, [c for c in claims if c.get("id") != claim.get("id")])
        await interaction.response.send_message(f"Deleted Claim ID `{claim['id']}`.", ephemeral=True)

class ManagerConfigModal(discord.ui.Modal, title="Manager Config"):
    approved_channel = discord.ui.TextInput(label="Approved claim log channel ID/mention", required=False, max_length=40)
    pending_channel = discord.ui.TextInput(label="Pending claim review channel ID/mention", required=False, max_length=40)
    cooldown = discord.ui.TextInput(label="Claim cooldown seconds", required=False, placeholder="60", max_length=10)
    max_hour = discord.ui.TextInput(label="Max claims per hour", required=False, placeholder="5", max_length=10)
    manager_app_channel = discord.ui.TextInput(label="Manager application channel ID/mention", required=False, max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction): return
        cfg = get_config(interaction.guild.id)
        approved = parse_snowflake(str(self.approved_channel))
        pending = parse_snowflake(str(self.pending_channel))
        if approved:
            cfg["claim_log_channel_id"] = approved
        if pending:
            cfg["pending_claim_channel_id"] = pending
        if str(self.cooldown).strip():
            cfg["claim_cooldown_seconds"] = max(0, int(str(self.cooldown).strip()))
        if str(self.max_hour).strip():
            cfg["max_claims_per_hour"] = max(1, int(str(self.max_hour).strip()))
        app_channel = parse_snowflake(str(self.manager_app_channel))
        if app_channel:
            cfg["manager_application_channel_id"] = app_channel
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message("Settings saved.", ephemeral=True)

class AddAutoroleModal(discord.ui.Modal, title="Add Claim Autorole"):
    role_id = discord.ui.TextInput(label="Role ID or mention", max_length=40)
    claims_required = discord.ui.TextInput(label="Approved claims required", placeholder="5", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction): return
        rid = parse_snowflake(str(self.role_id))
        if not rid:
            return await interaction.response.send_message("Enter a valid role ID or mention.", ephemeral=True)
        try:
            req = max(1, int(str(self.claims_required).strip()))
        except Exception:
            return await interaction.response.send_message("Claims required must be a number.", ephemeral=True)
        if not interaction.guild.get_role(rid):
            return await interaction.response.send_message("That role was not found in this server.", ephemeral=True)
        cfg = get_config(interaction.guild.id)
        cfg["autoroles"] = [r for r in autoroles_clean(interaction.guild) if str(r["role_id"]) != str(rid)]
        cfg["autoroles"].append({"role_id": rid, "claims_required": req})
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Autorole saved: <@&{rid}> at `{req}` approved claims.", ephemeral=True)

class DeleteAutoroleModal(discord.ui.Modal, title="Delete Claim Autorole"):
    role_id = discord.ui.TextInput(label="Role ID or mention", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction): return
        rid = parse_snowflake(str(self.role_id))
        if not rid:
            return await interaction.response.send_message("Enter a valid role ID or mention.", ephemeral=True)
        cfg = get_config(interaction.guild.id)
        before = len(autoroles_clean(interaction.guild))
        cfg["autoroles"] = [r for r in autoroles_clean(interaction.guild) if str(r["role_id"]) != str(rid)]
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Deleted autorole <@&{rid}>." if len(cfg["autoroles"]) < before else "That autorole rule was not found.", ephemeral=True)


# =========================================================
# QUICK REVIEW BUTTONS + SETUP WIZARD
# =========================================================
async def approve_claim_by_id(interaction: discord.Interaction, claim_id: str) -> None:
    if not await require_manager(interaction):
        return

    claims = get_claims(interaction.guild.id)
    claim = find_claim(claims, str(claim_id))
    if not claim:
        return await interaction.response.send_message("Claim not found.", ephemeral=True)

    old_status = claim.get("status", "pending")
    claim["status"] = "approved"
    claim["updated_ts"] = now_ts()
    claim["last_manager_id"] = interaction.user.id
    save_claims(interaction.guild.id, claims)

    member = interaction.guild.get_member(int(claim["user_id"]))
    user = member or await bot.fetch_user(int(claim["user_id"]))

    await delete_claim_message(interaction.guild, claim, pending=True)
    await post_claim(interaction.guild, claim, pending=False)

    if old_status != "approved":
        await safe_dm(user, f"✅ Your claim `discord.gg/{clean_code(claim.get('code'))}` was approved. An owner/manager will follow up with next steps.")

    if member:
        await apply_autoroles(interaction.guild, member)

    try:
        await interaction.message.edit(view=None)
    except Exception:
        pass

    await interaction.response.send_message(f"Approved Claim ID `{claim_id}`.", ephemeral=True)

class DenyClaimReasonModal(discord.ui.Modal, title="Deny Claim"):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=900,
        placeholder="Optional reason sent to the hunter."
    )

    def __init__(self, claim_id: str):
        super().__init__()
        self.claim_id = str(claim_id)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return

        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, self.claim_id)
        if not claim:
            return await interaction.response.send_message("Claim not found.", ephemeral=True)

        claim["status"] = "denied"
        claim["updated_ts"] = now_ts()
        claim["last_manager_id"] = interaction.user.id
        claim["denial_reason"] = str(self.reason).strip()
        save_claims(interaction.guild.id, claims)

        user = interaction.guild.get_member(int(claim["user_id"])) or await bot.fetch_user(int(claim["user_id"]))
        reason_text = f"\n\nReason: {claim['denial_reason']}" if claim["denial_reason"] else ""
        await safe_dm(user, f"❌ Your claim `discord.gg/{clean_code(claim.get('code'))}` was denied.{reason_text}\n\nKeep trying other words from the lists.")

        await delete_claim_message(interaction.guild, claim, pending=True)
        await delete_claim_message(interaction.guild, claim, pending=False)

        try:
            await interaction.message.edit(embed=claim_embed(interaction.guild, claim), view=None)
        except Exception:
            pass

        await interaction.response.send_message(f"Denied Claim ID `{self.claim_id}`.", ephemeral=True)

class SetBuyerFromClaimModal(discord.ui.Modal, title="Set Buyer / Status"):
    buyer = discord.ui.TextInput(label="Buyer info", required=False, max_length=200)
    status = discord.ui.TextInput(label="Status", placeholder="approved, sold, paid", required=False, max_length=20)

    def __init__(self, claim_id: str):
        super().__init__()
        self.claim_id = str(claim_id)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return

        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, self.claim_id)
        if not claim:
            return await interaction.response.send_message("Claim not found.", ephemeral=True)

        status = str(self.status).strip().lower() or claim.get("status", "pending")
        if status not in {"pending", "approved", "denied", "sold", "paid"}:
            return await interaction.response.send_message("Status must be pending, approved, denied, sold, or paid.", ephemeral=True)

        if str(self.buyer).strip():
            claim["buyer"] = str(self.buyer).strip()
        claim["status"] = status
        claim["updated_ts"] = now_ts()
        claim["last_manager_id"] = interaction.user.id
        save_claims(interaction.guild.id, claims)

        if status in {"approved", "sold", "paid"}:
            await delete_claim_message(interaction.guild, claim, pending=True)
            await post_claim(interaction.guild, claim, pending=False)
        else:
            await post_claim(interaction.guild, claim, pending=True)

        await interaction.response.send_message(f"Updated buyer/status for Claim ID `{self.claim_id}`.", ephemeral=True)

class PendingClaimReviewView(discord.ui.View):
    def __init__(self, claim_id: str):
        super().__init__(timeout=None)
        self.claim_id = str(claim_id)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="pending_claim_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await approve_claim_by_id(interaction, self.claim_id)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="pending_claim_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(DenyClaimReasonModal(self.claim_id))

    @discord.ui.button(label="Set Buyer / Status", style=discord.ButtonStyle.primary, custom_id="pending_claim_buyer")
    async def set_buyer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(SetBuyerFromClaimModal(self.claim_id))

class SetupChannelsModal(discord.ui.Modal, title="Setup Claim Channels"):
    pending_channel = discord.ui.TextInput(label="Pending review channel ID/mention", required=False, max_length=40)
    approved_channel = discord.ui.TextInput(label="Approved claim log channel ID/mention", required=False, max_length=40)
    leaderboard_channel = discord.ui.TextInput(label="Leaderboard channel ID/mention", required=False, max_length=40)
    claim_ping_role = discord.ui.TextInput(label="Claim ping role ID/mention", required=False, max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return

        cfg = get_config(interaction.guild.id)
        pending = parse_snowflake(str(self.pending_channel))
        approved = parse_snowflake(str(self.approved_channel))
        leaderboard = parse_snowflake(str(self.leaderboard_channel))
        claim_ping = parse_snowflake(str(self.claim_ping_role))

        if pending:
            cfg["pending_claim_channel_id"] = pending
        if approved:
            cfg["claim_log_channel_id"] = approved
        if leaderboard:
            cfg["leaderboard_channel_id"] = leaderboard
            cfg["leaderboard_message_id"] = None
        if claim_ping:
            cfg["claim_ping_role_id"] = claim_ping

        save_config(interaction.guild.id, cfg)

        msg = ""
        if leaderboard:
            channel = interaction.guild.get_channel(leaderboard)
            if channel:
                await post_or_update_leaderboard(interaction.guild, channel)
                msg += f"\nLeaderboard posted in <#{leaderboard}>."

        await interaction.response.send_message("Claim channel setup saved." + msg, ephemeral=True)

class SetupRolesModal(discord.ui.Modal, title="Setup Roles"):
    manager_role = discord.ui.TextInput(label="Manager role ID/mention", required=False, max_length=40)
    manager_application_channel = discord.ui.TextInput(label="Manager application channel ID/mention", required=False, max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return

        cfg = get_config(interaction.guild.id)
        manager_role = parse_snowflake(str(self.manager_role))
        app_channel = parse_snowflake(str(self.manager_application_channel))

        if manager_role:
            cfg["manager_role_id"] = manager_role
        if app_channel:
            cfg["manager_application_channel_id"] = app_channel

        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message("Role/application setup saved.", ephemeral=True)

class SetupLimitsModal(discord.ui.Modal, title="Setup Claim Limits"):
    cooldown = discord.ui.TextInput(label="Claim cooldown seconds", required=False, placeholder="60", max_length=10)
    max_hour = discord.ui.TextInput(label="Max claims per hour", required=False, placeholder="5", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return

        cfg = get_config(interaction.guild.id)

        if str(self.cooldown).strip():
            cfg["claim_cooldown_seconds"] = max(0, int(str(self.cooldown).strip()))
        if str(self.max_hour).strip():
            cfg["max_claims_per_hour"] = max(1, int(str(self.max_hour).strip()))

        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message("Claim limits saved.", ephemeral=True)

class SetupWizardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)

    @discord.ui.button(label="1. Claim Channels", style=discord.ButtonStyle.primary, custom_id="setup_claim_channels")
    async def claim_channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(SetupChannelsModal())

    @discord.ui.button(label="2. Roles / Applications", style=discord.ButtonStyle.primary, custom_id="setup_roles_apps")
    async def roles_apps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(SetupRolesModal())

    @discord.ui.button(label="3. Claim Limits", style=discord.ButtonStyle.secondary, custom_id="setup_claim_limits")
    async def limits(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.response.send_modal(SetupLimitsModal())

    @discord.ui.button(label="Post Hunter Panel", style=discord.ButtonStyle.success, custom_id="setup_post_hunter_panel")
    async def post_hunter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.channel.send(embed=hunter_panel_embed(), view=HunterPanel())
        await interaction.response.send_message("Hunter panel posted in this channel.", ephemeral=True)

    @discord.ui.button(label="Post Manager Panel", style=discord.ButtonStyle.success, custom_id="setup_post_manager_panel")
    async def post_manager(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction):
            return
        await interaction.channel.send(embed=manager_panel_embed(), view=ManagerPanel())
        await interaction.response.send_message("Manager panel posted in this channel.", ephemeral=True)

def setup_wizard_embed(guild: discord.Guild) -> discord.Embed:
    cfg = get_config(guild.id)
    e = make_embed("⚙️ Vanity Bot Setup Wizard", color=BLUE)
    e.description = (
        "Use the buttons below to configure the bot without typing a bunch of separate commands.\n\n"
        "**Recommended order:**\n"
        "1. Set claim channels\n"
        "2. Set manager role/application channel\n"
        "3. Set claim limits\n"
        "4. Post your panels"
    )
    e.add_field(
        name="Current Channels",
        value=(
            f"Pending Claims: {f'<#{cfg.get('pending_claim_channel_id')}>' if cfg.get('pending_claim_channel_id') else '`not set`'}\n"
            f"Approved Claims: {f'<#{cfg.get('claim_log_channel_id')}>' if cfg.get('claim_log_channel_id') else '`not set`'}\n"
            f"Leaderboard: {f'<#{cfg.get('leaderboard_channel_id')}>' if cfg.get('leaderboard_channel_id') else '`not set`'}\n"
            f"Manager Apps: {f'<#{cfg.get('manager_application_channel_id')}>' if cfg.get('manager_application_channel_id') else '`not set`'}"
        ),
        inline=False,
    )
    e.add_field(
        name="Current Limits",
        value=(
            f"Cooldown: `{cfg.get('claim_cooldown_seconds', CLAIM_COOLDOWN_SECONDS)}s`\n"
            f"Max claims/hour: `{cfg.get('max_claims_per_hour', MAX_CLAIMS_PER_HOUR)}`"
        ),
        inline=False,
    )
    return e


# =========================================================
# PANELS
# =========================================================
class HunterPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Log Claim", style=discord.ButtonStyle.success, custom_id="hunter_log_claim")
    async def log_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LogClaimModal())

    @discord.ui.button(label="My Stats", style=discord.ButtonStyle.primary, custom_id="hunter_my_stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=stats_embed(interaction.guild, interaction.user.id), ephemeral=True)

    @discord.ui.button(label="Info", style=discord.ButtonStyle.secondary, custom_id="hunter_info")
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=hunter_guide_embed(), ephemeral=True)

    @discord.ui.button(label="Get Roles", style=discord.ButtonStyle.secondary, custom_id="hunter_get_roles")
    async def get_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=roles_info_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Payout Info", style=discord.ButtonStyle.secondary, custom_id="hunter_payout_info")
    async def payout_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=payout_info_embed(), ephemeral=True)

    @discord.ui.button(label="Apply For Manager", style=discord.ButtonStyle.primary, custom_id="hunter_apply_manager")
    async def apply_manager(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ManagerApplicationModal())

    @discord.ui.button(label="Manager Role Info", style=discord.ButtonStyle.secondary, custom_id="hunter_manager_role_info")
    async def manager_role_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=manager_application_info_embed(), ephemeral=True)

class ManagerPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Edit Claim", style=discord.ButtonStyle.primary, custom_id="manager_edit_claim")
    async def edit_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(ManagerEditClaimModal())

    @discord.ui.button(label="Status / Buyer", style=discord.ButtonStyle.primary, custom_id="manager_status_claim")
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(ManagerStatusClaimModal())

    @discord.ui.button(label="Delete Claim", style=discord.ButtonStyle.danger, custom_id="manager_delete_claim")
    async def delete_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(ManagerDeleteClaimModal())

    @discord.ui.button(label="Recent Claims", style=discord.ButtonStyle.secondary, custom_id="manager_recent_claims")
    async def recent_claims(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        claims = sorted(get_claims(interaction.guild.id), key=lambda c: int(c.get("created_ts", 0)), reverse=True)[:20]
        e = make_embed("🧾 Recent Claims", color=GOLD)
        e.description = "\n".join(
            f"`{c['id']}` • <@{c['user_id']}> • `discord.gg/{clean_code(c.get('code'))}` • `{c.get('status', 'pending')}` • pay `{get_payment_method(interaction.guild.id, int(c.get('user_id'))) or 'Not set'}`"
            for c in claims
        ) or "No claims yet."
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.secondary, custom_id="manager_settings")
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(ManagerConfigModal())

    @discord.ui.button(label="Add Autorole", style=discord.ButtonStyle.success, custom_id="manager_add_autorole")
    async def add_autorole(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(AddAutoroleModal())

    @discord.ui.button(label="View Autoroles", style=discord.ButtonStyle.secondary, custom_id="manager_view_autoroles")
    async def view_autoroles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_message(embed=autoroles_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Delete Autorole", style=discord.ButtonStyle.danger, custom_id="manager_delete_autorole")
    async def delete_autorole(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(DeleteAutoroleModal())

    @discord.ui.button(label="Manager Guide", style=discord.ButtonStyle.secondary, custom_id="manager_info")
    async def manager_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_message(embed=manager_guide_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Get Roles", style=discord.ButtonStyle.secondary, custom_id="manager_get_roles")
    async def get_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_message(embed=roles_info_embed(interaction.guild), ephemeral=True)

    @discord.ui.button(label="Payout Info", style=discord.ButtonStyle.secondary, custom_id="manager_payout_info")
    async def payout_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_message(embed=payout_info_embed(), ephemeral=True)

# =========================================================
# CHECKER
# =========================================================
async def checker_send_line(channel, text: str) -> None:
    if not channel:
        return
    try:
        await channel.send(text)
        await asyncio.sleep(CHECKER_MESSAGE_DELAY_SECONDS)
    except Exception as e:
        logger.warning("Failed to send checker line: %s", e)

def final_available_embed(list_name: str, codes: List[str], available: List[str], taken: List[str], errors: List[str], became_taken: List[str], became_available: List[str]) -> discord.Embed:
    e = make_embed("✅ Updated Hunter Vanity List", color=GREEN if available else RED)
    e.description = f"""**List:** `{list_name}`
**Total Checked:** `{len(codes)}`
**Not Taken / Sent To Hunters:** `{len(available)}`
**Taken:** `{len(taken)}`
**Errors / Skipped:** `{len(errors)}`
**Became Taken Since Last Run:** `{len(became_taken)}`
**Became Not Taken Since Last Run:** `{len(became_available)}`
**Updated:** <t:{now_ts()}:R>

Only vanities that are **not currently taken** are included for hunters."""
    if available:
        preview = ", ".join(available[:80])
        if len(available) > 80:
            preview += f", ... +{len(available) - 80} more"
        e.add_field(name="Copyable Preview", value="```txt\\n" + preview + "\\n```", inline=False)
    if became_taken:
        txt = ", ".join(f"`{x}`" for x in became_taken[:30])
        if len(became_taken) > 30:
            txt += f" ... +{len(became_taken) - 30} more"
        e.add_field(name="Removed Because They Became Taken", value=txt, inline=False)
    if became_available:
        txt = ", ".join(f"`{x}`" for x in became_available[:30])
        if len(became_available) > 30:
            txt += f" ... +{len(became_available) - 30} more"
        e.add_field(name="Added Because They Became Not Taken", value=txt, inline=False)
    e.add_field(name="Full List", value="The complete updated hunter list is attached as a `.txt` file.", inline=False)
    e.set_footer(text="Auto-updated after a full slow check.")
    return e

def available_file(list_name: str, length: int, available: List[str]) -> discord.File:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", list_name)[:40] or "vanities"
    path = DATA_DIR / "exports" / f"{safe}_{length}_letter_available.txt"
    links = chr(10).join(f"discord.gg/{x}" for x in available)
    content = f"""Available vanities
List: {list_name}
Length: {length}
Updated: {now_ts()}

COPYABLE WORDS:
{', '.join(available)}

COPYABLE LINKS:
{links}
"""
    path.write_text(content, encoding="utf-8")
    return discord.File(str(path), filename=path.name)

def full_available_file(list_name: str, codes: List[str], available: List[str], taken: List[str], errors: List[str]) -> discord.File:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", list_name)[:40] or "vanities"
    path = DATA_DIR / "exports" / f"{safe}_full_results.txt"
    available_links = chr(10).join(f"discord.gg/{x}" for x in available)
    taken_links = chr(10).join(f"discord.gg/{x}" for x in taken)
    error_links = chr(10).join(f"discord.gg/{x}" for x in errors)
    content = f"""Full vanity check results
List: {list_name}
Updated: {now_ts()}
Total checked: {len(codes)}
Available / not taken: {len(available)}
Taken / claimed: {len(taken)}
Errors / uncertain: {len(errors)}

AVAILABLE WORDS SENT TO HUNTERS:
{', '.join(available)}

AVAILABLE LINKS:
{available_links}

TAKEN / REMOVED:
{', '.join(taken)}

TAKEN LINKS:
{taken_links}

ERROR / UNCERTAIN:
{', '.join(errors)}

ERROR LINKS:
{error_links}

ALL CHECKED:
{', '.join(codes)}
"""
    path.write_text(content, encoding="utf-8")
    return discord.File(str(path), filename=path.name)

def hunter_list_embed(list_name: str, length: int, available: List[str], removed_taken: List[str], newly_available: List[str], total_checked: int, errors: int) -> discord.Embed:
    e = make_embed(f"📋 {length} Letter Available Vanities", color=GREEN if available else RED)
    e.description = f"""**List:** `{list_name}`
**Length:** `{length}` letters
**Available:** `{len(available)}`
**Checked:** `{total_checked}`
**Became Taken:** `{len(removed_taken)}`
**Became Not Taken:** `{len(newly_available)}`
**Errors:** `{errors}`
**Updated:** <t:{now_ts()}:R>

Only words that are **not currently taken by a server** are posted here."""
    if available:
        preview = ", ".join(available[:80])
        if len(available) > 80:
            preview += f", ... +{len(available) - 80} more"
        e.add_field(name="Copyable Preview", value="```txt\\n" + preview + "\\n```", inline=False)
        e.add_field(name="Full List", value="The complete copyable list is attached as a `.txt` file.", inline=False)
    else:
        e.add_field(name="Available Vanities", value="None available for this length right now.", inline=False)
    e.set_footer(text="Auto-filtered: taken/claimed invites are removed before posting.")
    return e

def checker_summary_embed(list_name: str, codes: List[str], available: List[str], taken: List[str], errors: List[str], removed_taken: List[str], newly_available: List[str]) -> discord.Embed:
    e = make_embed("📡 Vanity Checker Results", color=BLUE)
    e.description = f"""**List:** `{list_name}`
**Total Checked:** `{len(codes)}`
**Available / Sent To Hunters:** `{len(available)}`
**Taken / Removed:** `{len(taken)}`
**Errors / Uncertain:** `{len(errors)}`
**Removed Since Last Run:** `{len(removed_taken)}`
**Became Not Taken:** `{len(newly_available)}`
**Updated:** <t:{now_ts()}:R>"""
    if removed_taken:
        txt = ", ".join(f"`{x}`" for x in removed_taken[:40])
        if len(removed_taken) > 40:
            txt += f" ... +{len(removed_taken)-40} more"
        e.add_field(name="Removed Because Taken", value=txt, inline=False)
    if newly_available:
        txt = ", ".join(f"`{x}`" for x in newly_available[:40])
        if len(newly_available) > 40:
            txt += f" ... +{len(newly_available)-40} more"
        e.add_field(name="Newly Available", value=txt, inline=False)
    e.set_footer(text="Full results are attached as a text file.")
    return e

async def send_private_check_logs(log_channel, length: int, available: List[str], taken: List[str], removed_taken: List[str], newly_available: List[str], errors: List[str]) -> None:
    if not log_channel:
        return

    lines = []
    for word in available:
        lines.append(f"{length} letters | Available: `discord.gg/{word}`")
    for word in removed_taken:
        lines.append(f"{length} letters | Removed from available file because it is taken now: `discord.gg/{word}`")
    for word in newly_available:
        lines.append(f"{length} letters | Added back to available file: `discord.gg/{word}`")
    for word in taken:
        if word not in removed_taken:
            lines.append(f"{length} letters | Taken: `discord.gg/{word}`")
    for word in errors:
        lines.append(f"{length} letters | Error / uncertain, not sent to hunters: `discord.gg/{word}`")

    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 1900:
            await log_channel.send(chunk)
            chunk = ""
        chunk += line + "\\n"
    if chunk:
        await log_channel.send(chunk)

def change_log_embed(list_name: str, removed_taken: List[str], newly_available: List[str], processed: int, errors: int) -> discord.Embed:
    e = make_embed("🔁 Vanity Checker Change Log", color=BLUE)
    e.description = f"""**List:** `{list_name}`
**Processed:** `{processed}`
**Removed Because Taken:** `{len(removed_taken)}`
**Became Not Taken:** `{len(newly_available)}`
**Errors:** `{errors}`
**Updated:** <t:{now_ts()}:R>"""
    e.add_field(name="Removed Because Taken", value="\\n".join(f"`discord.gg/{x}`" for x in removed_taken[:35]) or "None", inline=False)
    e.add_field(name="Newly Available", value="\\n".join(f"`discord.gg/{x}`" for x in newly_available[:35]) or "None", inline=False)
    return e


async def run_checker(guild: discord.Guild, list_name: str, setup: dict) -> dict:
    codes = list(dict.fromkeys(parse_codes(" ".join(get_lists(guild.id).get(list_name, [])))))
    codes = codes[:CHECKER_MAX_CODES_PER_RUN]

    final_channel_id = setup.get("lists_channel_id") or setup.get("hunters_invalid_channel_id")
    final_channel = bot.get_channel(int(final_channel_id)) or await bot.fetch_channel(int(final_channel_id))

    check_log_channel = None
    if setup.get("check_log_channel_id") or setup.get("private_log_channel_id"):
        channel_id = setup.get("check_log_channel_id") or setup.get("private_log_channel_id")
        try:
            check_log_channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        except Exception:
            check_log_channel = None

    taken_channel = None
    if setup.get("taken_channel_id"):
        try:
            taken_channel = bot.get_channel(int(setup["taken_channel_id"])) or await bot.fetch_channel(int(setup["taken_channel_id"]))
        except Exception:
            taken_channel = None

    available_channel = None
    if setup.get("available_channel_id"):
        try:
            available_channel = bot.get_channel(int(setup["available_channel_id"])) or await bot.fetch_channel(int(setup["available_channel_id"]))
        except Exception:
            available_channel = None

    change_channel = None
    if setup.get("change_log_channel_id"):
        try:
            change_channel = bot.get_channel(int(setup["change_log_channel_id"])) or await bot.fetch_channel(int(setup["change_log_channel_id"]))
        except Exception:
            change_channel = None

    delay = float(setup.get("delay_seconds", CHECK_DELAY_SECONDS))
    state = get_checker_state()
    key = f"{guild.id}:{list_name}"

    previous_available = set(state.get(key, {}).get("available", state.get(key, {}).get("invalid", [])))
    previous_taken = set(state.get(key, {}).get("taken", state.get(key, {}).get("valid", [])))
    previous_errors = set(state.get(key, {}).get("errors", []))

    available_set, taken_set, error_set = set(), set(), set()

    checker_runtime.update({
        "running": True,
        "stop_requested": False,
        "label": list_name,
        "total": len(codes),
        "current": 0,
    })

    await checker_send_line(check_log_channel, f"Starting checker for `{list_name}` — `{len(codes)}` words. This may be slow to avoid rate limits.")

    try:
        for idx, word in enumerate(codes, start=1):
            checker_runtime["current"] = idx
            if checker_runtime.get("stop_requested"):
                await checker_send_line(check_log_channel, f"Stopped early at `{idx}/{len(codes)}`.")
                break

            link = f"discord.gg/{word}"
            await checker_send_line(check_log_channel, f"[{idx}/{len(codes)}] Checking: `{link}`")

            result, payload = await safe_fetch_invite_for_checker(word)

            if result == "available":
                available_set.add(word)
                await checker_send_line(available_channel, f"`{link}`")
            elif result == "taken":
                taken_set.add(word)
                await checker_send_line(taken_channel, f"`{link}`")
            elif result == "stopped":
                await checker_send_line(check_log_channel, f"Stopped while checking `{link}`.")
                break
            else:
                error_set.add(word)
                await checker_send_line(check_log_channel, f"Error / skipped: `{link}` — `{payload or result}`")

            if idx < len(codes):
                stopped = await checker_sleep(delay)
                if stopped:
                    await checker_send_line(check_log_channel, f"Stopped after `{idx}/{len(codes)}`.")
                    break
    finally:
        checker_runtime["running"] = False

    available = sorted(available_set)
    taken = sorted(taken_set)
    errors = sorted(error_set)

    became_taken = sorted(previous_available & taken_set)
    became_available = sorted((previous_taken | previous_errors) & available_set)

    rewrite_available_files(guild.id, list_name, available)

    ping = f"<@&{setup['ping_role_id']}>" if setup.get("ping_role_id") else None
    allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)

    # Delete old final summary.
    old_summary_id = setup.get("summary_message_id")
    if old_summary_id:
        try:
            old = await final_channel.fetch_message(int(old_summary_id))
            await old.delete()
        except Exception:
            pass

    summary = await final_channel.send(
        content=ping,
        embed=final_available_embed(list_name, codes, available, taken, errors, became_taken, became_available),
        file=full_available_file(list_name, codes, available, taken, errors),
        allowed_mentions=allowed,
    )
    setup["summary_message_id"] = summary.id

    # Delete old per-length final embeds.
    old_ids = setup.get("message_ids", {}) or {}
    for _length, old_id in old_ids.items():
        try:
            old = await final_channel.fetch_message(int(old_id))
            await old.delete()
        except Exception:
            pass

    grouped_available: Dict[int, List[str]] = {}
    for word in available:
        grouped_available.setdefault(len(word), []).append(word)

    new_ids = {}
    for length in sorted(grouped_available.keys()):
        words = grouped_available[length]
        bt = [x for x in became_taken if len(x) == length]
        ba = [x for x in became_available if len(x) == length]
        msg = await final_channel.send(
            embed=hunter_list_embed(list_name, length, words, bt, ba, len(codes), len(errors)),
            file=available_file(list_name, length, words),
        )
        new_ids[str(length)] = msg.id
        await asyncio.sleep(CHECKER_MESSAGE_DELAY_SECONDS)

    if change_channel and (became_taken or became_available or setup.get("always_send_change_log", True)):
        await change_channel.send(embed=change_log_embed(list_name, became_taken, became_available, len(codes), len(errors)))

    await checker_send_line(check_log_channel, f"Finished `{list_name}`. Not taken: `{len(available)}` | Taken: `{len(taken)}` | Errors: `{len(errors)}`.")

    setup["message_ids"] = new_ids
    setup["last_run"] = now_ts()
    setup["next_run"] = now_ts() + int(setup.get("interval_minutes", 60)) * 60

    checkers = get_checkers(guild.id)
    checkers[list_name] = setup
    save_checkers(guild.id, checkers)

    state[key] = {
        "available": available,
        "taken": taken,
        "errors": errors,
        "invalid": available,
        "valid": taken,
        "last_run": now_ts(),
    }
    save_checker_state(state)

    log = {
        "id": make_id(),
        "list_name": list_name,
        "created_ts": now_ts(),
        "processed": len(codes),
        "errors": len(errors),
        "removed_taken": became_taken,
        "newly_available": became_available,
        "became_valid": became_taken,
        "valid_to_invalid": became_available,
    }
    logs = get_change_logs(guild.id)
    logs.append(log)
    save_change_logs(guild.id, logs)
    return log


@tasks.loop(seconds=30)
async def checker_loop():
    if checker_lock.locked():
        return
    all_checkers = load_json(CHECKERS_FILE, {})
    for guild_id, setups in list(all_checkers.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        for name, setup in list(setups.items()):
            if not setup.get("enabled", True):
                continue
            if now_ts() < int(setup.get("next_run", 0)):
                continue
            async with checker_lock:
                try:
                    await run_checker(guild, name, setup)
                except Exception as e:
                    print(f"Checker failed {guild_id}/{name}: {type(e).__name__}: {e}")

@checker_loop.before_loop
async def before_checker_loop():
    await bot.wait_until_ready()


@tasks.loop(minutes=60)
async def leaderboard_loop():
    for guild in bot.guilds:
        cfg = get_config(guild.id)
        if cfg.get("leaderboard_channel_id") and cfg.get("leaderboard_message_id"):
            try:
                await post_or_update_leaderboard(guild)
            except Exception as e:
                print(f"Leaderboard update failed for {guild.id}: {type(e).__name__}: {e}")

@leaderboard_loop.before_loop
async def before_leaderboard_loop():
    await bot.wait_until_ready()


# =========================================================
# SLASH COMMANDS
# =========================================================
@bot.tree.command(name="hunter_panel", description="Open the hunter panel.")
async def hunter_panel_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=hunter_panel_embed(), view=HunterPanel(), ephemeral=True)

@bot.tree.command(name="manager_panel", description="Open the manager panel.")
async def manager_panel_cmd(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.response.send_message(embed=manager_panel_embed(), view=ManagerPanel(), ephemeral=True)

@bot.tree.command(name="post_hunter_panel", description="Post the hunter panel.")
@app_commands.default_permissions(manage_guild=True)
async def post_hunter_panel(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.channel.send(embed=hunter_panel_embed(), view=HunterPanel())
    await interaction.response.send_message("Posted hunter panel.", ephemeral=True)

@bot.tree.command(name="post_manager_panel", description="Post the manager panel.")
@app_commands.default_permissions(manage_guild=True)
async def post_manager_panel(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.channel.send(embed=manager_panel_embed(), view=ManagerPanel())
    await interaction.response.send_message("Posted manager panel.", ephemeral=True)

@bot.tree.command(name="set_payment", description="Set your required preferred payment method.")
async def set_payment(interaction: discord.Interaction, method: str):
    if not interaction.guild:
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    method = method.strip()[:120]
    if not method:
        return await interaction.response.send_message("Enter a payment method.", ephemeral=True)
    payments = get_payments(interaction.guild.id)
    payments[str(interaction.user.id)] = {"method": method, "updated_ts": now_ts()}
    save_payments(interaction.guild.id, payments)
    await interaction.response.send_message(f"✅ Payment saved: `{method}`", ephemeral=True)

@bot.tree.command(name="edit_payment", description="Edit your preferred payment method.")
async def edit_payment(interaction: discord.Interaction, method: str):
    await set_payment(interaction, method)

@bot.tree.command(name="delete_payment", description="Delete your payment method.")
async def delete_payment(interaction: discord.Interaction):
    payments = get_payments(interaction.guild.id)
    existed = payments.pop(str(interaction.user.id), None)
    save_payments(interaction.guild.id, payments)
    await interaction.response.send_message("🗑️ Payment deleted. You cannot submit claims until you set it again." if existed else "No payment was set.", ephemeral=True)

@bot.tree.command(name="view_payments", description="Manager only: view payment methods.")
async def view_payments(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    payments = get_payments(interaction.guild.id)
    e = make_embed("💰 Hunter Payment Preferences", color=GOLD)
    e.description = "\n".join(f"<@{uid}> → `{d.get('method', 'Not set')}`" for uid, d in payments.items()) or "No payments set."
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="claim_stats", description="View claim stats.")
async def claim_stats(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    if target.id != interaction.user.id and not is_manager(interaction.user):
        return await interaction.response.send_message("Only managers can view others' stats.", ephemeral=True)
    await interaction.response.send_message(embed=stats_embed(interaction.guild, target.id), ephemeral=True)

@bot.tree.command(name="payout", description="Manager only: calculate payout split.")
@app_commands.default_permissions(manage_guild=True)
async def payout(interaction: discord.Interaction, hunter: discord.Member, sale_price: float, manager_brought_buyer: bool = False):
    if not await require_manager(interaction):
        return
    if sale_price <= 0:
        return await interaction.response.send_message("Sale price must be higher than 0.", ephemeral=True)
    await interaction.response.send_message(embed=payout_embed_for(interaction.guild, hunter.id, sale_price, manager_brought_buyer), ephemeral=True)

@bot.tree.command(name="payout_info", description="Show payout info.")
async def payout_info_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=payout_info_embed(), ephemeral=True)

@bot.tree.command(name="get_roles", description="Show how roles are obtained.")
async def get_roles_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=roles_info_embed(interaction.guild), ephemeral=True)

@bot.tree.command(name="hunter_info", description="Show hunter guide.")
async def hunter_info(interaction: discord.Interaction):
    await interaction.response.send_message(embed=hunter_guide_embed(), ephemeral=True)

@bot.tree.command(name="manager_info", description="Show manager guide.")
async def manager_info(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.response.send_message(embed=manager_guide_embed(interaction.guild), ephemeral=True)

@bot.tree.command(name="vanity_manager_add_role", description="Allow a role to use manager controls.")
@app_commands.default_permissions(manage_guild=True)
async def vanity_manager_add_role(interaction: discord.Interaction, role: discord.Role):
    if not await require_admin(interaction):
        return
    cfg = get_config(interaction.guild.id)
    roles = [str(x) for x in cfg.get("manager_roles", [])]
    if str(role.id) not in roles:
        roles.append(str(role.id))
    cfg["manager_roles"] = roles
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {role.mention} as a manager role.", ephemeral=True)

@bot.tree.command(name="vanity_manager_add_user", description="Allow a user to use manager controls.")
@app_commands.default_permissions(manage_guild=True)
async def vanity_manager_add_user(interaction: discord.Interaction, user: discord.Member):
    if not await require_admin(interaction):
        return
    cfg = get_config(interaction.guild.id)
    users = [str(x) for x in cfg.get("manager_users", [])]
    if str(user.id) not in users:
        users.append(str(user.id))
    cfg["manager_users"] = users
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {user.mention} as a manager.", ephemeral=True)


@bot.tree.command(name="leaderboard", description="View the current claim leaderboard.")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=leaderboard_embed(interaction.guild), ephemeral=True)

@bot.tree.command(name="post_leaderboard", description="Manager only: post the live claim leaderboard in a channel.")
@app_commands.default_permissions(manage_guild=True)
async def post_leaderboard(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    if not await require_manager(interaction):
        return
    target = channel or interaction.channel
    msg = await post_or_update_leaderboard(interaction.guild, target)
    if msg:
        await interaction.response.send_message(f"Leaderboard posted/updated in {target.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message("Could not post leaderboard. Check channel permissions.", ephemeral=True)

@bot.tree.command(name="leaderboard_update", description="Manager only: force update the posted leaderboard.")
@app_commands.default_permissions(manage_guild=True)
async def leaderboard_update(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    msg = await post_or_update_leaderboard(interaction.guild)
    if msg:
        await interaction.response.send_message("Leaderboard updated.", ephemeral=True)
    else:
        await interaction.response.send_message("No leaderboard channel is set. Use `/post_leaderboard` first.", ephemeral=True)



@bot.tree.command(name="setup_wizard", description="Manager only: guided setup for the vanity bot.")
@app_commands.default_permissions(manage_guild=True)
async def setup_wizard(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    await interaction.response.send_message(embed=setup_wizard_embed(interaction.guild), view=SetupWizardView(), ephemeral=True)



@bot.tree.command(name="checker_status", description="Manager only: see which auto-checker lists are running.")
@app_commands.default_permissions(manage_guild=True)
async def checker_status(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return

    checkers = get_checkers(interaction.guild.id)
    lists = get_lists(interaction.guild.id)

    e = make_embed("📡 Auto Checker Status", color=BLUE)

    if not checkers:
        e.description = "No auto-checker lists are currently set up."
        return await interaction.response.send_message(embed=e, ephemeral=True)

    lines = []
    for name, setup in sorted(checkers.items()):
        enabled = "Enabled" if setup.get("enabled", True) else "Disabled"
        word_count = len(lists.get(name, []))
        invalid_channel = setup.get("lists_channel_id") or setup.get("hunters_invalid_channel_id")
        change_channel = setup.get("change_log_channel_id")
        ping_role = setup.get("ping_role_id")
        interval = int(setup.get("interval_minutes", 90))
        last_run = int(setup.get("last_run", 0) or 0)
        next_run = int(setup.get("next_run", 0) or 0)

        lines.append(
            f"**`{name}`** — `{enabled}`\n"
            f"Words: `{word_count}` • Interval: `{interval}m`\n"
            f"Lists: {f'<#{invalid_channel}>' if invalid_channel else '`not set`'}\n"
            f"Changes: {f'<#{change_channel}>' if change_channel else '`not set`'}\n"
            f"Ping: {f'<@&{ping_role}>' if ping_role else '`none`'}\n"
            f"Last Run: {f'<t:{last_run}:R>' if last_run else '`never`'}\n"
            f"Next Run: {f'<t:{next_run}:R>' if next_run else '`not scheduled`'}"
        )

    e.description = "\n\n".join(lines[:10])
    if len(lines) > 10:
        e.set_footer(text=f"Showing 10 of {len(lines)} checker setups.")

    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="checker_toggle", description="Manager only: enable or disable an auto-checker list.")
@app_commands.default_permissions(manage_guild=True)
async def checker_toggle(interaction: discord.Interaction, list_name: str, enabled: bool):
    if not await require_manager(interaction):
        return

    name = list_name.strip().lower().replace(" ", "-")[:40]
    checkers = get_checkers(interaction.guild.id)

    if name not in checkers:
        return await interaction.response.send_message("No checker setup found for that list.", ephemeral=True)

    checkers[name]["enabled"] = bool(enabled)
    save_checkers(interaction.guild.id, checkers)

    await interaction.response.send_message(
        f"Checker `{name}` is now `{'enabled' if enabled else 'disabled'}`.",
        ephemeral=True
    )



@bot.tree.command(name="set_claim_ping_role", description="Manager only: set the role pinged when claims are posted.")
@app_commands.default_permissions(manage_guild=True)
async def set_claim_ping_role(interaction: discord.Interaction, role: Optional[discord.Role] = None):
    if not await require_manager(interaction):
        return
    cfg = get_config(interaction.guild.id)
    cfg["claim_ping_role_id"] = role.id if role else None
    save_config(interaction.guild.id, cfg)
    if role:
        await interaction.response.send_message(f"Claim posts will now ping {role.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message("Claim post ping role cleared.", ephemeral=True)

@bot.tree.command(name="checker_ping_role", description="Manager only: set the role pinged when a checker list posts.")
@app_commands.default_permissions(manage_guild=True)
async def checker_ping_role(interaction: discord.Interaction, list_name: str, role: Optional[discord.Role] = None):
    if not await require_manager(interaction):
        return
    name = list_name.strip().lower().replace(" ", "-")[:40]
    checkers = get_checkers(interaction.guild.id)
    if name not in checkers:
        return await interaction.response.send_message("No checker setup found for that list.", ephemeral=True)
    checkers[name]["ping_role_id"] = role.id if role else None
    save_checkers(interaction.guild.id, checkers)
    if role:
        await interaction.response.send_message(f"Checker `{name}` will now ping {role.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Checker `{name}` ping role cleared.", ephemeral=True)


@bot.tree.command(name="checker_add_list", description="Add/update a vanity word list.")
@app_commands.default_permissions(manage_guild=True)
async def checker_add_list(interaction: discord.Interaction, list_name: str, words: str):
    if not await require_manager(interaction):
        return
    name = list_name.strip().lower().replace(" ", "-")[:40]
    codes = parse_codes(words)
    if not name or not codes:
        return await interaction.response.send_message("Enter a list name and at least one vanity.", ephemeral=True)
    lists = get_lists(interaction.guild.id)
    merged, seen = [], set()
    for c in lists.get(name, []) + codes:
        if c not in seen:
            merged.append(c)
            seen.add(c)
    lists[name] = merged
    save_lists(interaction.guild.id, lists)
    await interaction.response.send_message(f"Saved `{name}` with `{len(merged)}` unique words.", ephemeral=True)

@bot.tree.command(name="checker_setup", description="Manager only: setup automatic vanity checking channels.")
@app_commands.default_permissions(manage_guild=True)
async def checker_setup(
    interaction: discord.Interaction,
    list_name: str,
    final_hunter_list_channel: discord.TextChannel,
    check_log_channel: discord.TextChannel,
    taken_vanity_channel: discord.TextChannel,
    not_taken_vanity_channel: discord.TextChannel,
    change_log_channel: Optional[discord.TextChannel] = None,
    ping_role: Optional[discord.Role] = None,
    interval_minutes: app_commands.Range[int, 5, 10080] = 60,
    delay_seconds: app_commands.Range[float, 3.0, 120.0] = CHECK_DELAY_SECONDS,
):
    if not await require_manager(interaction):
        return

    name = list_name.strip().lower().replace(" ", "-")[:40]
    if name not in get_lists(interaction.guild.id):
        return await interaction.response.send_message("That list does not exist. Use `/checker_add_list` first.", ephemeral=True)

    checkers = get_checkers(interaction.guild.id)
    old = checkers.get(name, {})

    checkers[name] = {
        "list_name": name,
        "lists_channel_id": final_hunter_list_channel.id,
        "check_log_channel_id": check_log_channel.id,
        "private_log_channel_id": check_log_channel.id,
        "taken_channel_id": taken_vanity_channel.id,
        "available_channel_id": not_taken_vanity_channel.id,
        "change_log_channel_id": change_log_channel.id if change_log_channel else None,
        "ping_role_id": ping_role.id if ping_role else None,
        "interval_minutes": int(interval_minutes),
        "delay_seconds": float(delay_seconds),
        "next_run": now_ts() + int(interval_minutes) * 60,
        "last_run": old.get("last_run", 0),
        "enabled": True,
        "message_ids": old.get("message_ids", {}),
        "summary_message_id": old.get("summary_message_id"),
        "always_send_change_log": True,
    }
    save_checkers(interaction.guild.id, checkers)

    await interaction.response.send_message(
        f"Checker `{name}` setup saved.\n"
        f"Final hunter list: {final_hunter_list_channel.mention}\n"
        f"Checking log: {check_log_channel.mention}\n"
        f"Taken vanities: {taken_vanity_channel.mention}\n"
        f"Not taken vanities: {not_taken_vanity_channel.mention}\n"
        f"Change log: {change_log_channel.mention if change_log_channel else '`not set`'}\n"
        f"Ping: {ping_role.mention if ping_role else '`none`'}\n"
        f"Interval: `{interval_minutes}m`\n"
        f"Delay per invite check: `{delay_seconds}s`",
        ephemeral=True,
    )

@bot.tree.command(name="checker_stop", description="Manager only: safely stop the current checker run.")
@app_commands.default_permissions(manage_guild=True)
async def checker_stop(interaction: discord.Interaction):
    if not await require_manager(interaction):
        return
    if not checker_runtime.get("running"):
        return await interaction.response.send_message("No checker run is active right now.", ephemeral=True)
    checker_runtime["stop_requested"] = True
    await interaction.response.send_message(
        f"Stop requested. Progress: `{checker_runtime.get('current', 0)}/{checker_runtime.get('total', 0)}`.",
        ephemeral=True,
    )


@bot.tree.command(name="checker_run_now", description="Run a checker list now.")
@app_commands.default_permissions(manage_guild=True)
async def checker_run_now(interaction: discord.Interaction, list_name: str):
    if not await require_manager(interaction):
        return
    name = list_name.strip().lower().replace(" ", "-")[:40]
    setup = get_checkers(interaction.guild.id).get(name)
    if not setup:
        return await interaction.response.send_message("No setup found for that list.", ephemeral=True)
    await interaction.response.send_message(f"Running `{name}` now...", ephemeral=True)
    async with checker_lock:
        log = await run_checker(interaction.guild, name, setup)
    await interaction.followup.send(f"Done. Became Taken: `{len(log.get('removed_taken', []))}` • Became Not Taken: `{len(log.get('newly_available', []))}`", ephemeral=True)

@bot.tree.command(name="checker_change_logs", description="View recent checker changes.")
@app_commands.default_permissions(manage_guild=True)
async def checker_change_logs(interaction: discord.Interaction, list_name: Optional[str] = None, limit: app_commands.Range[int, 1, 10] = 5):
    if not await require_manager(interaction):
        return
    logs = list(reversed(get_change_logs(interaction.guild.id)))
    if list_name:
        wanted = list_name.strip().lower().replace(" ", "-")[:40]
        logs = [x for x in logs if x.get("list_name") == wanted]
    e = make_embed("🔁 Recent Checker Change Logs", color=BLUE)
    if not logs:
        e.description = "No logs yet."
    else:
        for log in logs[:limit]:
            bv = log.get("became_valid", [])
            vti = log.get("valid_to_invalid", [])
            e.add_field(
                name=f"{log.get('list_name')} • <t:{int(log.get('created_ts', 0))}:R>",
                value=f"Invalid → Valid: `{len(bv)}`\n{', '.join('`'+x+'`' for x in bv[:10]) if bv else 'None'}\nValid → Invalid: `{len(vti)}`\n{', '.join('`'+x+'`' for x in vti[:10]) if vti else 'None'}",
                inline=False,
            )
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="manager_applications", description="Owner only: view recent manager applications.")
@app_commands.default_permissions(manage_guild=True)
async def manager_applications(interaction: discord.Interaction, status: Optional[str] = None):
    if not await require_admin(interaction):
        return
    apps = list(reversed(get_manager_apps(interaction.guild.id)))
    if status:
        apps = [x for x in apps if x.get("status") == status.lower()]
    e = make_embed("📝 Manager Applications", color=BLUE)
    if not apps:
        e.description = "No manager applications found."
    else:
        e.description = "\n".join(
            f"`{app['id']}` • <@{app['user_id']}> • `{app.get('status', 'pending')}` • <t:{int(app.get('created_ts', 0))}:R>"
            for app in apps[:20]
        )
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="set_manager_role", description="Owner only: set the role given when manager applications are approved.")
@app_commands.default_permissions(manage_guild=True)
async def set_manager_role(interaction: discord.Interaction, role: discord.Role):
    if not await require_admin(interaction):
        return
    cfg = get_config(interaction.guild.id)
    cfg["manager_role_id"] = role.id
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Manager application approval role set to {role.mention}.", ephemeral=True)

@bot.tree.command(name="set_manager_application_channel", description="Owner only: set where manager applications are sent.")
@app_commands.default_permissions(manage_guild=True)
async def set_manager_application_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await require_admin(interaction):
        return
    cfg = get_config(interaction.guild.id)
    cfg["manager_application_channel_id"] = channel.id
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Manager applications will be sent to {channel.mention}.", ephemeral=True)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    before_roles = {r.id for r in before.roles}
    after_roles = {r.id for r in after.roles}
    if HUNTER_ROLE_ID and HUNTER_ROLE_ID in after_roles and HUNTER_ROLE_ID not in before_roles:
        await safe_dm(after, "🏹 You were given the Hunter role. Start by running `/set_payment`, then use `/hunter_panel`.")
    if MANAGER_ROLE_ID and MANAGER_ROLE_ID in after_roles and MANAGER_ROLE_ID not in before_roles:
        await safe_dm(after, "🛠️ You were given the Manager role. Use `/manager_panel` and read the Manager Guide.")

@bot.event
async def on_ready():
    ensure_data()
    bot.add_view(HunterPanel())
    bot.add_view(ManagerPanel())
    print(f"Logged in as {bot.user}")
    print("RUNNING CLEAN PRODUCTION VANITY BOT WITH PAYOUTS + ROLE INFO")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Slash sync failed: {e}")
    if not checker_loop.is_running():
        checker_loop.start()
    if not leaderboard_loop.is_running():
        leaderboard_loop.start()

if not TOKEN:
    raise RuntimeError("TOKEN is missing. Add TOKEN to your environment variables.")

bot.run(TOKEN)
