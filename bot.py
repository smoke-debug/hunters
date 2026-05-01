
import discord
from discord.ext import commands
from discord import app_commands
import json, os, time

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

DATA = "data.json"

def load():
    if not os.path.exists(DATA):
        return {"payments": {}, "claims": []}
    return json.load(open(DATA))

def save(data):
    json.dump(data, open(DATA, "w"), indent=2)

def now():
    return int(time.time())

# ---------------- PAYMENT ----------------

@bot.tree.command(name="set_payment")
async def set_payment(i: discord.Interaction, method: str):
    data = load()
    data["payments"][str(i.user.id)] = method
    save(data)
    await i.response.send_message(f"✅ Payment set to `{method}`", ephemeral=True)

@bot.tree.command(name="delete_payment")
async def delete_payment(i: discord.Interaction):
    data = load()
    data["payments"].pop(str(i.user.id), None)
    save(data)
    await i.response.send_message("🗑️ Payment removed", ephemeral=True)

@bot.tree.command(name="view_payments")
async def view_payments(i: discord.Interaction):
    data = load()
    if not data["payments"]:
        return await i.response.send_message("No payments set.", ephemeral=True)
    txt = "\n".join([f"<@{u}> → `{m}`" for u,m in data["payments"].items()])
    await i.response.send_message(txt[:2000], ephemeral=True)

# ---------------- CLAIM ----------------

@bot.tree.command(name="claim")
async def claim(i: discord.Interaction, code: str):
    data = load()

    if str(i.user.id) not in data["payments"]:
        return await i.response.send_message(
            "❌ You MUST set payment first using /set_payment",
            ephemeral=True
        )

    claim = {
        "user": i.user.id,
        "code": code,
        "status": "pending",
        "time": now()
    }
    data["claims"].append(claim)
    save(data)

    await i.response.send_message("📩 Claim submitted for approval.", ephemeral=True)

# ---------------- APPROVAL ----------------

@bot.tree.command(name="approve")
async def approve(i: discord.Interaction, index: int):
    data = load()
    try:
        claim = data["claims"][index]
    except:
        return await i.response.send_message("Invalid ID", ephemeral=True)

    claim["status"] = "approved"
    save(data)

    user = await bot.fetch_user(claim["user"])
    try:
        await user.send(f"✅ Your claim `{claim['code']}` was approved!")
    except:
        pass

    await i.response.send_message("Approved.", ephemeral=True)

@bot.tree.command(name="deny")
async def deny(i: discord.Interaction, index: int):
    data = load()
    try:
        claim = data["claims"][index]
    except:
        return await i.response.send_message("Invalid ID", ephemeral=True)

    claim["status"] = "denied"
    save(data)

    user = await bot.fetch_user(claim["user"])
    try:
        await user.send("❌ Your claim was denied. Keep trying.")
    except:
        pass

    await i.response.send_message("Denied.", ephemeral=True)

# ---------------- READY ----------------

@bot.event
async def on_ready():
    await bot.tree.sync()
    print("READY")

bot.run("YOUR_TOKEN_HERE")
