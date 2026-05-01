
from __future__ import annotations

import os
import re
import json
import time
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import discord
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
NOTES_FILE = DATA_DIR / "user_notes.json"
LISTS_FILE = DATA_DIR / "vanity_lists.json"
CHECKERS_FILE = DATA_DIR / "checker_setups.json"
CHECKER_STATE_FILE = DATA_DIR / "checker_state.json"
CHECKER_CHANGE_LOG_FILE = DATA_DIR / "checker_change_logs.json"

DEFAULT_CLAIM_COOLDOWN_SECONDS = int(os.getenv("CLAIM_COOLDOWN_SECONDS", "60"))
DEFAULT_MAX_CLAIMS_PER_HOUR = int(os.getenv("MAX_CLAIMS_PER_HOUR", "5"))
DEFAULT_CHECK_DELAY_SECONDS = float(os.getenv("CHECK_DELAY_SECONDS", "3"))

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

# =========================================================
# BASIC HELPERS / STORAGE
# =========================================================
def ensure_data() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

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

def clean_code(text: str) -> str:
    text = str(text or "").strip().lower()
    prefixes = (
        "https://discord.gg/", "http://discord.gg/", "discord.gg/",
        "https://discord.com/invite/", "http://discord.com/invite/", "discord.com/invite/"
    )
    for p in prefixes:
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

def make_embed(title: str, desc: str = "", color: discord.Color = DARK) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.timestamp = discord.utils.utcnow()
    return e

