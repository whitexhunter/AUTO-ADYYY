import discord
import uuid
import os
import time
from datetime import datetime
import storage
import campaign_engine

_start_time = time.time()
ADMIN_IDS = []

def load_admin_ids():
    global ADMIN_IDS
    raw = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [x.strip() for x in raw.split(",") if x.strip()]

def is_admin(did):
    return str(did) in ADMIN_IDS

def _overview_embed():
    users = storage.get_all_users()
    accounts = storage.get_accounts()
    campaigns = storage.get_campaigns()
    subs = storage.get_subscriptions()

    total_sent = sum(c.get("messages_sent", 0) for c in campaigns)
    total_failed = sum(c.get("messages_failed", 0) for c in campaigns)
    confirmed = [s for s in subs if s["status"] == "confirmed"]
    pending = [s for s in subs if s["status"] == "pending"]
    revenue = sum(s.get("amount", 0) for s in confirmed)
    trials = sum(1 for u in users if u.get("trial_used"))

    dist = {"free": 0, "v1": 0, "v2": 0, "v3": 0, "lifetime": 0}
    for u in users:
        p = storage.get_user_effective_plan(u["discord_id"])
        dist[p] = dist.get(p, 0) + 1

    embed = discord.Embed(title="🛡️ Admin Panel", color=discord.Color.dark_gold(), timestamp=datetime.utcnow())
    embed.add_field(name="👥 Users", value=str(len(users)), inline=True)
    embed.add_field(name="👤 Accounts", value=str(len(accounts)), inline=True)
    embed.add_field(name="📨 Campaigns", value=str(len(campaigns)), inline=True)
    embed.add_field(name="✅ Sent", value=str(total_sent), inline=True)
    embed.add_field(name="❌ Failed", value=str(total_failed), inline=True)
    embed.add_field(name="💰 Revenue", value=f"${revenue}", inline=True)
    embed.add_field(name="📋 Confirmed", value=str(len(confirmed)), inline=True)
    embed.add_field(name="⏳ Pending", value=str(len(pending)), inline=True)
    embed.add_field(name="🎯 Trials", value=str(trials), inline=True)

    dist_str = "\n".join(f"{k.capitalize()}: {v}" for k, v in dist.items() if v > 0)
    embed.add_field(name="📊 Plans", value=dist_str or "N/A", inline=False)

    uptime = int(time.time() - _start_time)
    embed.add_field(name="⏱ Uptime", value=f"{uptime//3600}h {(uptime%3600)//60}m", inline=True)
    sizes = []
    for f in storage.FILES:
        p = os.path.join(storage.DATA_DIR, f)
        if os.path.exists(p):
            sizes.append(f"{f}: {os.path.getsize(p)/1024:.1f}KB")
    embed.add_field(name="💾 Files", value="\n".join(sizes) or "N/A", inline=True)
    embed.add_field(name="🛡️ Admins", value=str(len(ADMIN_IDS)), inline=True)
    return embed


class AdminPanelView(discord.ui.View):
    def __init__(self, did):
        super().__init__(timeout=300)
        self.did = did

    @discord.ui.button(label="📊 Overview", style=discord.ButtonStyle.primary, row=0)
    async def ov_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_overview_embed(), view=self)

    @discord.ui.button(label="👥 Users", style=discord.ButtonStyle.secondary, row=0)
    async def users_btn(self, btn, interaction):
        users = storage.get_all_users()
        embed = discord.Embed(title="👥 Users", color=discord.Color.blue())
        for u in users[:20]:
            embed.add_field(name=f"`{u['discord_id']}`", value=f"Plan: {storage.get_user_effective_plan(u['discord_id']).capitalize()} | Acc: {len(storage.get_user_accounts(u['discord_id']))}", inline=False)
        embed.set_footer(text=f"{min(len(users),20)}/{len(users)} shown")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📨 Campaigns", style=discord.ButtonStyle.secondary, row=0)
    async def camps_btn(self, btn, interaction):
        camps = storage.get_campaigns()
        embed = discord.Embed(title="📨 All Campaigns", color=discord.Color.purple())
        for c in camps[:15]:
            embed.add_field(name=f"{c.get('name','?')} ({c.get('status','?')})", value=f"User: `{c['discord_id'][:10]}...` | Sent: {c.get('messages_sent',0)}", inline=False)
        embed.set_footer(text=f"{min(len(camps),15)}/{len(camps)} shown")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="💰 Revenue", style=discord.ButtonStyle.danger, row=1)
    async def rev_btn(self, btn, interaction):
        subs = storage.get_subscriptions()
        confirmed = [s for s in subs if s["status"] == "confirmed"]
        pending = [s for s in subs if s["status"] == "pending"]
        embed = discord.Embed(title="💰 Revenue", color=discord.Color.gold())
        embed.add_field(name="Total", value=f"**${sum(s.get('amount',0) for s in confirmed)}**", inline=True)
        embed.add_field(name="Confirmed", value=str(len(confirmed)), inline=True)
        embed.add_field(name="Pending", value=str(len(pending)), inline=True)
        embed.add_field(name="Trials", value=str(sum(1 for u in storage.get_all_users() if u.get("trial_used"))), inline=True)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⚙️ System", style=discord.ButtonStyle.secondary, row=1)
    async def sys_btn(self, btn, interaction):
        uptime = int(time.time() - _start_time)
        embed = discord.Embed(title="⚙️ System", color=discord.Color.dark_blue())
        embed.add_field(name="⏱ Uptime", value=f"{uptime//3600}h {(uptime%3600)//60}m {uptime%60}s", inline=True)
        sizes = "\n".join(f"{f}: {os.path.getsize(os.path.join(storage.DATA_DIR, f))/1024:.1f}KB" for f in storage.FILES if os.path.exists(os.path.join(storage.DATA_DIR, f)))
        embed.add_field(name="💾 Files", value=sizes or "N/A", inline=False)
        embed.add_field(name="🛡️ Admins", value="\n".join(ADMIN_IDS) or "None", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔑 Gen Key", style=discord.ButtonStyle.success, row=2)
    async def gen_btn(self, btn, interaction):
        await interaction.response.send_modal(AdminGenKeyModal())

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def ref_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_overview_embed(), view=self)


class AdminGenKeyModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Generate Keys")
        self.add_item(discord.ui.InputText(label="Plan", placeholder="v1, v2, v3, lifetime", required=True))
        self.add_item(discord.ui.InputText(label="Count (1-50)", placeholder="5", required=True))

    async def callback(self, interaction):
        plan = self.children[0].value.strip().lower()
        try:
            count = max(1, min(50, int(self.children[1].value.strip())))
        except:
            await interaction.response.edit_message(embed=discord.Embed(title="❌ Invalid count", color=discord.Color.red()))
            return
        if plan not in ("v1", "v2", "v3", "lifetime"):
            await interaction.response.edit_message(embed=discord.Embed(title="❌ Invalid plan", color=discord.Color.red()))
            return

        import string, secrets
        keys = []
        for _ in range(count):
            k = f"HUNTER-{''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(4))}-{''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(4))}-{''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(4))}"
            storage.add_key({"key": k, "plan": plan, "created_at": datetime.utcnow().isoformat(), "created_by": str(interaction.user.id), "redeemed_by": None, "redeemed_at": None})
            keys.append(k)

        embed = discord.Embed(title=f"✅ {count} {plan.upper()} Keys", color=discord.Color.green())
        embed.add_field(name="Keys", value=f"```{chr(10).join(keys[:10])}{chr(10)+'...'+str(count-10)+' more' if count>10 else ''}```", inline=False)
        await interaction.response.edit_message(embed=embed)
