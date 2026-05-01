import os, re, json, time
from pathlib import Path
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception: pass

TOKEN=os.getenv('TOKEN')
DATA=Path(os.getenv('DATA_DIR','data'))
CONFIG=DATA/'config.json'; CLAIMS=DATA/'claims.json'; NOTES=DATA/'notes.json'
intents=discord.Intents.default(); intents.guilds=True; intents.members=True
bot=commands.Bot(command_prefix='!', intents=intents, help_command=None)
DARK=discord.Color.from_rgb(34,34,42); PURPLE=discord.Color.from_rgb(150,95,255); GOLD=discord.Color.from_rgb(245,185,80); GREEN=discord.Color.from_rgb(65,185,115); RED=discord.Color.from_rgb(220,75,75); BLUE=discord.Color.from_rgb(90,150,255)

def ensure(): DATA.mkdir(parents=True, exist_ok=True)
def load(p,d):
    ensure()
    if not p.exists(): return d
    try: return json.loads(p.read_text())
    except Exception: return d
def save(p,d):
    ensure(); t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(d,indent=2)); os.replace(t,p)
def g(gid): return str(gid)
def ts(): return int(time.time())
def nid(): return str(int(time.time()*1000))
def emb(title, desc='', color=DARK):
    e=discord.Embed(title=title, description=desc, color=color); e.timestamp=discord.utils.utcnow(); return e
def snow(x):
    m=re.search(r'\d{15,25}', str(x or '')); return int(m.group(0)) if m else None
def code(x):
    x=str(x or '').lower().strip()
    for p in ['https://discord.gg/','http://discord.gg/','discord.gg/','https://discord.com/invite/','http://discord.com/invite/','discord.com/invite/']: x=x.replace(p,'')
    return re.sub(r'[^a-z0-9_-]','',x.strip('/'))[:32]
def money(v): return f'${float(v):,.2f}'
def cfg(gid):
    d=load(CONFIG,{})
    return d.setdefault(g(gid), {'manager_users':[],'manager_roles':[],'claim_log_channel_id':None,'claim_cooldown':60,'max_claims_hour':5,'autoroles':[]})
def save_cfg(gid,c): d=load(CONFIG,{}); d[g(gid)]=c; save(CONFIG,d)
def claims(gid): d=load(CLAIMS,{}); return d.setdefault(g(gid),[])
def save_claims(gid,c): d=load(CLAIMS,{}); d[g(gid)]=c; save(CLAIMS,d)
def notes(gid): d=load(NOTES,{}); return d.setdefault(g(gid),{})
def save_notes(gid,n): d=load(NOTES,{}); d[g(gid)]=n; save(NOTES,d)
def admin(m): return m.guild_permissions.administrator or m.guild_permissions.manage_guild
def manager(m):
    if admin(m): return True
    c=cfg(m.guild.id)
    return str(m.id) in map(str,c.get('manager_users',[])) or bool({str(r.id) for r in m.roles}&{str(x) for x in c.get('manager_roles',[])})
async def need_manager(i):
    if not i.guild or not isinstance(i.user,discord.Member): await i.response.send_message('Server only.',ephemeral=True); return False
    if not manager(i.user): await i.response.send_message('You need manager access.',ephemeral=True); return False
    return True
async def need_admin(i):
    if not i.guild or not isinstance(i.user,discord.Member): await i.response.send_message('Server only.',ephemeral=True); return False
    if not admin(i.user): await i.response.send_message('You need Manage Server or Administrator.',ephemeral=True); return False
    return True
def find(cs,cid):
    for c in cs:
        if str(c.get('id'))==str(cid): return c
    return None
def uclaims(gid,uid): return [c for c in claims(gid) if str(c.get('user_id'))==str(uid)]
def dup(gid,cd): return any(code(c.get('code'))==code(cd) for c in claims(gid))
def spam(gid,uid):
    c=cfg(gid); cs=sorted(uclaims(gid,uid),key=lambda x:int(x.get('created_ts',0)),reverse=True)
    if cs and ts()-int(cs[0].get('created_ts',0))<int(c.get('claim_cooldown',60)): return True, 'Slow down before logging another claim.'
    if len([x for x in cs if ts()-int(x.get('created_ts',0))<=3600])>=int(c.get('max_claims_hour',5)): return True, 'You reached the hourly claim limit.'
    return False,''
