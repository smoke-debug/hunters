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
        "valid_channel_id": None, "invalid_channel_id": None,
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
