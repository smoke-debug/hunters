from __future__ import annotations

import os, re, json, time, asyncio
from pathlib import Path
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

TOKEN = os.getenv("TOKEN")
DATA_DIR = Path("data")
INVALID_DIR = DATA_DIR / "invalid_vanities"
CONFIG_FILE = DATA_DIR / "config.json"
LISTS_FILE = DATA_DIR / "lists.json"
WATCHES_FILE = DATA_DIR / "watches.json"
CLAIMS_FILE = DATA_DIR / "claims.json"
CLAIM_STATS_FILE = DATA_DIR / "claim_stats.json"
CHECK_DELAY = float(os.getenv("CHECK_DELAY", "3"))
WATCH_INTERVAL_MINUTES = int(os.getenv("WATCH_INTERVAL_MINUTES", "10"))
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

run_lock = asyncio.Lock()
stop_requested = False


def ensure_dirs():
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


def save_json(path: Path, data):
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def gkey(guild_id: int) -> str:
    return str(guild_id)


def now_ts() -> int:
    return int(time.time())


def clean_code(text: str) -> str:
    text = str(text or "").strip().lower()
    for prefix in ("https://discord.gg/", "http://discord.gg/", "discord.gg/", "https://discord.com/invite/", "http://discord.com/invite/", "discord.com/invite/"):
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
        val = int(item)
        if val not in ids:
            ids.append(val)
    return ids


def role_mentions(guild: discord.Guild, ids: List[int]) -> str:
    return " ".join(r.mention for rid in ids if (r := guild.get_role(int(rid))))


def embed(title: str, desc: str = "", color: discord.Color = DARK) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color)
    e.timestamp = discord.utils.utcnow()
    return e


def config(guild_id: int) -> dict:
    data = load_json(CONFIG_FILE, {})
    return data.setdefault(gkey(guild_id), {
        "manager_users": [], "manager_roles": [],
        "valid_channel_id": None, "invalid_channel_id": None, "claim_log_channel_id": None,
        "elite_hunter_role_name": "elite hunter",
        "ping_role_ids": [], "delay_seconds": CHECK_DELAY,
    })


def save_config(guild_id: int, cfg: dict):
    data = load_json(CONFIG_FILE, {})
    data[gkey(guild_id)] = cfg
    save_json(CONFIG_FILE, data)