def claim_embed(guild,c):
    st=c.get('status','pending'); color=GREEN if st in ['approved','sold','paid'] else RED if st=='denied' else GOLD
    e=emb('🏷️ Vanity Claim', f"**Vanity:** `discord.gg/{code(c.get('code'))}`\n**Hunter:** <@{c.get('user_id')}>\n**Status:** `{st}`\n**Logged:** <t:{int(c.get('created_ts',ts()))}:R>", color)
    if float(c.get('value') or 0)>0: e.add_field(name='Value',value=f"`{money(c.get('value'))}`",inline=True)
    if c.get('buyer'): e.add_field(name='Buyer',value=str(c.get('buyer'))[:1024],inline=True)
    e.add_field(name='Notes',value=(c.get('notes') or 'No notes.')[:1024],inline=False)
    if c.get('manager_notes'): e.add_field(name='Manager Notes',value=str(c.get('manager_notes'))[:1024],inline=False)
    e.set_footer(text=f"Claim ID: {c.get('id')} • {guild.name}"); return e
async def post_claim(guild,c):
    cid=cfg(guild.id).get('claim_log_channel_id')
    if not cid: return
    try: ch=guild.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
    except Exception: return
    if c.get('message_id'):
        try: await (await ch.fetch_message(int(c['message_id']))).delete()
        except Exception: pass
    try:
        msg=await ch.send(embed=claim_embed(guild,c)); c['message_id']=msg.id
        cs=claims(guild.id); old=find(cs,c['id'])
        if old: old.update(c); save_claims(guild.id,cs)
    except Exception: pass
async def del_msg(guild,c):
    cid=cfg(guild.id).get('claim_log_channel_id')
    if not cid or not c.get('message_id'): return
    try: ch=guild.get_channel(int(cid)) or await bot.fetch_channel(int(cid)); await (await ch.fetch_message(int(c['message_id']))).delete()
    except Exception: pass
async def autoroles(guild,member):
    total=len(uclaims(guild.id,member.id))
    for r in cfg(guild.id).get('autoroles',[]):
        role=guild.get_role(int(r.get('role_id',0))); req=int(r.get('claims_required',0))
        if role and total>=req and role not in member.roles:
            try: await member.add_roles(role,reason=f'Reached {req} claims')
            except Exception: pass
def stats(guild,uid):
    cs=uclaims(guild.id,uid); e=emb('📊 Hunter Stats',f'Stats for <@{uid}>',PURPLE)
    e.add_field(name='Claims',value=f"Total: `{len(cs)}`\nApproved: `{sum(1 for c in cs if c.get('status')=='approved')}`\nPending: `{sum(1 for c in cs if c.get('status','pending')=='pending')}`",inline=True)
    e.add_field(name='Value',value=f"`{money(sum(float(c.get('value') or 0) for c in cs))}`",inline=True)
    lines=[f"`discord.gg/{code(c.get('code'))}` • `{c.get('status','pending')}` • ID `{c.get('id')}`" for c in sorted(cs,key=lambda x:int(x.get('created_ts',0)),reverse=True)[:15]]
    e.add_field(name='Claimed Vanities',value='\n'.join(lines) if lines else 'No claims yet.',inline=False); return e

class LogClaim(discord.ui.Modal,title='Log Vanity Claim'):
    vanity=discord.ui.TextInput(label='Vanity code/link',placeholder='make or discord.gg/make',max_length=80)
    value=discord.ui.TextInput(label='Estimated value (optional)',required=False,max_length=20)
    notes=discord.ui.TextInput(label='Notes (optional)',style=discord.TextStyle.paragraph,required=False,max_length=800)
    async def on_submit(self,i):
        bad,msg=spam(i.guild.id,i.user.id)
        if bad and not manager(i.user): return await i.response.send_message(msg,ephemeral=True)
        cd=code(str(self.vanity))
        if not cd: return await i.response.send_message('Enter a valid vanity.',ephemeral=True)
        if dup(i.guild.id,cd): return await i.response.send_message('That vanity is already logged.',ephemeral=True)
        try: val=max(float(str(self.value).replace('$','').replace(',','').strip() or 0),0)
        except Exception: val=0
        c={'id':nid(),'code':cd,'user_id':i.user.id,'created_by':i.user.id,'created_ts':ts(),'updated_ts':ts(),'status':'pending','value':round(val,2),'buyer':'','notes':str(self.notes).strip(),'manager_notes':'','message_id':None}
        cs=claims(i.guild.id); cs.append(c); save_claims(i.guild.id,cs); await post_claim(i.guild,c); await autoroles(i.guild,i.user)
        await i.response.send_message(f'Logged `discord.gg/{cd}`. Claim ID `{c["id"]}`.',ephemeral=True)