def get_config(guild_id: int) -> dict:
    data = load_json(CONFIG_FILE, {})
    return data.setdefault(gkey(guild_id), {
        "manager_users": [],
        "manager_roles": [],
        "claim_log_channel_id": None,
        "claim_cooldown_seconds": DEFAULT_CLAIM_COOLDOWN_SECONDS,
        "max_claims_per_hour": DEFAULT_MAX_CLAIMS_PER_HOUR,
        "autoroles": []
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

def get_notes(guild_id: int) -> Dict[str, List[dict]]:
    data = load_json(NOTES_FILE, {})
    return data.setdefault(gkey(guild_id), {})

def save_notes(guild_id: int, notes: Dict[str, List[dict]]) -> None:
    data = load_json(NOTES_FILE, {})
    data[gkey(guild_id)] = notes
    save_json(NOTES_FILE, data)

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
    data = load_json(CHECKER_CHANGE_LOG_FILE, {})
    return data.setdefault(gkey(guild_id), [])

def save_change_logs(guild_id: int, logs: List[dict]) -> None:
    data = load_json(CHECKER_CHANGE_LOG_FILE, {})
    data[gkey(guild_id)] = logs[-250:]
    save_json(CHECKER_CHANGE_LOG_FILE, data)

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
# CLAIM SYSTEM
# =========================================================
async def invite_is_valid(code: str) -> Tuple[bool, str]:
    try:
        await bot.fetch_invite(code)
        return True, "valid"
    except discord.NotFound:
        return False, "not_found"
    except discord.Forbidden:
        return False, "forbidden"
    except discord.HTTPException as e:
        return False, f"http_{getattr(e, 'status', 'unknown')}"
    except Exception as e:
        return False, type(e).__name__

async def invite_status(code: str) -> str:
    try:
        await bot.fetch_invite(code)
        return "valid"
    except discord.NotFound:
        return "invalid"
    except discord.Forbidden:
        return "error"
    except discord.HTTPException:
        return "error"
    except Exception:
        return "error"

def find_claim(claims: List[dict], claim_id: str) -> Optional[dict]:
    for claim in claims:
        if str(claim.get("id")) == str(claim_id):
            return claim
    return None

def user_claims(guild_id: int, user_id: int) -> List[dict]:
    return [c for c in get_claims(guild_id) if str(c.get("user_id")) == str(user_id)]

def claim_count_for(guild_id: int, user_id: int) -> int:
    return len(user_claims(guild_id, user_id))

def has_duplicate_claim(guild_id: int, code: str) -> bool:
    code = clean_code(code)
    return any(clean_code(c.get("code", "")) == code for c in get_claims(guild_id))

def claim_spam_check(guild_id: int, user_id: int) -> Tuple[bool, str]:
    cfg = get_config(guild_id)
    cooldown = int(cfg.get("claim_cooldown_seconds", DEFAULT_CLAIM_COOLDOWN_SECONDS))
    max_hour = int(cfg.get("max_claims_per_hour", DEFAULT_MAX_CLAIMS_PER_HOUR))
    claims = sorted(user_claims(guild_id, user_id), key=lambda c: int(c.get("created_ts", 0)), reverse=True)
    if claims:
        last = int(claims[0].get("created_ts", 0))
        if now_ts() - last < cooldown:
            return True, f"Slow down. You can log another claim <t:{last + cooldown}:R>."
    recent = [c for c in claims if now_ts() - int(c.get("created_ts", 0)) <= 3600]
    if len(recent) >= max_hour:
        return True, f"You reached the limit of `{max_hour}` claims per hour."
    return False, ""

def claim_embed(guild: discord.Guild, claim: dict) -> discord.Embed:
    status = claim.get("status", "pending")
    color = GREEN if status == "approved" else RED if status == "denied" else GOLD
    e = make_embed("🏷️ Vanity Claim", color=color)
    e.description = (
        f"**Vanity:** `discord.gg/{clean_code(claim.get('code'))}`\n"
        f"**Hunter:** <@{claim.get('user_id')}>\n"
        f"**Status:** `{status}`\n"
        f"**Logged:** <t:{int(claim.get('created_ts', now_ts()))}:R>"
    )
    if float(claim.get("value") or 0) > 0:
        e.add_field(name="Value", value=f"`{money(float(claim.get('value', 0)))}`", inline=True)
    if claim.get("buyer"):
        e.add_field(name="Buyer", value=str(claim.get("buyer"))[:1024], inline=True)
    e.add_field(name="Notes", value=(claim.get("notes") or "No notes.")[:1024], inline=False)
    if claim.get("manager_notes"):
        e.add_field(name="Manager Notes", value=str(claim.get("manager_notes"))[:1024], inline=False)
    e.set_footer(text=f"Claim ID: {claim.get('id')} • {guild.name}")
    return e

async def post_or_replace_claim_embed(guild: discord.Guild, claim: dict) -> None:
    cfg = get_config(guild.id)
    channel_id = cfg.get("claim_log_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return

    old_id = claim.get("message_id")
    if old_id:
        try:
            old_msg = await channel.fetch_message(int(old_id))
            await old_msg.delete()
        except Exception:
            pass

    try:
        msg = await channel.send(embed=claim_embed(guild, claim))
        claim["message_id"] = msg.id
        claims = get_claims(guild.id)
        existing = find_claim(claims, str(claim["id"]))
        if existing:
            existing.update(claim)
            save_claims(guild.id, claims)
    except Exception:
        pass

async def delete_claim_message(guild: discord.Guild, claim: dict) -> None:
    cfg = get_config(guild.id)
    channel_id = cfg.get("claim_log_channel_id")
    if not channel_id or not claim.get("message_id"):
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return
    try:
        msg = await channel.fetch_message(int(claim["message_id"]))
        await msg.delete()
    except Exception:
        pass

async def apply_autoroles(guild: discord.Guild, member: discord.Member) -> None:
    cfg = get_config(guild.id)
    total = claim_count_for(guild.id, member.id)
    for rule in sorted(cfg.get("autoroles", []), key=lambda x: int(x.get("claims_required", 0))):
        role = guild.get_role(int(rule.get("role_id", 0)))
        req = int(rule.get("claims_required", 0))
        if role and total >= req and role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Reached {req} vanity claims")
            except Exception:
                pass

def stats_embed(guild: discord.Guild, user_id: int) -> discord.Embed:
    claims = sorted(user_claims(guild.id, user_id), key=lambda c: int(c.get("created_ts", 0)), reverse=True)
    e = make_embed("📊 Hunter Stats", f"Stats for <@{user_id}>", PURPLE)
    e.add_field(name="Total Claims", value=f"`{len(claims)}`", inline=True)
    e.add_field(name="Approved", value=f"`{sum(1 for c in claims if c.get('status') == 'approved')}`", inline=True)
    e.add_field(name="Total Value", value=f"`{money(sum(float(c.get('value') or 0) for c in claims))}`", inline=True)
    lines = [f"`discord.gg/{clean_code(c.get('code'))}` • `{c.get('status', 'pending')}` • ID `{c.get('id')}`" for c in claims[:15]]
    e.add_field(name="Claimed Vanities", value="\n".join(lines) if lines else "No claims yet.", inline=False)
    return e

def autoroles_embed(guild: discord.Guild) -> discord.Embed:
    cfg = get_config(guild.id)
    e = make_embed("🎖️ Current Claim Autoroles", color=BLUE)
    rules = sorted(cfg.get("autoroles", []), key=lambda x: int(x.get("claims_required", 0)))
    if not rules:
        e.description = "No claim autoroles are set yet."
    else:
        lines = []
        for r in rules:
            role = guild.get_role(int(r.get("role_id", 0)))
            role_text = role.mention if role else f"`Missing role {r.get('role_id')}`"
            lines.append(f"{role_text} — `{int(r.get('claims_required', 0))}` claims")
        e.description = "\n".join(lines)
    return e

# =========================================================
# MODALS
# =========================================================
class LogClaimModal(discord.ui.Modal, title="Log Vanity Claim"):
    code = discord.ui.TextInput(label="Vanity code/link", placeholder="example or discord.gg/example", max_length=80)
    value = discord.ui.TextInput(label="Estimated value (optional)", placeholder="25 or 25.50", required=False, max_length=20)
    notes = discord.ui.TextInput(label="Notes (optional)", style=discord.TextStyle.paragraph, required=False, max_length=800)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This only works in a server.", ephemeral=True)

        code = clean_code(str(self.code))
        if not code:
            return await interaction.response.send_message("Enter a valid vanity code.", ephemeral=True)

        valid, _ = await invite_is_valid(code)
        if not valid:
            return await interaction.response.send_message(
                f"`discord.gg/{code}` is not currently valid, so it cannot be claimed.",
                ephemeral=True
            )

        if has_duplicate_claim(interaction.guild.id, code):
            return await interaction.response.send_message("That vanity is already logged.", ephemeral=True)

        spam, spam_reason = claim_spam_check(interaction.guild.id, interaction.user.id)
        if spam and not is_manager(interaction.user):
            return await interaction.response.send_message(spam_reason, ephemeral=True)

        try:
            value = round(max(float(str(self.value).replace("$", "").replace(",", "").strip() or 0), 0), 2)
        except Exception:
            value = 0.0

        claim = {
            "id": make_id(),
            "code": code,
            "user_id": interaction.user.id,
            "created_by": interaction.user.id,
            "created_ts": now_ts(),
            "updated_ts": now_ts(),
            "status": "pending",
            "value": value,
            "buyer": "",
            "notes": str(self.notes).strip(),
            "manager_notes": "",
            "message_id": None,
        }

        claims = get_claims(interaction.guild.id)
        claims.append(claim)
        save_claims(interaction.guild.id, claims)

        await post_or_replace_claim_embed(interaction.guild, claim)
        await apply_autoroles(interaction.guild, interaction.user)
        await interaction.response.send_message(f"Logged claim `discord.gg/{code}` with Claim ID `{claim['id']}`.", ephemeral=True)

class ManagerEditClaimModal(discord.ui.Modal, title="Manager Edit Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", max_length=40)
    code = discord.ui.TextInput(label="New vanity code/link (optional)", required=False, max_length=80)
    user_id = discord.ui.TextInput(label="New hunter user ID/mention (optional)", required=False, max_length=40)
    value = discord.ui.TextInput(label="Value (optional)", required=False, max_length=20)
    notes = discord.ui.TextInput(label="Public notes (optional)", style=discord.TextStyle.paragraph, required=False, max_length=800)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)

        if str(self.code).strip():
            code = clean_code(str(self.code))
            valid, _ = await invite_is_valid(code)
            if not valid:
                return await interaction.response.send_message("That vanity is not currently valid, so it cannot be set as a claim.", ephemeral=True)
            if any(c.get("id") != claim.get("id") and clean_code(c.get("code")) == code for c in claims):
                return await interaction.response.send_message("Another claim already uses that vanity.", ephemeral=True)
            claim["code"] = code

        uid = parse_snowflake(str(self.user_id))
        if uid:
            claim["user_id"] = uid

        if str(self.value).strip():
            try:
                claim["value"] = round(max(float(str(self.value).replace("$", "").replace(",", "").strip()), 0), 2)
            except Exception:
                return await interaction.response.send_message("Invalid value.", ephemeral=True)

        if str(self.notes).strip():
            claim["notes"] = str(self.notes).strip()

        claim["updated_ts"] = now_ts()
        claim["last_manager_id"] = interaction.user.id
        save_claims(interaction.guild.id, claims)
        await post_or_replace_claim_embed(interaction.guild, claim)
        member = interaction.guild.get_member(int(claim["user_id"]))
        if member:
            await apply_autoroles(interaction.guild, member)
        await interaction.response.send_message(f"Updated Claim ID `{claim['id']}`.", ephemeral=True)

class ManagerStatusClaimModal(discord.ui.Modal, title="Set Claim Status / Buyer"):
    claim_id = discord.ui.TextInput(label="Claim ID", max_length=40)
    status = discord.ui.TextInput(label="Status", placeholder="pending, approved, denied, sold, paid", max_length=20)
    buyer = discord.ui.TextInput(label="Buyer info (optional)", required=False, max_length=200)
    manager_notes = discord.ui.TextInput(label="Manager notes (optional)", style=discord.TextStyle.paragraph, required=False, max_length=800)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)

        status = str(self.status).strip().lower()
        allowed = {"pending", "approved", "denied", "sold", "paid"}
        if status not in allowed:
            return await interaction.response.send_message("Status must be pending, approved, denied, sold, or paid.", ephemeral=True)

        claim["status"] = status
        if str(self.buyer).strip():
            claim["buyer"] = str(self.buyer).strip()
        if str(self.manager_notes).strip():
            claim["manager_notes"] = str(self.manager_notes).strip()
        claim["updated_ts"] = now_ts()
        claim["last_manager_id"] = interaction.user.id
        save_claims(interaction.guild.id, claims)
        await post_or_replace_claim_embed(interaction.guild, claim)
        await interaction.response.send_message(f"Updated Claim ID `{claim['id']}` to `{status}`.", ephemeral=True)

class ManagerDeleteClaimModal(discord.ui.Modal, title="Delete Claim"):
    claim_id = discord.ui.TextInput(label="Claim ID", max_length=40)
    confirm = discord.ui.TextInput(label="Type DELETE to confirm", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        if str(self.confirm).strip().upper() != "DELETE":
            return await interaction.response.send_message("Cancelled.", ephemeral=True)

        claims = get_claims(interaction.guild.id)
        claim = find_claim(claims, str(self.claim_id).strip())
        if not claim:
            return await interaction.response.send_message("Claim ID not found.", ephemeral=True)

        await delete_claim_message(interaction.guild, claim)
        claims = [c for c in claims if c.get("id") != claim.get("id")]
        save_claims(interaction.guild.id, claims)
        await interaction.response.send_message(f"Deleted Claim ID `{claim['id']}`.", ephemeral=True)

class AddUserNoteModal(discord.ui.Modal, title="Add User Note"):
    user_id = discord.ui.TextInput(label="User ID or mention", max_length=40)
    note = discord.ui.TextInput(label="Note", style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        uid = parse_snowflake(str(self.user_id))
        if not uid:
            return await interaction.response.send_message("Enter a valid user ID or mention.", ephemeral=True)
        notes = get_notes(interaction.guild.id)
        items = notes.setdefault(str(uid), [])
        items.append({"id": make_id(), "note": str(self.note).strip(), "created_by": interaction.user.id, "created_ts": now_ts()})
        save_notes(interaction.guild.id, notes)
        await interaction.response.send_message(f"Added note for <@{uid}>.", ephemeral=True)

class ViewUserNotesModal(discord.ui.Modal, title="View User Notes"):
    user_id = discord.ui.TextInput(label="User ID or mention", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        uid = parse_snowflake(str(self.user_id))
        if not uid:
            return await interaction.response.send_message("Enter a valid user ID or mention.", ephemeral=True)
        items = get_notes(interaction.guild.id).get(str(uid), [])
        e = make_embed("📝 User Notes", f"Notes for <@{uid}>", BLUE)
        if not items:
            e.add_field(name="Notes", value="No notes found.", inline=False)
        else:
            lines = []
            for n in sorted(items, key=lambda x: int(x.get("created_ts", 0)), reverse=True)[:15]:
                lines.append(f"**ID `{n['id']}`** • by <@{n['created_by']}> • <t:{int(n['created_ts'])}:R>\n{n['note'][:300]}")
            e.add_field(name="Recent Notes", value="\n\n".join(lines)[:4000], inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

class DeleteUserNoteModal(discord.ui.Modal, title="Delete User Note"):
    user_id = discord.ui.TextInput(label="User ID or mention", max_length=40)
    note_id = discord.ui.TextInput(label="Note ID", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        uid = parse_snowflake(str(self.user_id))
        if not uid:
            return await interaction.response.send_message("Enter a valid user ID or mention.", ephemeral=True)
        notes = get_notes(interaction.guild.id)
        items = notes.get(str(uid), [])
        before = len(items)
        notes[str(uid)] = [n for n in items if str(n.get("id")) != str(self.note_id).strip()]
        save_notes(interaction.guild.id, notes)
        await interaction.response.send_message("Deleted note." if len(notes[str(uid)]) < before else "Note ID not found.", ephemeral=True)

class ManagerConfigModal(discord.ui.Modal, title="Manager Config"):
    claim_log_channel_id = discord.ui.TextInput(label="Claim log channel ID/mention", required=False, max_length=40)
    cooldown_seconds = discord.ui.TextInput(label="Claim cooldown seconds", required=False, placeholder="60", max_length=10)
    max_per_hour = discord.ui.TextInput(label="Max claims per hour", required=False, placeholder="5", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        cfg = get_config(interaction.guild.id)
        cid = parse_snowflake(str(self.claim_log_channel_id))
        if cid:
            cfg["claim_log_channel_id"] = cid
        if str(self.cooldown_seconds).strip():
            cfg["claim_cooldown_seconds"] = max(0, int(str(self.cooldown_seconds).strip()))
        if str(self.max_per_hour).strip():
            cfg["max_claims_per_hour"] = max(1, int(str(self.max_per_hour).strip()))
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message("Settings saved.", ephemeral=True)

class AddAutoroleModal(discord.ui.Modal, title="Add Claim Autorole"):
    role_id = discord.ui.TextInput(label="Role ID or mention", max_length=40)
    claims_required = discord.ui.TextInput(label="Claims required", placeholder="10", max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
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
        cfg["autoroles"] = [r for r in cfg.get("autoroles", []) if str(r.get("role_id")) != str(rid)]
        cfg["autoroles"].append({"role_id": rid, "claims_required": req})
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Autorole saved: <@&{rid}> at `{req}` claims.", ephemeral=True)

class DeleteAutoroleModal(discord.ui.Modal, title="Delete Claim Autorole"):
    role_id = discord.ui.TextInput(label="Role ID or mention", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_manager(interaction):
            return
        rid = parse_snowflake(str(self.role_id))
        if not rid:
            return await interaction.response.send_message("Enter a valid role ID or mention.", ephemeral=True)
        cfg = get_config(interaction.guild.id)
        before = len(cfg.get("autoroles", []))
        cfg["autoroles"] = [r for r in cfg.get("autoroles", []) if str(r.get("role_id")) != str(rid)]
        save_config(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Deleted autorole <@&{rid}>." if len(cfg["autoroles"]) < before else "That autorole rule was not found.", ephemeral=True)

# =========================================================
# PANEL VIEWS
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

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, custom_id="hunter_help")
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = make_embed("📘 Hunter Guide", color=PURPLE)
        e.description = (
            "Only log a claim if the vanity currently works on Discord and you successfully pulled it.\n\n"
            "The bot checks the vanity before saving your claim. If it is still invalid, the claim is blocked."
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

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
        e.description = "\n".join(f"`{c['id']}` • <@{c['user_id']}> • `discord.gg/{clean_code(c.get('code'))}` • `{c.get('status', 'pending')}`" for c in claims) or "No claims yet."
        await interaction.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(label="Add Note", style=discord.ButtonStyle.secondary, custom_id="manager_add_note")
    async def add_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(AddUserNoteModal())

    @discord.ui.button(label="View Notes", style=discord.ButtonStyle.secondary, custom_id="manager_view_notes")
    async def view_notes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(ViewUserNotesModal())

    @discord.ui.button(label="Delete Note", style=discord.ButtonStyle.danger, custom_id="manager_delete_note")
    async def delete_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_manager(interaction): return
        await interaction.response.send_modal(DeleteUserNoteModal())

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

def hunter_panel_embed() -> discord.Embed:
    e = make_embed("🏹 Vanity Hunter Panel", color=PURPLE)
    e.description = (
        "Use this panel to log successful vanity pulls and view your stats.\n\n"
        "**Claims are only accepted if the vanity is currently valid on Discord.**"
    )
    return e

def manager_panel_embed() -> discord.Embed:
    e = make_embed("🛠️ Manager Control Panel", color=GOLD)
    e.description = (
        "Control claims, user notes, buyer/status info, claim settings, and claim-count autoroles.\n\n"
        "Managers are responsible for reviewing claims, keeping logs clean, and finding buyers."
    )
    return e

# =========================================================
# CHECKER CHANGE LOGS
# =========================================================
def list_embed_by_length(list_name: str, length: int, invalids: List[str], became_valid_for_length: List[str], valid_to_invalid_for_length: List[str], processed: int, errors: int) -> discord.Embed:
    e = make_embed(f"{length} lettered vanities", color=RED if invalids else GREEN)
    e.description = (
        f"**List:** `{list_name}`\n"
        f"**Processed:** `{processed}`\n"
        f"**Currently Invalid:** `{len(invalids)}`\n"
        f"**Invalid → Valid:** `{len(became_valid_for_length)}`\n"
        f"**Valid → Invalid:** `{len(valid_to_invalid_for_length)}`\n"
        f"**Errors:** `{errors}`\n"
        f"**Updated:** <t:{now_ts()}:R>"
    )
    if invalids:
        lines = [f"`discord.gg/{c}`" for c in invalids[:40]]
        if len(invalids) > 40:
            lines.append(f"...and `{len(invalids) - 40}` more")
        e.add_field(name="Fresh Invalid List", value="\n".join(lines), inline=False)
    else:
        e.add_field(name="Fresh Invalid List", value="No invalid vanities in this length right now.", inline=False)
    return e

def change_log_embed(list_name: str, became_valid: List[str], valid_to_invalid: List[str], processed: int, errors: int) -> discord.Embed:
    e = make_embed("🔁 Vanity List Change Log", color=BLUE)
    e.description = (
        f"**List:** `{list_name}`\n"
        f"**Processed:** `{processed}`\n"
        f"**Invalid → Valid:** `{len(became_valid)}`\n"
        f"**Valid → Invalid:** `{len(valid_to_invalid)}`\n"
        f"**Errors:** `{errors}`\n"
        f"**Updated:** <t:{now_ts()}:R>"
    )

    if became_valid:
        lines = [f"`discord.gg/{c}`" for c in became_valid[:35]]
        if len(became_valid) > 35:
            lines.append(f"...and `{len(became_valid) - 35}` more")
        e.add_field(name="✅ Was Invalid, Now Valid", value="\n".join(lines), inline=False)
    else:
        e.add_field(name="✅ Was Invalid, Now Valid", value="None this update.", inline=False)

    if valid_to_invalid:
        lines = [f"`discord.gg/{c}`" for c in valid_to_invalid[:35]]
        if len(valid_to_invalid) > 35:
            lines.append(f"...and `{len(valid_to_invalid) - 35}` more")
        e.add_field(name="🔥 Was Valid, Now Invalid", value="\n".join(lines), inline=False)
    else:
        e.add_field(name="🔥 Was Valid, Now Invalid", value="None this update.", inline=False)

    return e

async def run_two_server_check(guild: discord.Guild, list_name: str, setup: dict) -> dict:
    lists = get_lists(guild.id)
    codes = list(dict.fromkeys(parse_codes(" ".join(lists.get(list_name, [])))))

    invalid_channel_id = int(setup["hunters_invalid_channel_id"])
    valid_channel_id = setup.get("hunters_valid_channel_id")
    change_log_channel_id = setup.get("change_log_channel_id")
    ping_role_id = setup.get("ping_role_id")
    delay = float(setup.get("delay_seconds", DEFAULT_CHECK_DELAY_SECONDS))

    invalid_channel = bot.get_channel(invalid_channel_id) or await bot.fetch_channel(invalid_channel_id)
    valid_channel = None
    if valid_channel_id:
        try:
            valid_channel = bot.get_channel(int(valid_channel_id)) or await bot.fetch_channel(int(valid_channel_id))
        except Exception:
            valid_channel = None

    change_log_channel = None
    if change_log_channel_id:
        try:
            change_log_channel = bot.get_channel(int(change_log_channel_id)) or await bot.fetch_channel(int(change_log_channel_id))
        except Exception:
            change_log_channel = None

    state = get_checker_state()
    s_key = f"{guild.id}:{list_name}"
    prev_invalid = set(state.get(s_key, {}).get("invalid", []))
    prev_valid = set(state.get(s_key, {}).get("valid", []))

    current_invalid = set()
    current_valid = set()
    errors = 0

    for idx, code in enumerate(codes, start=1):
        status = await invite_status(code)
        if status == "invalid":
            current_invalid.add(code)
        elif status == "valid":
            current_valid.add(code)
        else:
            errors += 1
        if idx < len(codes):
            await asyncio.sleep(delay)

    became_valid = sorted(prev_invalid & current_valid)
    valid_to_invalid = sorted(prev_valid & current_invalid)

    grouped: Dict[int, List[str]] = {}
    for code in sorted(current_invalid):
        grouped.setdefault(len(code), []).append(code)

    content = f"<@&{ping_role_id}>" if ping_role_id else None
    allowed = discord.AllowedMentions(roles=True, users=False, everyone=False)

    old_message_ids = setup.get("message_ids", {}) or {}
    new_message_ids = {}
    lengths = sorted(grouped.keys())

    for i, length in enumerate(lengths):
        old_id = old_message_ids.get(str(length))
        invalids = grouped[length]
        bv_len = [c for c in became_valid if len(c) == length]
        vti_len = [c for c in valid_to_invalid if len(c) == length]
        e = list_embed_by_length(list_name, length, invalids, bv_len, vti_len, len(codes), errors)

        if old_id:
            try:
                old_msg = await invalid_channel.fetch_message(int(old_id))
                await old_msg.delete()
            except Exception:
                pass

        msg = await invalid_channel.send(content=content if i == 0 else None, embed=e, allowed_mentions=allowed)
        new_message_ids[str(length)] = msg.id

    # Delete old embeds for lengths that no longer have invalids.
    for length, old_id in old_message_ids.items():
        if length not in new_message_ids:
            try:
                old_msg = await invalid_channel.fetch_message(int(old_id))
                await old_msg.delete()
            except Exception:
                pass

    if valid_channel:
        valid_summary = make_embed("✅ Vanity Check Valid Summary", color=GREEN)
        valid_summary.description = (
            f"**List:** `{list_name}`\n"
            f"**Processed:** `{len(codes)}`\n"
            f"**Currently Valid:** `{len(current_valid)}`\n"
            f"**Invalid → Valid:** `{len(became_valid)}`\n"
            f"**Valid → Invalid:** `{len(valid_to_invalid)}`\n"
            f"**Updated:** <t:{now_ts()}:R>"
        )
        if became_valid:
            valid_summary.add_field(name="Invalid → Valid", value="\n".join(f"`discord.gg/{c}`" for c in became_valid[:35]), inline=False)

        old_valid_id = setup.get("valid_message_id")
        if old_valid_id:
            try:
                old_msg = await valid_channel.fetch_message(int(old_valid_id))
                await old_msg.delete()
            except Exception:
                pass
        msg = await valid_channel.send(embed=valid_summary)
        setup["valid_message_id"] = msg.id

    change_log_message_id = None
    if change_log_channel and (became_valid or valid_to_invalid or setup.get("always_send_change_log", True)):
        e = change_log_embed(list_name, became_valid, valid_to_invalid, len(codes), errors)
        msg = await change_log_channel.send(embed=e)
        change_log_message_id = msg.id

    setup["message_ids"] = new_message_ids
    setup["last_run"] = now_ts()
    setup["next_run"] = now_ts() + int(setup.get("interval_minutes", 60)) * 60

    checkers = get_checkers(guild.id)
    checkers[list_name] = setup
    save_checkers(guild.id, checkers)

    state[s_key] = {
        "invalid": sorted(current_invalid),
        "valid": sorted(current_valid),
        "last_run": now_ts(),
    }
    save_checker_state(state)

    log_entry = {
        "id": make_id(),
        "list_name": list_name,
        "created_ts": now_ts(),
        "processed": len(codes),
        "errors": errors,
        "became_valid": became_valid,
        "valid_to_invalid": valid_to_invalid,
        "change_log_channel_id": change_log_channel_id,
        "change_log_message_id": change_log_message_id,
    }
    logs = get_change_logs(guild.id)
    logs.append(log_entry)
    save_change_logs(guild.id, logs)

    return log_entry

@tasks.loop(seconds=30)
async def checker_loop():
    if checker_lock.locked():
        return
    all_checkers = load_json(CHECKERS_FILE, {})
    for guild_id, checkers in list(all_checkers.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        for name, setup in list(checkers.items()):
            if not setup.get("enabled", True):
                continue
            if now_ts() < int(setup.get("next_run", 0)):
                continue
            async with checker_lock:
                try:
                    await run_two_server_check(guild, name, setup)
                except Exception as e:
                    print(f"Checker failed for {guild_id}/{name}: {type(e).__name__}: {e}")

@checker_loop.before_loop
async def before_checker_loop():
    await bot.wait_until_ready()

# =========================================================
# SLASH COMMANDS
# =========================================================
@bot.tree.command(name="hunter_panel", description="Open the hunter claim panel.")
async def hunter_panel_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=hunter_panel_embed(), view=HunterPanel(), ephemeral=True)

@bot.tree.command(name="manager_panel", description="Open the manager control panel.")
async def manager_panel_cmd(interaction: discord.Interaction):
    if not await require_manager(interaction): return
    await interaction.response.send_message(embed=manager_panel_embed(), view=ManagerPanel(), ephemeral=True)

@bot.tree.command(name="post_hunter_panel", description="Post the hunter panel in this channel.")
@app_commands.default_permissions(manage_guild=True)
async def post_hunter_panel(interaction: discord.Interaction):
    if not await require_manager(interaction): return
    await interaction.channel.send(embed=hunter_panel_embed(), view=HunterPanel())
    await interaction.response.send_message("Posted hunter panel.", ephemeral=True)

@bot.tree.command(name="post_manager_panel", description="Post the manager panel in this channel.")
@app_commands.default_permissions(manage_guild=True)
async def post_manager_panel(interaction: discord.Interaction):
    if not await require_manager(interaction): return
    await interaction.channel.send(embed=manager_panel_embed(), view=ManagerPanel())
    await interaction.response.send_message("Posted manager panel.", ephemeral=True)

@bot.tree.command(name="help", description="Show bot help.")
async def help_cmd(interaction: discord.Interaction):
    e = make_embed("📚 Vanity Bot Help", color=PURPLE)
    e.description = (
        "This bot is claim-only.\n\n"
        "Hunters use the Hunter Panel to log claims.\n"
        "Managers use the Manager Panel to edit/delete claims, manage notes, and control autoroles.\n\n"
        "**Claims are blocked unless the entered vanity is currently valid.**\n"
        "Checker change logs can be sent to a separate channel using `/two_server_list_setup`."
    )
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="claim_stats", description="View claim stats for yourself or another user.")
async def claim_stats(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    if target.id != interaction.user.id and not is_manager(interaction.user):
        return await interaction.response.send_message("Only managers can view other users' stats.", ephemeral=True)
    await interaction.response.send_message(embed=stats_embed(interaction.guild, target.id), ephemeral=True)

@bot.tree.command(name="vanity_manager_add_role", description="Allow a role to use manager controls.")
@app_commands.default_permissions(manage_guild=True)
async def vanity_manager_add_role(interaction: discord.Interaction, role: discord.Role):
    if not await require_admin(interaction): return
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
    if not await require_admin(interaction): return
    cfg = get_config(interaction.guild.id)
    users = [str(x) for x in cfg.get("manager_users", [])]
    if str(user.id) not in users:
        users.append(str(user.id))
    cfg["manager_users"] = users
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {user.mention} as a manager.", ephemeral=True)

@bot.tree.command(name="checker_add_list", description="Add/update a vanity word list in the private checker server.")
@app_commands.default_permissions(manage_guild=True)
async def checker_add_list(interaction: discord.Interaction, list_name: str, words: str):
    if not await require_manager(interaction): return
    name = list_name.strip().lower().replace(" ", "-")[:40]
    codes = parse_codes(words)
    if not name or not codes:
        return await interaction.response.send_message("Enter a list name and at least one vanity.", ephemeral=True)
    lists = get_lists(interaction.guild.id)
    merged, seen = [], set()
    for code in lists.get(name, []) + codes:
        if code not in seen:
            merged.append(code)
            seen.add(code)
    lists[name] = merged
    save_lists(interaction.guild.id, lists)
    await interaction.response.send_message(f"Saved list `{name}` with `{len(merged)}` unique vanities.", ephemeral=True)

@bot.tree.command(name="two_server_list_setup", description="Send checked list updates to your hunters server.")
@app_commands.default_permissions(manage_guild=True)
async def two_server_list_setup(
    interaction: discord.Interaction,
    list_name: str,
    hunters_invalid_channel_id: str,
    hunters_valid_channel_id: Optional[str] = None,
    change_log_channel_id: Optional[str] = None,
    ping_role_id: Optional[str] = None,
    interval_minutes: app_commands.Range[int, 5, 10080] = 60,
    delay_seconds: app_commands.Range[float, 1.0, 30.0] = DEFAULT_CHECK_DELAY_SECONDS,
    always_send_change_log: bool = True,
):
    if not await require_manager(interaction): return
    name = list_name.strip().lower().replace(" ", "-")[:40]
    lists = get_lists(interaction.guild.id)
    if name not in lists:
        return await interaction.response.send_message("That list does not exist. Use `/checker_add_list` first.", ephemeral=True)

    invalid_id = parse_snowflake(hunters_invalid_channel_id)
    valid_id = parse_snowflake(hunters_valid_channel_id)
    change_id = parse_snowflake(change_log_channel_id)
    ping_id = parse_snowflake(ping_role_id)

    if not invalid_id:
        return await interaction.response.send_message("Enter the Vanity Hunters invalid/update channel ID.", ephemeral=True)

    try:
        bot.get_channel(invalid_id) or await bot.fetch_channel(invalid_id)
    except Exception:
        return await interaction.response.send_message("I cannot access the invalid/update channel.", ephemeral=True)

    if change_id:
        try:
            bot.get_channel(change_id) or await bot.fetch_channel(change_id)
        except Exception:
            return await interaction.response.send_message("I cannot access the change-log channel.", ephemeral=True)

    checkers = get_checkers(interaction.guild.id)
    old = checkers.get(name, {})
    checkers[name] = {
        "list_name": name,
        "hunters_invalid_channel_id": invalid_id,
        "hunters_valid_channel_id": valid_id,
        "change_log_channel_id": change_id,
        "ping_role_id": ping_id,
        "interval_minutes": int(interval_minutes),
        "delay_seconds": float(delay_seconds),
        "next_run": now_ts() + int(interval_minutes) * 60,
        "last_run": old.get("last_run", 0),
        "enabled": True,
        "message_ids": old.get("message_ids", {}),
        "valid_message_id": old.get("valid_message_id"),
        "always_send_change_log": bool(always_send_change_log),
    }
    save_checkers(interaction.guild.id, checkers)

    await interaction.response.send_message(
        f"Two-server checker saved for `{name}`.\n"
        f"Invalid updates: <#{invalid_id}>\n"
        f"Valid summary: {f'<#{valid_id}>' if valid_id else '`not set`'}\n"
        f"Change logs: {f'<#{change_id}>' if change_id else '`not set`'}",
        ephemeral=True
    )

@bot.tree.command(name="checker_run_now", description="Run a two-server checker list now.")
@app_commands.default_permissions(manage_guild=True)
async def checker_run_now(interaction: discord.Interaction, list_name: str):
    if not await require_manager(interaction): return
    name = list_name.strip().lower().replace(" ", "-")[:40]
    setup = get_checkers(interaction.guild.id).get(name)
    if not setup:
        return await interaction.response.send_message("No two-server setup found for that list.", ephemeral=True)
    await interaction.response.send_message(f"Running checker for `{name}` now...", ephemeral=True)
    async with checker_lock:
        entry = await run_two_server_check(interaction.guild, name, setup)
    await interaction.followup.send(
        f"Finished `{name}`. Invalid → Valid: `{len(entry['became_valid'])}` • Valid → Invalid: `{len(entry['valid_to_invalid'])}`",
        ephemeral=True
    )

@bot.tree.command(name="checker_change_logs", description="View recent invalid/valid flips from list checks.")
@app_commands.default_permissions(manage_guild=True)
async def checker_change_logs(interaction: discord.Interaction, list_name: Optional[str] = None, limit: app_commands.Range[int, 1, 10] = 5):
    if not await require_manager(interaction): return
    logs = list(reversed(get_change_logs(interaction.guild.id)))
    if list_name:
        wanted = list_name.strip().lower().replace(" ", "-")[:40]
        logs = [x for x in logs if x.get("list_name") == wanted]
    logs = logs[:int(limit)]

    e = make_embed("🔁 Recent Checker Change Logs", color=BLUE)
    if not logs:
        e.description = "No checker change logs yet."
    else:
        fields = []
        for log in logs:
            bv = log.get("became_valid", [])
            vti = log.get("valid_to_invalid", [])
            value = (
                f"**Time:** <t:{int(log.get('created_ts', 0))}:R>\n"
                f"**Processed:** `{log.get('processed', 0)}` • **Errors:** `{log.get('errors', 0)}`\n"
                f"**Invalid → Valid:** `{len(bv)}`\n"
                f"{', '.join('`' + c + '`' for c in bv[:12]) if bv else 'None'}\n"
                f"**Valid → Invalid:** `{len(vti)}`\n"
                f"{', '.join('`' + c + '`' for c in vti[:12]) if vti else 'None'}"
            )
            e.add_field(name=f"List: {log.get('list_name')}", value=value[:1024], inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="checker_setups", description="Show two-server checker setups.")
@app_commands.default_permissions(manage_guild=True)
async def checker_setups(interaction: discord.Interaction):
    if not await require_manager(interaction): return
    checkers = get_checkers(interaction.guild.id)
    e = make_embed("Two-Server Checker Setups", color=BLUE)
    if not checkers:
        e.description = "No setups yet."
    else:
        lines = []
        for name, c in checkers.items():
            lines.append(
                f"`{name}` → invalid <#{c['hunters_invalid_channel_id']}>"
                f" • changes {f'<#{c.get('change_log_channel_id')}>' if c.get('change_log_channel_id') else '`not set`'}"
                f" • every `{c['interval_minutes']}m` • next <t:{int(c.get('next_run', 0))}:R>"
            )
        e.description = "\n".join(lines)
    await interaction.response.send_message(embed=e, ephemeral=True)

# =========================================================
# EVENTS
# =========================================================
@bot.event
async def on_ready():
    ensure_data()
    bot.add_view(HunterPanel())
    bot.add_view(ManagerPanel())
    print(f"Logged in as {bot.user}")
    print("RUNNING CLAIM BOT WITH SEPARATE CHECKER CHANGE LOG CHANNEL")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Slash sync failed: {e}")
    if not checker_loop.is_running():
        checker_loop.start()

if not TOKEN:
    raise RuntimeError("TOKEN is missing. Add TOKEN to your environment variables.")

bot.run(TOKEN)