def is_adminish(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild


def has_access(member: discord.Member) -> bool:
    if is_adminish(member):
        return True
    cfg = config(member.guild.id)
    if str(member.id) in {str(x) for x in cfg.get("manager_users", [])}:
        return True
    roles = {str(r.id) for r in member.roles}
    allowed = {str(x) for x in cfg.get("manager_roles", [])}
    return bool(roles & allowed)


async def require_access(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return False
    if not has_access(interaction.user):
        await interaction.response.send_message("You need Manage Server/Admin or vanity manager access.", ephemeral=True)
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


def invalid_path(length: int) -> Path:
    ensure_dirs()
    return INVALID_DIR / f"invalid_{length}_letters.txt"


def read_invalid(length: int) -> set[str]:
    try:
        return {clean_code(x) for x in invalid_path(length).read_text(encoding="utf-8").splitlines() if clean_code(x)}
    except Exception:
        return set()


def write_invalid(length: int, values: set[str]):
    invalid_path(length).write_text("\n".join(sorted(values)) + ("\n" if values else ""), encoding="utf-8")


def lists_for(guild_id: int) -> dict:
    data = load_json(LISTS_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_lists(guild_id: int, lists: dict):
    data = load_json(LISTS_FILE, {})
    data[gkey(guild_id)] = lists
    save_json(LISTS_FILE, data)


def watches_for(guild_id: int) -> dict:
    data = load_json(WATCHES_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_watches(guild_id: int, watches: dict):
    data = load_json(WATCHES_FILE, {})
    data[gkey(guild_id)] = watches
    save_json(WATCHES_FILE, data)


def claims_for(guild_id: int) -> list:
    data = load_json(CLAIMS_FILE, {})
    return data.setdefault(gkey(guild_id), [])


def save_claims(guild_id: int, claims: list):
    data = load_json(CLAIMS_FILE, {})
    data[gkey(guild_id)] = claims
    save_json(CLAIMS_FILE, data)


def parse_claim_date(date_text: str) -> tuple[Optional[int], Optional[str]]:
    text = str(date_text or "").strip()
    if not text:
        return None, "Date is required. Use `YYYY-MM-DD`, like `2026-04-30`."
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not m:
        return None, "Invalid date. Use `YYYY-MM-DD`, like `2026-04-30`."
    y, mo, d = map(int, m.groups())
    try:
        import datetime
        dt = datetime.datetime(y, mo, d, 12, 0, 0, tzinfo=datetime.timezone.utc)
        return int(dt.timestamp()), None
    except ValueError:
        return None, "That date does not exist. Use a real date in `YYYY-MM-DD` format."


def claim_stats_for(guild_id: int) -> dict:
    data = load_json(CLAIM_STATS_FILE, {})
    return data.setdefault(gkey(guild_id), {})


def save_claim_stats(guild_id: int, stats: dict):
    data = load_json(CLAIM_STATS_FILE, {})
    data[gkey(guild_id)] = stats
    save_json(CLAIM_STATS_FILE, data)


def claim_embed(guild: discord.Guild, claim: dict, stats: Optional[dict] = None) -> discord.Embed:
    code = claim.get("code", "unknown")
    tried = int(claim.get("total_tried", 0))
    e = embed("🏷️ Vanity Claim Logged", color=GOLD)
    e.description = (
        f"**Vanity:** `discord.gg/{code}`\n"
        f"**Claimed By:** <@{claim.get('user_id')}>\n"
        f"**Claim Date:** <t:{int(claim.get('claimed_ts', now_ts()))}:D>\n"
        f"**Vanities Tried This Run:** `{tried:,}`\n"
        f"**Logged:** <t:{int(claim.get('logged_ts', now_ts()))}:R>"
    )
    if stats:
        e.add_field(name="Hunter Stats", value=(
            f"**Total Claims:** `{int(stats.get('claims', 0)):,}`\n"
            f"**Total Vanities Tried:** `{int(stats.get('total_tried', 0)):,}`"
        ), inline=False)
    notes = claim.get("notes") or "No notes."
    e.add_field(name="Notes", value=notes[:1024], inline=False)
    e.set_footer(text=f"Claim ID: {claim.get('id')} • {guild.name}")
    return e


async def maybe_give_elite_hunter(member: discord.Member, claims_count: int) -> Optional[discord.Role]:
    if claims_count < 10:
        return None
    role = discord.utils.get(member.guild.roles, name="elite hunter") or discord.utils.get(member.guild.roles, name="Elite Hunter")
    if not role:
        return None
    if role not in member.roles:
        try:
            await member.add_roles(role, reason="Reached 10+ vanity claims")
        except Exception:
            return None
    return role


async def invite_status(code: str) -> str:
    try:
        await bot.fetch_invite(code)
        return "valid"
    except discord.NotFound:
        return "invalid"
    except discord.Forbidden:
        return "error: forbidden"
    except discord.HTTPException as e:
        return f"error: HTTP {e.status}"
    except Exception as e:
        return f"error: {type(e).__name__}"


def compact(items: List[str], limit: int = 25) -> str:
    if not items:
        return "None"
    shown = [f"`discord.gg/{x}`" for x in items[:limit]]
    if len(items) > limit:
        shown.append(f"...and {len(items) - limit} more")
    return "\n".join(shown)


async def run_check(guild: discord.Guild, label: str, codes: List[str], valid_channel, invalid_channel, ping_role_ids: List[int], delay: float, status_msg=None, alert_only_recent=False):
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

    if alert_only_recent and not stats["recent"] and not stats["became_valid"] and not stats["errors"]:
        return stats

    runtime = now_ts() - stats["start"]
    desc = f"**List:** `{label}`\n**Processed:** `{stats['processed']}`\n**Valid:** `{len(stats['valid'])}` • **Invalid:** `{len(stats['invalid'])}` • **Errors:** `{len(stats['errors'])}`\n**New Invalid Targets:** `{len(stats['recent'])}`\n**Became Valid Again:** `{len(stats['became_valid'])}`\n**Runtime:** `{runtime}s`"
    await valid_channel.send(embed=embed("✅ Vanity Results — Valid", desc + "\n\n" + compact(stats["valid"]), GREEN))
    e = embed("🔥 Vanity Results — Targets", desc, RED if stats["recent"] else PURPLE)
    e.add_field(name="New Targets", value=compact(stats["recent"]), inline=False)
    e.add_field(name="All Invalid This Run", value=compact(stats["invalid"]), inline=False)
    if stats["errors"]:
        e.add_field(name="Errors", value="\n".join(stats["errors"][:10]), inline=False)
    await invalid_channel.send(content=role_mentions(guild, ping_role_ids) or None, embed=e, allowed_mentions=discord.AllowedMentions(roles=True))
    return stats


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


@bot.tree.command(name="vanity_help", description="Show the vanity bot command guide.")
async def vanity_help(interaction: discord.Interaction):
    e = embed("Vanity Hunter Bot Help", "Simple separate bot for checking Discord invite/vanity codes.", PURPLE)
    e.add_field(name="Setup", value="`/vanity_setup`\n`/vanity_access_add_user`\n`/vanity_access_add_role`", inline=False)
    e.add_field(name="Check", value="`/vanity_check`\n`/vanity_add_list`\n`/vanity_run_list`\n`/vanity_stop`", inline=False)
    e.add_field(name="Auto Watch", value="`/vanity_watch_start`\n`/vanity_watch_stop`\n`/vanity_watches`", inline=False)
    e.add_field(name="Claim Logs", value="`/claim_log` — members log claimed vanities\n`/claim_setup` — set log channel\n`/claim_history` — view recent claims", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="vanity_setup", description="Set result channels, ping roles, and delay.")
async def vanity_setup(interaction: discord.Interaction, valid_channel: discord.TextChannel, invalid_channel: discord.TextChannel, ping_roles: Optional[str] = None, delay_seconds: app_commands.Range[float, 1.0, 30.0] = CHECK_DELAY):
    if not await require_admin(interaction): return
    cfg = config(interaction.guild.id)
    cfg.update({"valid_channel_id": valid_channel.id, "invalid_channel_id": invalid_channel.id, "ping_role_ids": parse_role_ids(ping_roles), "delay_seconds": float(delay_seconds)})
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Saved. Valid: {valid_channel.mention} • Invalid: {invalid_channel.mention} • Delay: `{delay_seconds}s`", ephemeral=True)


@bot.tree.command(name="vanity_access_add_user", description="Allow a user to use vanity commands.")
async def vanity_access_add_user(interaction: discord.Interaction, user: discord.Member):
    if not await require_admin(interaction): return
    cfg = config(interaction.guild.id); users = [str(x) for x in cfg.get("manager_users", [])]
    if str(user.id) not in users: users.append(str(user.id))
    cfg["manager_users"] = users; save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {user.mention}.", ephemeral=True)


@bot.tree.command(name="vanity_access_add_role", description="Allow a role to use vanity commands.")
async def vanity_access_add_role(interaction: discord.Interaction, role: discord.Role):
    if not await require_admin(interaction): return
    cfg = config(interaction.guild.id); roles = [str(x) for x in cfg.get("manager_roles", [])]
    if str(role.id) not in roles: roles.append(str(role.id))
    cfg["manager_roles"] = roles; save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Added {role.mention}.", ephemeral=True)


@bot.tree.command(name="vanity_check", description="Check comma/space separated vanity codes.")
async def vanity_check(interaction: discord.Interaction, codes: str):
    global stop_requested
    if not await require_access(interaction): return
    parsed = parse_codes(codes)[:MAX_MANUAL_CODES]
    if not parsed: return await interaction.response.send_message("Paste at least one code.", ephemeral=True)
    if run_lock.locked(): return await interaction.response.send_message("A check is already running.", ephemeral=True)
    cfg = config(interaction.guild.id)
    vc = interaction.guild.get_channel(int(cfg.get("valid_channel_id") or 0)) or interaction.channel
    ic = interaction.guild.get_channel(int(cfg.get("invalid_channel_id") or 0)) or interaction.channel
    await interaction.response.send_message(f"Checking `{len(parsed)}` codes...", ephemeral=True)
    msg = await interaction.channel.send(f"Checking `manual` — `0/{len(parsed)}` done")
    async with run_lock:
        stop_requested = False
        await run_check(interaction.guild, "manual", parsed, vc, ic, cfg.get("ping_role_ids", []), cfg.get("delay_seconds", CHECK_DELAY), msg)
        stop_requested = False


@bot.tree.command(name="vanity_add_list", description="Save or add words to a named list.")
async def vanity_add_list(interaction: discord.Interaction, name: str, codes: str, replace: bool = False):
    if not await require_access(interaction): return
    name = clean_code(name.replace(" ", "-"))[:40]
    parsed = parse_codes(codes)
    if not name or not parsed: return await interaction.response.send_message("Use a valid name and codes.", ephemeral=True)
    lists = lists_for(interaction.guild.id)
    base = [] if replace else lists.get(name, [])
    merged = []
    for c in base + parsed:
        if c not in merged: merged.append(c)
    lists[name] = merged; save_lists(interaction.guild.id, lists)
    await interaction.response.send_message(f"Saved `{len(merged)}` codes in `{name}`.", ephemeral=True)


@bot.tree.command(name="vanity_lists", description="Show saved vanity lists.")
async def vanity_lists(interaction: discord.Interaction):
    if not await require_access(interaction): return
    lists = lists_for(interaction.guild.id)
    desc = "\n".join(f"`{k}` — `{len(v)}` codes" for k, v in lists.items()) or "No lists yet."
    await interaction.response.send_message(embed=embed("Saved Vanity Lists", desc, PURPLE), ephemeral=True)


@bot.tree.command(name="vanity_run_list", description="Check a saved vanity list.")
async def vanity_run_list(interaction: discord.Interaction, name: str):
    global stop_requested
    if not await require_access(interaction): return
    name = clean_code(name.replace(" ", "-"))[:40]
    codes = lists_for(interaction.guild.id).get(name, [])[:MAX_LIST_CODES]
    if not codes: return await interaction.response.send_message("That list is empty or missing.", ephemeral=True)
    if run_lock.locked(): return await interaction.response.send_message("A check is already running.", ephemeral=True)
    cfg = config(interaction.guild.id)
    vc = interaction.guild.get_channel(int(cfg.get("valid_channel_id") or 0)) or interaction.channel
    ic = interaction.guild.get_channel(int(cfg.get("invalid_channel_id") or 0)) or interaction.channel
    await interaction.response.send_message(f"Checking list `{name}` with `{len(codes)}` codes...", ephemeral=True)
    msg = await interaction.channel.send(f"Checking `{name}` — `0/{len(codes)}` done")
    async with run_lock:
        stop_requested = False
        await run_check(interaction.guild, name, codes, vc, ic, cfg.get("ping_role_ids", []), cfg.get("delay_seconds", CHECK_DELAY), msg)
        stop_requested = False


@bot.tree.command(name="vanity_stop", description="Stop the current vanity check.")
async def vanity_stop(interaction: discord.Interaction):
    global stop_requested
    if not await require_access(interaction): return
    if not run_lock.locked(): return await interaction.response.send_message("No check is running.", ephemeral=True)
    stop_requested = True
    await interaction.response.send_message("Stop requested.", ephemeral=True)


@bot.tree.command(name="vanity_watch_start", description="Auto-check a saved list every few minutes.")
async def vanity_watch_start(interaction: discord.Interaction, name: str, interval_minutes: app_commands.Range[int, 5, 1440] = WATCH_INTERVAL_MINUTES):
    if not await require_access(interaction): return
    name = clean_code(name.replace(" ", "-"))[:40]
    if name not in lists_for(interaction.guild.id): return await interaction.response.send_message("That list does not exist.", ephemeral=True)
    cfg = config(interaction.guild.id); watches = watches_for(interaction.guild.id)
    watches[name] = {"enabled": True, "interval_minutes": int(interval_minutes), "next_run": now_ts() + 5, "valid_channel_id": cfg.get("valid_channel_id"), "invalid_channel_id": cfg.get("invalid_channel_id"), "ping_role_ids": cfg.get("ping_role_ids", [])}
    save_watches(interaction.guild.id, watches)
    await interaction.response.send_message(f"Watching `{name}` every `{interval_minutes}` minutes.", ephemeral=True)


@bot.tree.command(name="vanity_watch_stop", description="Stop auto-checking a saved list.")
async def vanity_watch_stop(interaction: discord.Interaction, name: str):
    if not await require_access(interaction): return
    name = clean_code(name.replace(" ", "-"))[:40]
    watches = watches_for(interaction.guild.id); watches.pop(name, None); save_watches(interaction.guild.id, watches)
    await interaction.response.send_message(f"Stopped watching `{name}`.", ephemeral=True)


@bot.tree.command(name="vanity_watches", description="Show active auto-checks.")
async def vanity_watches(interaction: discord.Interaction):
    if not await require_access(interaction): return
    watches = watches_for(interaction.guild.id)
    desc = "\n".join(f"`{k}` — every `{v.get('interval_minutes')}` min • next <t:{int(v.get('next_run',0))}:R>" for k,v in watches.items()) or "No active watches."
    await interaction.response.send_message(embed=embed("Active Watches", desc, GOLD), ephemeral=True)


@bot.tree.command(name="vanity_files", description="Show invalid-file counts.")
async def vanity_files(interaction: discord.Interaction):
    if not await require_access(interaction): return
    lines = [f"`{n}` letters — `{len(read_invalid(n))}` saved" for n in range(1, 11) if len(read_invalid(n))]
    await interaction.response.send_message(embed=embed("Invalid Vanity Files", "Folder: `data/invalid_vanities/`\n" + ("\n".join(lines) or "No invalids saved yet."), PURPLE), ephemeral=True)


@bot.tree.command(name="claim_setup", description="Set the channel where member vanity claims are logged.")
async def claim_setup(interaction: discord.Interaction, log_channel: discord.TextChannel):
    if not await require_admin(interaction): return
    cfg = config(interaction.guild.id)
    cfg["claim_log_channel_id"] = log_channel.id
    save_config(interaction.guild.id, cfg)
    await interaction.response.send_message(f"Claim logs will be posted in {log_channel.mention}.", ephemeral=True)


@bot.tree.command(name="claim_log", description="Log a vanity you claimed and update your hunter stats.")
@app_commands.describe(
    vanity="The vanity code or invite link, like prey or discord.gg/prey",
    claimed_date="Date you claimed it. Format: YYYY-MM-DD",
    total_tried="How many total vanities you tried before/while hunting this claim",
    notes="Extra details, proof, or context"
)
async def claim_log(
    interaction: discord.Interaction,
    vanity: str,
    claimed_date: str,
    total_tried: app_commands.Range[int, 0, 1000000],
    notes: Optional[str] = None,
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)

    code = clean_code(vanity)
    if not code:
        return await interaction.response.send_message("Use a valid vanity code or invite link.", ephemeral=True)

    claimed_ts, err = parse_claim_date(claimed_date)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)

    cfg = config(interaction.guild.id)
    log_channel = interaction.guild.get_channel(int(cfg.get("claim_log_channel_id") or 0))
    if not log_channel:
        log_channel = interaction.channel

    claims = claims_for(interaction.guild.id)
    claim_id = str(int(time.time() * 1000))
    claim = {
        "id": claim_id,
        "guild_id": interaction.guild.id,
        "user_id": interaction.user.id,
        "code": code,
        "claimed_ts": claimed_ts,
        "logged_ts": now_ts(),
        "total_tried": int(total_tried),
        "notes": (notes or "").strip()[:1000] or None,
    }
    claims.append(claim)
    save_claims(interaction.guild.id, claims[-2000:])

    all_stats = claim_stats_for(interaction.guild.id)
    stats = all_stats.setdefault(str(interaction.user.id), {"claims": 0, "total_tried": 0, "last_claim_ts": None, "last_logged_ts": None, "codes": []})
    stats["claims"] = int(stats.get("claims", 0)) + 1
    stats["total_tried"] = int(stats.get("total_tried", 0)) + int(total_tried)
    stats["last_claim_ts"] = claimed_ts
    stats["last_logged_ts"] = now_ts()
    codes = list(stats.get("codes", []))
    if code not in codes:
        codes.append(code)
    stats["codes"] = codes[-200:]
    all_stats[str(interaction.user.id)] = stats
    save_claim_stats(interaction.guild.id, all_stats)

    elite_role = await maybe_give_elite_hunter(interaction.user, int(stats.get("claims", 0)))
    await log_channel.send(embed=claim_embed(interaction.guild, claim, stats))

    msg = f"Logged `discord.gg/{code}` in {log_channel.mention}. Your total claims: `{int(stats.get('claims', 0))}`."
    if elite_role:
        msg += f" You also earned {elite_role.mention}."
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="claim_history", description="Managers: show recent vanity claims, optionally for one member.")
async def claim_history(interaction: discord.Interaction, user: Optional[discord.Member] = None, limit: app_commands.Range[int, 1, 20] = 10):
    if not await require_access(interaction): return
    claims = list(reversed(claims_for(interaction.guild.id)))
    if user:
        claims = [c for c in claims if int(c.get("user_id", 0)) == user.id]
    claims = claims[:int(limit)]
    if not claims:
        return await interaction.response.send_message("No claims found.", ephemeral=True)
    lines = []
    for c in claims:
        lines.append(
            f"`discord.gg/{c.get('code')}` — <@{c.get('user_id')}> • "
            f"claimed <t:{int(c.get('claimed_ts'))}:D> • tried `{int(c.get('total_tried', 0)):,}`"
        )
    await interaction.response.send_message(embed=embed("Recent Vanity Claims", "\n".join(lines), GOLD), ephemeral=True)


@bot.tree.command(name="claim_stats", description="Show your vanity hunter stats or another member's stats.")
async def claim_stats(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    if not interaction.guild:
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    target = user or interaction.user
    stats = claim_stats_for(interaction.guild.id).get(str(target.id), {"claims": 0, "total_tried": 0, "codes": []})
    codes = stats.get("codes", [])[-10:]
    e = embed(f"🏹 Vanity Hunter Stats — {target.display_name}", color=PURPLE)
    e.description = (
        f"**Total Claims:** `{int(stats.get('claims', 0)):,}`\n"
        f"**Total Vanities Tried:** `{int(stats.get('total_tried', 0)):,}`\n"
        f"**Elite Hunter Progress:** `{min(int(stats.get('claims', 0)), 10)}/10 claims`"
    )
    if stats.get("last_claim_ts"):
        e.add_field(name="Last Claim", value=f"<t:{int(stats.get('last_claim_ts'))}:D>", inline=True)
    e.add_field(name="Recent Claimed Vanities", value="\n".join(f"`discord.gg/{c}`" for c in codes) or "None yet.", inline=False)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="claim_leaderboard", description="Show the top vanity hunters by total claims.")
async def claim_leaderboard(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("This only works in a server.", ephemeral=True)
    all_stats = claim_stats_for(interaction.guild.id)
    ranked = sorted(all_stats.items(), key=lambda kv: (int(kv[1].get("claims", 0)), int(kv[1].get("total_tried", 0))), reverse=True)[:10]
    if not ranked:
        return await interaction.response.send_message("No hunter stats yet.", ephemeral=True)
    lines = []
    for i, (uid, st) in enumerate(ranked, start=1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`#{i}`"
        lines.append(f"{medal} <@{uid}> — `{int(st.get('claims', 0))}` claims • `{int(st.get('total_tried', 0)):,}` tried")
    await interaction.response.send_message(embed=embed("🏆 Vanity Hunter Leaderboard", "\n".join(lines), GOLD))


@bot.tree.command(name="info_manager", description="Send an informational embed about vanity manager roles.")
async def info_manager(interaction: discord.Interaction):
    e = embed("📌 Vanity Manager Role", color=PURPLE)
    e.description = (
        "Managers keep the hunting system organized and fair. They help assign work, check claim logs, verify notes/proof, "
        "watch for duplicate claims, and make sure hunters understand what to do.\n\n"
        "**Main duties:** review claim logs, answer hunter questions, manage saved lists/watches, and report strong vanities or issues to ownership.\n"
        "**Expected behavior:** be clear, fair, active, and don’t abuse access to lists, results, or hunter stats."
    )
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="info_elite_hunter", description="Send an informational embed about the Elite Hunter role.")
async def info_elite_hunter(interaction: discord.Interaction):
    e = embed("🏹 Elite Hunter Role", color=GOLD)
    e.description = (
        "**Elite Hunter** is earned by logging **10+ confirmed vanity claims** with `/claim_log`.\n\n"
        "Once you reach 10 claims, the bot will try to give you the role named `elite hunter` automatically. "
        "Your claims, total vanities tried, dates, and notes are saved to your stats.\n\n"
        "Elite Hunters are trusted hunters who consistently find and report usable vanities."
    )
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="info_vanity_job", description="Send an informational embed explaining how the vanity hunting job works.")
async def info_vanity_job(interaction: discord.Interaction):
    e = embed("🔎 How Vanity Hunting Works", color=GREEN)
    e.description = (
        "Your job is to search/check potential Discord vanity invites and log anything you successfully claim.\n\n"
        "**How to work:** hunt through assigned words/lists, try available codes, and keep track of how many total vanities you tested.\n"
        "**When you claim one:** use `/claim_log` with the vanity, claim date, total tried, and notes.\n"
        "**Stats:** every claim adds to your total claims and total tried. At **10+ claims**, you can earn `elite hunter`.\n\n"
        "Do not fake claims, steal other hunters’ work, or spam low-quality logs. Clear notes help managers verify your work faster."
    )
    await interaction.response.send_message(embed=e)


@tasks.loop(seconds=30)
async def watch_loop():
    if run_lock.locked(): return
    all_watches = load_json(WATCHES_FILE, {})
    for guild_id, watches in list(all_watches.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild: continue
        changed = False
        for name, w in list(watches.items()):
            if now_ts() < int(w.get("next_run", 0)): continue
            codes = lists_for(guild.id).get(name, [])[:MAX_LIST_CODES]
            vc = guild.get_channel(int(w.get("valid_channel_id") or 0))
            ic = guild.get_channel(int(w.get("invalid_channel_id") or 0))
            if codes and vc and ic:
                async with run_lock:
                    await run_check(guild, f"watch:{name}", codes, vc, ic, [int(x) for x in w.get("ping_role_ids", [])], CHECK_DELAY, None, True)
            w["last_run"] = now_ts(); w["next_run"] = now_ts() + int(w.get("interval_minutes", WATCH_INTERVAL_MINUTES)) * 60
            watches[name] = w; changed = True
        if changed:
            all_watches[guild_id] = watches
    save_json(WATCHES_FILE, all_watches)


@watch_loop.before_loop
async def before_watch_loop():
    await bot.wait_until_ready()


if not TOKEN:
    raise RuntimeError("Missing TOKEN environment variable.")
bot.run(TOKEN)