class EditClaim(discord.ui.Modal,title='Edit Any Claim'):
    claim_id=discord.ui.TextInput(label='Claim ID',max_length=40)
    vanity=discord.ui.TextInput(label='New vanity (optional)',required=False,max_length=80)
    user=discord.ui.TextInput(label='New hunter ID/mention (optional)',required=False,max_length=40)
    value=discord.ui.TextInput(label='Value (optional)',required=False,max_length=20)
    notes=discord.ui.TextInput(label='Public notes (optional)',style=discord.TextStyle.paragraph,required=False,max_length=800)
    async def on_submit(self,i):
        if not await need_manager(i): return
        cs=claims(i.guild.id); c=find(cs,str(self.claim_id).strip())
        if not c: return await i.response.send_message('Claim ID not found.',ephemeral=True)
        if str(self.vanity).strip(): c['code']=code(str(self.vanity))
        uid=snow(str(self.user))
        if uid: c['user_id']=uid
        if str(self.value).strip():
            try: c['value']=round(max(float(str(self.value).replace('$','').replace(',','').strip()),0),2)
            except Exception: return await i.response.send_message('Invalid value.',ephemeral=True)
        if str(self.notes).strip(): c['notes']=str(self.notes).strip()
        c['updated_ts']=ts(); c['last_manager_id']=i.user.id; save_claims(i.guild.id,cs); await post_claim(i.guild,c)
        await i.response.send_message(f'Updated claim `{c["id"]}`.',ephemeral=True)
class ControlClaim(discord.ui.Modal,title='Set Status / Buyer'):
    claim_id=discord.ui.TextInput(label='Claim ID',max_length=40)
    status=discord.ui.TextInput(label='Status',placeholder='pending, approved, denied, sold, paid',max_length=20)
    buyer=discord.ui.TextInput(label='Buyer info (optional)',required=False,max_length=200)
    manager_notes=discord.ui.TextInput(label='Manager notes (optional)',style=discord.TextStyle.paragraph,required=False,max_length=800)
    async def on_submit(self,i):
        if not await need_manager(i): return
        cs=claims(i.guild.id); c=find(cs,str(self.claim_id).strip())
        if not c: return await i.response.send_message('Claim ID not found.',ephemeral=True)
        st=str(self.status).strip().lower()
        if st not in ['pending','approved','denied','sold','paid']: return await i.response.send_message('Invalid status.',ephemeral=True)
        c['status']=st
        if str(self.buyer).strip(): c['buyer']=str(self.buyer).strip()
        if str(self.manager_notes).strip(): c['manager_notes']=str(self.manager_notes).strip()
        c['updated_ts']=ts(); save_claims(i.guild.id,cs); await post_claim(i.guild,c)
        await i.response.send_message(f'Updated claim `{c["id"]}` to `{st}`.',ephemeral=True)
class DeleteClaim(discord.ui.Modal,title='Delete Claim'):
    claim_id=discord.ui.TextInput(label='Claim ID',max_length=40)
    confirm=discord.ui.TextInput(label='Type DELETE',max_length=20)
    async def on_submit(self,i):
        if not await need_manager(i): return
        if str(self.confirm).strip().upper()!='DELETE': return await i.response.send_message('Cancelled.',ephemeral=True)
        cs=claims(i.guild.id); c=find(cs,str(self.claim_id).strip())
        if not c: return await i.response.send_message('Claim ID not found.',ephemeral=True)
        await del_msg(i.guild,c); save_claims(i.guild.id,[x for x in cs if x.get('id')!=c.get('id')])
        await i.response.send_message(f'Deleted claim `{c["id"]}`.',ephemeral=True)
class AddNote(discord.ui.Modal,title='Log Note About User'):
    user=discord.ui.TextInput(label='User ID or mention',max_length=40)
    note=discord.ui.TextInput(label='Note',style=discord.TextStyle.paragraph,max_length=1000)
    async def on_submit(self,i):
        if not await need_manager(i): return
        uid=snow(str(self.user))
        if not uid: return await i.response.send_message('Invalid user.',ephemeral=True)
        n=notes(i.guild.id); n.setdefault(str(uid),[]).append({'id':nid(),'note':str(self.note).strip(),'by':i.user.id,'ts':ts()}); save_notes(i.guild.id,n)
        await i.response.send_message(f'Added note for <@{uid}>.',ephemeral=True)
