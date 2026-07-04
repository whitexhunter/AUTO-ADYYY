import discord
import uuid
import os
import time
from datetime import datetime
import storage
import campaign_engine

ADMIN_IDS = []

def _load_admin_ids():
    global ADMIN_IDS
    raw = os.getenv("ADMIN_IDS", "")
    if raw:
        ADMIN_IDS = [x.strip() for x in raw.split(",") if x.strip()]

def _is_admin(discord_id: str) -> bool:
    return discord_id in ADMIN_IDS

def _get_admin_overview_embed():
    users = storage.get_all_users()
    accounts = storage.get_accounts()
    campaigns = storage.get_campaigns()
    subs = storage.get_subscriptions()
    
    total_sent = sum(c.get("messages_sent", 0) for c in campaigns)
    total_failed = sum(c.get("messages_failed", 0) for c in campaigns)
    
    confirmed_subs = [s for s in subs if s["status"] == "confirmed"]
    pending_subs = [s for s in subs if s["status"] == "pending"]
    total_revenue = sum(s.get("amount", 0) for s in confirmed_subs)
    
    trials = sum(1 for u in users if u.get("trial_used"))
    
    # Plan distribution
    plan_dist = {"free": 0, "v1": 0, "v2": 0, "v3": 0, "lifetime": 0}
    for u in users:
        plan = storage.get_user_effective_plan(u["discord_id"])
        plan_dist[plan] = plan_dist.get(plan, 0) + 1
    
    embed = discord.Embed(
        title="🛡️ Admin Panel — Overview",
        color=discord.Color.dark_gold(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="👥 Total Users", value=str(len(users)), inline=True)
    embed.add_field(name="👤 Total Accounts", value=str(len(accounts)), inline=True)
    embed.add_field(name="📨 Total Campaigns", value=str(len(campaigns)), inline=True)
    embed.add_field(name="✅ Messages Sent", value=str(total_sent), inline=True)
    embed.add_field(name="❌ Messages Failed", value=str(total_failed), inline=True)
    embed.add_field(name="💰 Revenue (est.)", value=f"${total_revenue}", inline=True)
    embed.add_field(name="📋 Confirmed Subs", value=str(len(confirmed_subs)), inline=True)
    embed.add_field(name="⏳ Pending Subs", value=str(len(pending_subs)), inline=True)
    embed.add_field(name="🎯 Free Trials", value=str(trials), inline=True)
    
    dist_str = "\n".join([f"{k.capitalize()}: {v}" for k, v in plan_dist.items() if v > 0])
    embed.add_field(name="📊 Plan Distribution", value=dist_str or "N/A", inline=False)
    
    # System info
    uptime_seconds = time.time() - _admin_views_start_time
    uptime_str = f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m"
    
    data_sizes = []
    for fname in storage.FILES:
        path = os.path.join(storage.DATA_DIR, fname)
        if os.path.exists(path):
            size = os.path.getsize(path)
            data_sizes.append(f"{fname}: {size / 1024:.1f}KB")
    
    embed.add_field(name="⚙️ System", value=f"Uptime: {uptime_str}", inline=True)
    embed.add_field(name="💾 Data Files", value="\n".join(data_sizes) or "N/A", inline=True)
    embed.add_field(name="🛡️ Admins", value=f"{len(ADMIN_IDS)} configured", inline=True)
    
    return embed

_admin_views_start_time = time.time()


class AdminPanelView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=300)
        self.discord_id = discord_id
        _load_admin_ids()
    
    def _check(self, interaction: discord.Interaction) -> bool:
        if not _is_admin(str(interaction.user.id)):
            return False
        return True
    
    @discord.ui.button(label="📊 Overview", style=discord.ButtonStyle.primary, row=0)
    async def overview_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        embed = _get_admin_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="👥 Users", style=discord.ButtonStyle.secondary, row=0)
    async def users_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        users = storage.get_all_users()
        embed = discord.Embed(title="👥 All Users", color=discord.Color.blue())
        
        for u in users[:20]:
            plan = storage.get_user_effective_plan(u["discord_id"])
            acc_count = len(storage.get_user_accounts(u["discord_id"]))
            embed.add_field(
                name=f"`{u['discord_id']}`",
                value=f"Plan: {plan.capitalize()} | Accounts: {acc_count}",
                inline=False
            )
        
        embed.set_footer(text=f"Showing {min(len(users), 20)} of {len(users)} users")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="📨 All Campaigns", style=discord.ButtonStyle.secondary, row=0)
    async def all_campaigns_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        campaigns = storage.get_campaigns()
        embed = discord.Embed(title="📨 All Campaigns", color=discord.Color.purple())
        
        for c in campaigns[:15]:
            embed.add_field(
                name=f"{c.get('name', 'Unnamed')} ({c.get('status', '?')})",
                value=f"User: `{c['discord_id'][:10]}...` | Sent: {c.get('messages_sent', 0)} | Type: {c.get('type', '?')}",
                inline=False
            )
        
        embed.set_footer(text=f"Showing {min(len(campaigns), 15)} of {len(campaigns)} campaigns")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="💰 Revenue", style=discord.ButtonStyle.danger, row=1)
    async def revenue_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        subs = storage.get_subscriptions()
        embed = discord.Embed(title="💰 Revenue", color=discord.Color.gold())
        
        confirmed = [s for s in subs if s["status"] == "confirmed"]
        pending = [s for s in subs if s["status"] == "pending"]
        total_rev = sum(s.get("amount", 0) for s in confirmed)
        
        embed.add_field(name="Total Revenue", value=f"**${total_rev}**", inline=True)
        embed.add_field(name="Confirmed Subs", value=str(len(confirmed)), inline=True)
        embed.add_field(name="Pending Subs", value=str(len(pending)), inline=True)
        
        users = storage.get_all_users()
        trials = sum(1 for u in users if u.get("trial_used"))
        embed.set_footer(text=f"Free trials used: {trials}")
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="⚙️ System", style=discord.ButtonStyle.secondary, row=1)
    async def system_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        
        uptime_seconds = time.time() - _admin_views_start_time
        uptime_str = f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m {int(uptime_seconds % 60)}s"
        
        embed = discord.Embed(title="⚙️ System Info", color=discord.Color.dark_blue())
        embed.add_field(name="🕐 Uptime", value=uptime_str, inline=True)
        
        data_sizes = []
        for fname in storage.FILES:
            path = os.path.join(storage.DATA_DIR, fname)
            if os.path.exists(path):
                size = os.path.getsize(path)
                data_sizes.append(f"{fname}: {size / 1024:.1f}KB")
        embed.add_field(name="💾 Data Files", value="\n".join(data_sizes) or "N/A", inline=False)
        embed.add_field(name="🛡️ Admins", value="\n".join(ADMIN_IDS) or "None configured", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="🔑 Generate Key", style=discord.ButtonStyle.success, row=2)
    async def genkey_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        modal = AdminGenKeyModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh_btn(self, btn, interaction: discord.Interaction):
        if not self._check(interaction):
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        embed = _get_admin_overview_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class AdminGenKeyModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Generate License Keys")
        
        self.add_item(discord.ui.InputText(
            label="Plan",
            placeholder="v1, v2, v3, or lifetime",
            required=True,
            min_length=1,
            max_length=10
        ))
        self.add_item(discord.ui.InputText(
            label="Count (1-50)",
            placeholder="5",
            required=True,
            min_length=1,
            max_length=2
        ))
    
    async def callback(self, interaction: discord.Interaction):
        plan = self.children[0].value.strip().lower()
        count_str = self.children[1].value.strip()
        
        valid_plans = ["v1", "v2", "v3", "lifetime"]
        if plan not in valid_plans:
            embed = discord.Embed(
                title="❌ Invalid Plan",
                description=f"Valid plans: {', '.join(valid_plans)}",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed)
            return
        
        try:
            count = int(count_str)
            if count < 1 or count > 50:
                raise ValueError
        except:
            embed = discord.Embed(
                title="❌ Invalid Count",
                description="Enter a number between 1 and 50.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed)
            return
        
        keys_generated = []
        import string, secrets
        for _ in range(count):
            part1 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            part2 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            part3 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
            key_str = f"HUNTER-{part1}-{part2}-{part3}"
            
            key_data = {
                "key": key_str,
                "plan": plan,
                "created_at": datetime.utcnow().isoformat(),
                "created_by": str(interaction.user.id),
                "redeemed_by": None,
                "redeemed_at": None
            }
            storage.add_key(key_data)
            keys_generated.append(key_str)
        
        embed = discord.Embed(
            title=f"✅ Generated {count} Keys",
            description=f"Plan: **{plan.upper()}**",
            color=discord.Color.green()
        )
        
        # Show first 10 keys
        key_list = "\n".join(keys_generated[:10])
        if count > 10:
            key_list += f"\n... and {count - 10} more"
        embed.add_field(name="Keys", value=f"```{key_list}```", inline=False)
        
        await interaction.response.edit_message(embed=embed)