class ViewNotes(discord.ui.Modal,title='View User Notes'):
    user=discord.ui.TextInput(label='User ID or mention',max_length=40)
    async def on_submit(self,i):
        if not await need_manager(i): return
        uid=snow(str(self.user)); items=notes(i.guild.id).get(str(uid),[]) if uid else []
        e=emb('📝 User Notes',f'Notes for <@{uid}>' if uid else 'Invalid user',BLUE)
        e.description += '\n\nNo notes found.' if not items else ''
        if items: e.add_field(name='Recent Notes',value='\n\n'.join(f"ID `{x['id']}` • by <@{x['by']}> • <t:{x['ts']}:R>\n{x['note'][:300]}" for x in sorted(items,key=lambda x:x['ts'],reverse=True)[:15])[:4000],inline=False)
        await i.response.send_message(embed=e,ephemeral=True)
class Settings(discord.ui.Modal,title='Manager Settings'):
    log=discord.ui.TextInput(label='Claim log channel ID/mention',required=False,max_length=40)
    cooldown=discord.ui.TextInput(label='Claim cooldown seconds',required=False,max_length=10)
    maxhour=discord.ui.TextInput(label='Max claims per hour',required=False,max_length=10)
    async def on_submit(self,i):
        if not await need_manager(i): return
        c=cfg(i.guild.id); cid=snow(str(self.log))
        if cid: c['claim_log_channel_id']=cid
        if str(self.cooldown).strip(): c['claim_cooldown']=max(0,int(str(self.cooldown).strip()))
        if str(self.maxhour).strip(): c['max_claims_hour']=max(1,int(str(self.maxhour).strip()))
        save_cfg(i.guild.id,c); await i.response.send_message('Settings saved.',ephemeral=True)
class AutoRole(discord.ui.Modal,title='Add Claim Autorole'):
    role=discord.ui.TextInput(label='Role ID/mention',max_length=40)
    req=discord.ui.TextInput(label='Claims required',max_length=10)
    async def on_submit(self,i):
        if not await need_manager(i): return
        rid=snow(str(self.role)); req=max(1,int(str(self.req).strip()))
        c=cfg(i.guild.id); c['autoroles']=[x for x in c.get('autoroles',[]) if str(x.get('role_id'))!=str(rid)]; c['autoroles'].append({'role_id':rid,'claims_required':req}); save_cfg(i.guild.id,c)
        await i.response.send_message(f'Autorole saved: <@&{rid}> at `{req}` claims.',ephemeral=True)

def hunter_embed():
    return emb('🏹 Hunter Panel','Log successful vanity pulls only. Fake, duplicate, or spammed claims can get you suspended.',PURPLE)
def manager_embed():
    e=emb('🛠️ Manager Control Panel','Control claims, user notes, buyer/status info, autoroles, and settings.\n\n**Managers are responsible for reviewing claims and finding buyers for valuable vanities.**',GOLD); return e
class HunterView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Log Claim',style=discord.ButtonStyle.success,custom_id='log_claim')
    async def a(self,i,b): await i.response.send_modal(LogClaim())
    @discord.ui.button(label='My Stats',style=discord.ButtonStyle.primary,custom_id='my_stats')
    async def b(self,i,bt): await i.response.send_message(embed=stats(i.guild,i.user.id),ephemeral=True)
    @discord.ui.button(label='My Claims',style=discord.ButtonStyle.secondary,custom_id='my_claims')
    async def c(self,i,bt):
        cs=uclaims(i.guild.id,i.user.id); e=emb('📌 My Claims',color=PURPLE); e.description='No claims yet.' if not cs else '\n'.join(f"`{x['id']}` • `discord.gg/{code(x.get('code'))}` • `{x.get('status','pending')}`" for x in sorted(cs,key=lambda x:x['created_ts'],reverse=True)[:20]); await i.response.send_message(embed=e,ephemeral=True)
class ManagerView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Edit Claim',style=discord.ButtonStyle.primary,custom_id='edit_claim')
    async def a(self,i,b):
        if await need_manager(i): await i.response.send_modal(EditClaim())
    @discord.ui.button(label='Set Status / Buyer',style=discord.ButtonStyle.primary,custom_id='control_claim')
    async def b(self,i,bt):
        if await need_manager(i): await i.response.send_modal(ControlClaim())
    @discord.ui.button(label='Delete Claim',style=discord.ButtonStyle.danger,custom_id='delete_claim')
    async def c(self,i,bt):
        if await need_manager(i): await i.response.send_modal(DeleteClaim())
    @discord.ui.button(label='Recent Claims',style=discord.ButtonStyle.secondary,custom_id='recent_claims')
    async def d(self,i,bt):
        if not await need_manager(i): return
        cs=sorted(claims(i.guild.id),key=lambda x:x.get('created_ts',0),reverse=True)[:20]; e=emb('🧾 Recent Claims',color=GOLD); e.description='No claims.' if not cs else '\n'.join(f"`{x['id']}` • <@{x['user_id']}> • `discord.gg/{code(x.get('code'))}` • `{x.get('status','pending')}`" for x in cs); await i.response.send_message(embed=e,ephemeral=True)
    @discord.ui.button(label='Add User Note',style=discord.ButtonStyle.secondary,custom_id='add_note')
    async def e(self,i,bt):
        if await need_manager(i): await i.response.send_modal(AddNote())
    @discord.ui.button(label='View User Notes',style=discord.ButtonStyle.secondary,custom_id='view_notes')
    async def f(self,i,bt):
        if await need_manager(i): await i.response.send_modal(ViewNotes())
    @discord.ui.button(label='Settings',style=discord.ButtonStyle.secondary,custom_id='settings')
    async def h(self,i,bt):
        if await need_manager(i): await i.response.send_modal(Settings())
    @discord.ui.button(label='Add Autorole',style=discord.ButtonStyle.success,custom_id='autorole')
    async def j(self,i,bt):
        if await need_manager(i): await i.response.send_modal(AutoRole())

@bot.tree.command(name='hunter_panel',description='Open hunter panel')
async def hp(i): await i.response.send_message(embed=hunter_embed(),view=HunterView(),ephemeral=True)
@bot.tree.command(name='manager_panel',description='Open manager panel')
async def mp(i):
    if await need_manager(i): await i.response.send_message(embed=manager_embed(),view=ManagerView(),ephemeral=True)
@bot.tree.command(name='post_hunter_panel',description='Post hunter panel')
@app_commands.default_permissions(manage_guild=True)
async def php(i):
    if await need_manager(i): await i.channel.send(embed=hunter_embed(),view=HunterView()); await i.response.send_message('Posted hunter panel.',ephemeral=True)
@bot.tree.command(name='post_manager_panel',description='Post manager panel')
@app_commands.default_permissions(manage_guild=True)
async def pmp(i):
    if await need_manager(i): await i.channel.send(embed=manager_embed(),view=ManagerView()); await i.response.send_message('Posted manager panel.',ephemeral=True)
@bot.tree.command(name='claim_stats',description='View claim stats')
async def stcmd(i,user:Optional[discord.Member]=None):
    target=user or i.user
    if target.id!=i.user.id and not manager(i.user): return await i.response.send_message('Only managers can view others.',ephemeral=True)
    await i.response.send_message(embed=stats(i.guild,target.id),ephemeral=True)
@bot.tree.command(name='help',description='Show help')
async def helpcmd(i):
    e=emb('📚 Vanity Bot Help','Claim-only bot. Hunters use the Hunter Panel. Managers use the Manager Panel to edit/delete/control claims and log notes.',PURPLE); await i.response.send_message(embed=e,ephemeral=True)
@bot.tree.command(name='vanity_manager_add_role',description='Give a role manager access')
@app_commands.default_permissions(manage_guild=True)
async def addrole(i,role:discord.Role):
    if not await need_admin(i): return
    c=cfg(i.guild.id); arr=[str(x) for x in c.get('manager_roles',[])]
    if str(role.id) not in arr: arr.append(str(role.id))
    c['manager_roles']=arr; save_cfg(i.guild.id,c); await i.response.send_message(f'Added {role.mention}.',ephemeral=True)
@bot.tree.command(name='vanity_manager_add_user',description='Give a user manager access')
@app_commands.default_permissions(manage_guild=True)
async def adduser(i,user:discord.Member):
    if not await need_admin(i): return
    c=cfg(i.guild.id); arr=[str(x) for x in c.get('manager_users',[])]
    if str(user.id) not in arr: arr.append(str(user.id))
    c['manager_users']=arr; save_cfg(i.guild.id,c); await i.response.send_message(f'Added {user.mention}.',ephemeral=True)
@bot.event
async def on_ready():
    bot.add_view(HunterView()); bot.add_view(ManagerView()); print(f'Logged in as {bot.user}'); print('RUNNING CLAIM NOTES MANAGER CONTROLS VERSION')
    try: synced=await bot.tree.sync(); print(f'Synced {len(synced)} slash commands.')
    except Exception as e: print(f'Slash sync failed: {e}')
if not TOKEN: raise RuntimeError('TOKEN missing')
bot.run(TOKEN)
