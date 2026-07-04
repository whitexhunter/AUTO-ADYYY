import discord
import uuid
from datetime import datetime, timedelta
import storage
import discord_api
from crypto_utils import encrypt_token, decrypt_token
import campaign_engine


# ─── Helper Functions ──────────────────────────────

def _get_plan_name(plan: str) -> str:
    return storage.PLANS.get(plan, {}).get("name", plan.capitalize())

def _get_dashboard_embed(discord_id: str):
    user = storage.get_user(discord_id)
    if not user:
        user = {"discord_id": discord_id, "plan": "free", "trial_used": False}
        storage.upsert_user(discord_id, user)
    
    plan = storage.get_user_effective_plan(discord_id)
    accounts = storage.get_user_accounts(discord_id)
    campaigns = storage.get_user_campaigns(discord_id)
    max_accounts = storage.get_plan_max_accounts(plan)
    
    total_sent = sum(c.get("messages_sent", 0) for c in campaigns)
    total_failed = sum(c.get("messages_failed", 0) for c in campaigns)
    
    running = sum(1 for c in campaigns if c.get("status") == "running")
    paused = sum(1 for c in campaigns if c.get("status") == "paused")
    completed = sum(1 for c in campaigns if c.get("status") == "completed")
    
    embed = discord.Embed(
        title="📊 Dashboard",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="📋 Plan", value=f"**{_get_plan_name(plan)}**", inline=True)
    
    # Subscription info
    subs = storage.get_user_subscriptions(discord_id)
    active_sub = None
    for s in subs:
        if s["status"] == "confirmed":
            expires = datetime.fromisoformat(s["expires_at"])
            if expires > datetime.utcnow():
                active_sub = s
                break
    
    if active_sub:
        embed.add_field(name="⏳ Expires", value=f"<t:{int(datetime.fromisoformat(active_sub['expires_at']).timestamp())}:R>", inline=True)
    
    # Trial info
    if user.get("trial_active"):
        trial_exp = datetime.fromisoformat(user["trial_expires_at"])
        embed.add_field(name="🎯 Trial", value=f"Expires <t:{int(trial_exp.timestamp())}:R>", inline=True)
    
    embed.add_field(name="👤 Accounts", value=f"{len(accounts)}/{max_accounts}", inline=True)
    embed.add_field(name="📨 Campaigns", value=str(len(campaigns)), inline=True)
    embed.add_field(name="✅ Sent", value=str(total_sent), inline=True)
    embed.add_field(name="❌ Failed", value=str(total_failed), inline=True)
    embed.add_field(name="▶️ Running", value=str(running), inline=True)
    embed.add_field(name="⏸️ Paused", value=str(paused), inline=True)
    embed.add_field(name="✅ Completed", value=str(completed), inline=True)
    
    return embed


# ─── Main Panel View ───────────────────────────────

class MainPanelView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=300)
        self.discord_id = discord_id
    
    @discord.ui.button(label="📊 Dashboard", style=discord.ButtonStyle.primary, row=0)
    async def dashboard_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="👤 My Accounts", style=discord.ButtonStyle.secondary, row=0)
    async def my_accounts_btn(self, btn, interaction: discord.Interaction):
        accounts = storage.get_user_accounts(self.discord_id)
        plan = storage.get_user_effective_plan(self.discord_id)
        max_acc = storage.get_plan_max_accounts(plan)
        
        embed = discord.Embed(title="👤 My Accounts", color=discord.Color.green())
        embed.set_footer(text=f"Accounts: {len(accounts)}/{max_acc}")
        
        view = AccountsListView(self.discord_id)
        
        if not accounts:
            embed.description = "No accounts added yet."
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        for i, acc in enumerate(accounts[:10], 1):
            status = "✅ Online" if acc.get("valid") else "❌ Invalid"
            name = acc.get("username", "Unknown")
            embed.add_field(
                name=f"{i}. {name}",
                value=f"ID: `{acc['id'][:8]}...` | Status: {status}",
                inline=False
            )
        
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="📨 My Campaigns", style=discord.ButtonStyle.secondary, row=0)
    async def my_campaigns_btn(self, btn, interaction: discord.Interaction):
        campaigns = storage.get_user_campaigns(self.discord_id)
        embed = discord.Embed(title="📨 My Campaigns", color=discord.Color.purple())
        view = CampaignsListView(self.discord_id)
        
        if not campaigns:
            embed.description = "No campaigns yet. Create one!"
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        for i, camp in enumerate(campaigns[:10], 1):
            status_emoji = {
                "running": "▶️",
                "paused": "⏸️",
                "completed": "✅",
                "failed": "❌"
            }.get(camp.get("status", ""), "❓")
            
            camp_type = "📢 Channel" if camp["type"] == "channel" else "💬 DM Reply"
            sent = camp.get("messages_sent", 0)
            failed = camp.get("messages_failed", 0)
            
            embed.add_field(
                name=f"{status_emoji} {camp.get('name', 'Unnamed')}",
                value=f"Type: {camp_type} | Sent: {sent} | Failed: {failed}",
                inline=False
            )
        
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="➕ Add Account", style=discord.ButtonStyle.success, row=1)
    async def add_account_btn(self, btn, interaction: discord.Interaction):
        plan = storage.get_user_effective_plan(self.discord_id)
        max_acc = storage.get_plan_max_accounts(plan)
        accounts = storage.get_user_accounts(self.discord_id)
        
        if len(accounts) >= max_acc:
            embed = discord.Embed(
                title="❌ Account Limit Reached",
                description=f"Your plan ({_get_plan_name(plan)}) allows only **{max_acc}** accounts.\nUpgrade to add more!",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        modal = AddAccountModal(self.discord_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🆕 New Campaign", style=discord.ButtonStyle.success, row=1)
    async def new_campaign_btn(self, btn, interaction: discord.Interaction):
        accounts = storage.get_user_accounts(self.discord_id)
        if not accounts:
            embed = discord.Embed(
                title="❌ No Accounts",
                description="Add an account first before creating a campaign.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        view = NewCampaignTypeView(self.discord_id)
        embed = discord.Embed(
            title="🆕 New Campaign",
            description="Select campaign type:",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="💎 Plans & Buy", style=discord.ButtonStyle.danger, row=1)
    async def plans_btn(self, btn, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💎 Plans & Pricing",
            description="Choose a plan to unlock more features.",
            color=discord.Color.gold()
        )
        
        for pname, pdata in storage.PLANS.items():
            if pname == "free":
                price_str = "**$0** Free"
            elif pname == "lifetime":
                price_str = f"**${pdata['price']** One-Time"
            else:
                price_str = f"**${pdata['price']}/month**"
            
            features = "\n".join([f"• {f.replace('_', ' ').title()}" for f in pdata["features"]])
            embed.add_field(
                name=f"{pdata['name']} — {price_str}",
                value=f"Accounts: {pdata['accounts']}\n{features}",
                inline=False
            )
        
        embed.set_footer(text="Payments via Litecoin (LTC)")
        view = PlansView(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="🔑 Redeem Key", style=discord.ButtonStyle.secondary, row=2)
    async def redeem_btn(self, btn, interaction: discord.Interaction):
        modal = RedeemKeyModal(self.discord_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=self)


# ─── Accounts List View ────────────────────────────

class AccountsListView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=120)
        self.discord_id = discord_id
    
    @discord.ui.button(label="🗑️ Delete Account", style=discord.ButtonStyle.danger, row=0)
    async def delete_account_btn(self, btn, interaction: discord.Interaction):
        accounts = storage.get_user_accounts(self.discord_id)
        if not accounts:
            await interaction.response.send_message("No accounts to delete.", ephemeral=True)
            return
        
        options = [
            discord.SelectOption(
                label=f"{a.get('username', 'Unknown')} ({a['id'][:8]}...)",
                value=a["id"]
            ) for a in accounts[:25]
        ]
        
        view = AccountDeleteSelectView(self.discord_id, options)
        embed = discord.Embed(
            title="🗑️ Select Account to Delete",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class AccountDeleteSelectView(discord.ui.View):
    def __init__(self, discord_id: str, options: list):
        super().__init__(timeout=60)
        self.discord_id = discord_id
        self.add_item(AccountSelectMenu(options, self))
    
    @discord.ui.button(label="🔙 Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class AccountSelectMenu(discord.ui.Select):
    def __init__(self, options: list, parent_view):
        self.parent_view = parent_view
        super().__init__(placeholder="Choose an account...", options=options, min_values=1, max_values=1)
    
    async def callback(self, interaction: discord.Interaction):
        acc_id = self.values[0]
        success = storage.delete_account(acc_id, self.parent_view.discord_id)
        if success:
            embed = discord.Embed(title="✅ Account Deleted", color=discord.Color.green())
        else:
            embed = discord.Embed(title="❌ Failed to Delete", color=discord.Color.red())
        
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.parent_view.discord_id))


# ─── Add Account Modal ─────────────────────────────

class AddAccountModal(discord.ui.Modal):
    def __init__(self, discord_id: str):
        super().__init__(title="Add Discord Account")
        self.discord_id = discord_id
        self.add_item(discord.ui.InputText(
            label="Discord Token",
            placeholder="Paste your Discord user token here...",
            style=discord.InputTextStyle.long,
            required=True
        ))
    
    async def callback(self, interaction: discord.Interaction):
        token = self.children[0].value.strip()
        
        await interaction.response.defer(ephemeral=True)
        
        # Validate token via Discord API
        user_info = discord_api.validate_token(token)
        if not user_info:
            embed = discord.Embed(
                title="❌ Invalid Token",
                description="Could not validate this token. Make sure it's a valid Discord user token.",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed)
            return
        
        # Check plan limits
        plan = storage.get_user_effective_plan(self.discord_id)
        max_acc = storage.get_plan_max_accounts(plan)
        accounts = storage.get_user_accounts(self.discord_id)
        if len(accounts) >= max_acc:
            embed = discord.Embed(
                title="❌ Account Limit Reached",
                description=f"Your plan allows **{max_acc}** accounts. Upgrade to add more.",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed)
            return
        
        # Check for duplicate
        for acc in accounts:
            if acc.get("discord_user_id") == user_info["id"]:
                embed = discord.Embed(
                    title="❌ Already Added",
                    description="This Discord account is already linked.",
                    color=discord.Color.red()
                )
                await interaction.edit_original_response(embed=embed)
                return
        
        # Encrypt and store
        encrypted = encrypt_token(token)
        account_id = str(uuid.uuid4())
        
        account = {
            "id": account_id,
            "discord_id": self.discord_id,
            "discord_user_id": user_info["id"],
            "username": user_info.get("username", "Unknown"),
            "email": user_info.get("email", "Unknown"),
            "encrypted_token": encrypted,
            "valid": True,
            "added_at": datetime.utcnow().isoformat()
        }
        storage.add_account(account)
        
        embed = discord.Embed(
            title="✅ Account Added",
            description=f"**{user_info.get('username', 'Unknown')}** has been added successfully!",
            color=discord.Color.green()
        )
        embed.add_field(name="Account ID", value=f"`{account_id[:8]}...`", inline=True)
        embed.add_field(name="Username", value=user_info.get("username", "N/A"), inline=True)
        
        await interaction.edit_original_response(embed=embed)


# ─── Campaign Views ────────────────────────────────

class CampaignsListView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=120)
        self.discord_id = discord_id
    
    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.success, row=0)
    async def resume_campaign_btn(self, btn, interaction: discord.Interaction):
        campaigns = storage.get_user_campaigns(self.discord_id)
        paused_camps = [c for c in campaigns if c.get("status") == "paused" or c.get("status") == "failed"]
        if not paused_camps:
            embed = discord.Embed(title="ℹ️ No paused campaigns", color=discord.Color.blue())
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        options = [
            discord.SelectOption(
                label=f"{c.get('name', 'Unnamed')} ({c.get('messages_sent', 0)} sent)",
                value=c["id"]
            ) for c in paused_camps[:25]
        ]
        view = CampaignResumeSelectView(self.discord_id, options)
        embed = discord.Embed(title="▶️ Select Campaign to Resume", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.warning, row=0)
    async def pause_campaign_btn(self, btn, interaction: discord.Interaction):
        campaigns = storage.get_user_campaigns(self.discord_id)
        running_camps = [c for c in campaigns if c.get("status") == "running"]
        if not running_camps:
            embed = discord.Embed(title="ℹ️ No running campaigns", color=discord.Color.blue())
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        for c in running_camps:
            campaign_engine.pause_campaign(c["id"])
        
        embed = discord.Embed(
            title="⏸️ All Running Campaigns Paused",
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
    
    @discord.ui.button(label="🔄 View Details", style=discord.ButtonStyle.primary, row=0)
    async def view_details_btn(self, btn, interaction: discord.Interaction):
        campaigns = storage.get_user_campaigns(self.discord_id)
        if not campaigns:
            embed = discord.Embed(title="ℹ️ No campaigns", color=discord.Color.blue())
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        # Show latest campaign details
        camp = campaigns[-1]
        embed = discord.Embed(
            title=f"📋 {camp.get('name', 'Unnamed')}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Type", value="📢 Channel" if camp["type"] == "channel" else "💬 DM Reply", inline=True)
        embed.add_field(name="Status", value=camp.get("status", "unknown"), inline=True)
        embed.add_field(name="Sent", value=str(camp.get("messages_sent", 0)), inline=True)
        embed.add_field(name="Failed", value=str(camp.get("messages_failed", 0)), inline=True)
        if camp.get("replied_count"):
            embed.add_field(name="Replies", value=str(camp["replied_count"]), inline=True)
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class CampaignResumeSelectView(discord.ui.View):
    def __init__(self, discord_id: str, options: list):
        super().__init__(timeout=60)
        self.discord_id = discord_id
        self.add_item(CampaignResumeSelectMenu(options, self))
    
    @discord.ui.button(label="🔙 Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class CampaignResumeSelectMenu(discord.ui.Select):
    def __init__(self, options: list, parent_view):
        self.parent_view = parent_view
        super().__init__(placeholder="Select campaign...", options=options, min_values=1, max_values=1)
    
    async def callback(self, interaction: discord.Interaction):
        camp_id = self.values[0]
        campaign_engine.start_campaign(camp_id)
        embed = discord.Embed(title="▶️ Campaign Resumed!", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.parent_view.discord_id))


# ─── New Campaign Type View ────────────────────────

class NewCampaignTypeView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=120)
        self.discord_id = discord_id
    
    @discord.ui.button(label="📢 Channel Messaging", style=discord.ButtonStyle.primary, row=0)
    async def channel_campaign_btn(self, btn, interaction: discord.Interaction):
        accounts = storage.get_user_accounts(self.discord_id)
        options = [
            discord.SelectOption(
                label=f"{a.get('username', 'Unknown')}",
                value=a["id"],
                description=f"ID: {a['id'][:8]}..."
            ) for a in accounts[:25]
        ]
        view = CampaignAccountSelectView(self.discord_id, options, "channel")
        embed = discord.Embed(
            title="📢 Channel Messaging",
            description="Select which account to use:",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="💬 DM Auto-Reply", style=discord.ButtonStyle.primary, row=0)
    async def dm_campaign_btn(self, btn, interaction: discord.Interaction):
        plan = storage.get_user_effective_plan(self.discord_id)
        features = storage.get_plan_features(plan)
        if "dm_auto_reply" not in features:
            embed = discord.Embed(
                title="❌ Requires V3+",
                description="DM Auto-Reply requires **V3 ($7/month)** or **Lifetime ($30)** plan.",
                color=discord.Color.red()
            )
            embed.set_footer(text="Use /redeem or visit Plans & Buy to upgrade")
            await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
            return
        
        accounts = storage.get_user_accounts(self.discord_id)
        options = [
            discord.SelectOption(
                label=f"{a.get('username', 'Unknown')}",
                value=a["id"]
            ) for a in accounts[:25]
        ]
        view = CampaignAccountSelectView(self.discord_id, options, "dm_auto_reply")
        embed = discord.Embed(
            title="💬 DM Auto-Reply",
            description="Select which account to monitor:",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class CampaignAccountSelectView(discord.ui.View):
    def __init__(self, discord_id: str, options: list, camp_type: str):
        super().__init__(timeout=120)
        self.discord_id = discord_id
        self.camp_type = camp_type
        self.add_item(CampaignAccountSelectMenu(options, self))
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, btn, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🆕 New Campaign",
            description="Select campaign type:",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=NewCampaignTypeView(self.discord_id))


class CampaignAccountSelectMenu(discord.ui.Select):
    def __init__(self, options: list, parent_view):
        self.parent_view = parent_view
        super().__init__(placeholder="Select account...", options=options, min_values=1, max_values=1)
    
    async def callback(self, interaction: discord.Interaction):
        acc_id = self.values[0]
        if self.parent_view.camp_type == "channel":
            modal = ChannelCampaignModal(self.parent_view.discord_id, acc_id)
            await interaction.response.send_modal(modal)
        else:
            modal = DmCampaignModal(self.parent_view.discord_id, acc_id)
            await interaction.response.send_modal(modal)


# ─── Campaign Modals ───────────────────────────────

class ChannelCampaignModal(discord.ui.Modal):
    def __init__(self, discord_id: str, account_id: str):
        super().__init__(title="New Channel Campaign")
        self.discord_id = discord_id
        self.account_id = account_id
        
        self.add_item(discord.ui.InputText(
            label="Campaign Name",
            placeholder="My Campaign",
            max_length=50,
            required=True
        ))
        self.add_item(discord.ui.InputText(
            label="Channel IDs (comma-separated)",
            placeholder="123456789,987654321",
            required=True
        ))
        self.add_item(discord.ui.InputText(
            label="Messages (one per line)",
            placeholder="Hello world!\nThis is my second message!",
            style=discord.InputTextStyle.long,
            required=True
        ))
        self.add_item(discord.ui.InputText(
            label="Delay (seconds, min 1)",
            placeholder="1",
            required=False,
            value="1"
        ))
    
    async def callback(self, interaction: discord.Interaction):
        name = self.children[0].value.strip()
        channels_raw = self.children[1].value.strip()
        messages_raw = self.children[2].value.strip()
        delay_str = self.children[3].value.strip() or "1"
        
        channels = [c.strip() for c in channels_raw.split(",") if c.strip()]
        messages = [m.strip() for m in messages_raw.split("\n") if m.strip()]
        
        if not channels or not messages:
            embed = discord.Embed(
                title="❌ Missing Fields",
                description="You need at least 1 channel and 1 message.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
            return
        
        try:
            delay = max(int(delay_str), 1)
        except:
            delay = 1
        
        campaign_id = str(uuid.uuid4())
        campaign = {
            "id": campaign_id,
            "discord_id": self.discord_id,
            "account_id": self.account_id,
            "name": name,
            "type": "channel",
            "channels": channels,
            "messages": messages,
            "delay": delay,
            "status": "idle",
            "messages_sent": 0,
            "messages_failed": 0,
            "created_at": datetime.utcnow().isoformat()
        }
        storage.add_campaign(campaign)
        
        # Auto-start
        campaign_engine.start_campaign(campaign_id)
        
        embed = discord.Embed(
            title="✅ Campaign Created!",
            description=f"**{name}** is now running.",
            color=discord.Color.green()
        )
        embed.add_field(name="Channels", value=str(len(channels)), inline=True)
        embed.add_field(name="Messages", value=str(len(messages)), inline=True)
        embed.add_field(name="Delay", value=f"{delay}s", inline=True)
        
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class DmCampaignModal(discord.ui.Modal):
    def __init__(self, discord_id: str, account_id: str):
        super().__init__(title="New DM Auto-Reply")
        self.discord_id = discord_id
        self.account_id = account_id
        
        self.add_item(discord.ui.InputText(
            label="Campaign Name",
            placeholder="Auto-Reply Campaign",
            max_length=50,
            required=True
        ))
        self.add_item(discord.ui.InputText(
            label="Reply Messages (one per line)",
            placeholder="Thanks for your message!\nI'll get back to you soon.",
            style=discord.InputTextStyle.long,
            required=True
        ))
        self.add_item(discord.ui.InputText(
            label="Keywords (comma-separated, optional)",
            placeholder="help,support,hello",
            required=False
        ))
    
    async def callback(self, interaction: discord.Interaction):
        name = self.children[0].value.strip()
        messages_raw = self.children[1].value.strip()
        keywords_raw = self.children[2].value.strip()
        
        messages = [m.strip() for m in messages_raw.split("\n") if m.strip()]
        keywords = [k.strip().lower() for k in keywords_raw.split(",") if k.strip()] if keywords_raw else []
        
        if not messages:
            embed = discord.Embed(
                title="❌ Missing Messages",
                description="You need at least 1 reply message.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
            return
        
        campaign_id = str(uuid.uuid4())
        campaign = {
            "id": campaign_id,
            "discord_id": self.discord_id,
            "account_id": self.account_id,
            "name": name,
            "type": "dm_auto_reply",
            "messages": messages,
            "keywords": keywords,
            "status": "running",
            "replied_count": 0,
            "last_replied_id": "",
            "created_at": datetime.utcnow().isoformat()
        }
        storage.add_campaign(campaign)
        
        # Start the DM responder for this user
        campaign_engine.start_dm_responder(self.discord_id)
        
        embed = discord.Embed(
            title="✅ DM Auto-Reply Created!",
            description=f"**{name}** is now active.",
            color=discord.Color.green()
        )
        embed.add_field(name="Reply Messages", value=str(len(messages)), inline=True)
        if keywords:
            embed.add_field(name="Keywords", value=", ".join(keywords), inline=True)
        else:
            embed.add_field(name="Keywords", value="All messages", inline=True)
        
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


# ─── Plans & Buy View ──────────────────────────────

class PlansView(discord.ui.View):
    def __init__(self, discord_id: str):
        super().__init__(timeout=120)
        self.discord_id = discord_id
    
    @discord.ui.button(label="💳 Buy V1 ($3)", style=discord.ButtonStyle.primary, row=0)
    async def buy_v1(self, btn, interaction: discord.Interaction):
        await self._create_payment(interaction, "v1")
    
    @discord.ui.button(label="💳 Buy V2 ($5)", style=discord.ButtonStyle.primary, row=0)
    async def buy_v2(self, btn, interaction: discord.Interaction):
        await self._create_payment(interaction, "v2")
    
    @discord.ui.button(label="💳 Buy V3 ($7)", style=discord.ButtonStyle.primary, row=0)
    async def buy_v3(self, btn, interaction: discord.Interaction):
        await self._create_payment(interaction, "v3")
    
    @discord.ui.button(label="💳 Buy Lifetime ($30)", style=discord.ButtonStyle.danger, row=1)
    async def buy_lifetime(self, btn, interaction: discord.Interaction):
        await self._create_payment(interaction, "lifetime")
    
    @discord.ui.button(label="🎯 Free Trial (10min V3)", style=discord.ButtonStyle.success, row=1)
    async def free_trial(self, btn, interaction: discord.Interaction):
        user = storage.get_user(self.discord_id)
        if user and user.get("trial_used"):
            embed = discord.Embed(
                title="❌ Trial Already Used",
                description="You've already claimed your free trial.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        
        expiry = datetime.utcnow() + timedelta(minutes=10)
        storage.upsert_user(self.discord_id, {
            "trial_active": True,
            "trial_expires_at": expiry.isoformat(),
            "trial_used": True
        })
        
        embed = discord.Embed(
            title="🎯 Free Trial Activated!",
            description=f"V3 trial expires <t:{int(expiry.timestamp())}:R>",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
    
    async def _create_payment(self, interaction, plan: str):
        plan_data = storage.PLANS[plan]
        sub_id = str(uuid.uuid4())
        
        # Simple LTC address placeholder — you'll configure real addresses
        ltc_address = f"L{''.join(plan_data['name'].upper() for _ in range(1))}xxxxxxxxxxxxxxxxxxxxxx"
        
        sub = {
            "id": sub_id,
            "discord_id": self.discord_id,
            "plan": plan,
            "amount": plan_data["price"],
            "ltc_address": ltc_address,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(hours=2)).isoformat()
        }
        storage.add_subscription(sub)
        
        embed = discord.Embed(
            title="💳 Payment Request",
            description=f"Purchase **{plan_data['name']}**",
            color=discord.Color.gold()
        )
        embed.add_field(name="Amount", value=f"**${plan_data['price']}** (in LTC)", inline=True)
        embed.add_field(name="LTC Address", value=f"`{ltc_address}`", inline=False)
        embed.add_field(name="Subscription ID", value=f"`{sub_id[:8]}...`", inline=True)
        embed.add_field(name="Expires", value=f"<t:{int((datetime.utcnow() + timedelta(hours=2)).timestamp())}:R>", inline=True)
        embed.set_footer(text="Send exact LTC amount. Contact admin after payment.")
        
        await interaction.response.edit_message(embed=embed, view=self)


# ─── Redeem Key Modal ──────────────────────────────

class RedeemKeyModal(discord.ui.Modal):
    def __init__(self, discord_id: str):
        super().__init__(title="Redeem License Key")
        self.discord_id = discord_id
        self.add_item(discord.ui.InputText(
            label="License Key",
            placeholder="HUNTER-XXXX-XXXX-XXXX",
            required=True,
            min_length=10,
            max_length=50
        ))
    
    async def callback(self, interaction: discord.Interaction):
        key_str = self.children[0].value.strip()
        
        result = storage.redeem_key(key_str, self.discord_id)
        
        if not result:
            embed = discord.Embed(
                title="❌ Invalid or Already Used Key",
                description="This key doesn't exist or has already been redeemed.",
                color=discord.Color.red()
            )
        else:
            plan = result["plan"]
            plan_data = storage.PLANS.get(plan, storage.PLANS["free"])
            
            # Create a subscription
            sub_id = str(uuid.uuid4())
            
            if plan == "lifetime":
                expires_at = "2099-12-31T23:59:59"
            else:
                expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()
            
            sub = {
                "id": sub_id,
                "discord_id": self.discord_id,
                "plan": plan,
                "amount": 0,
                "status": "confirmed",
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": expires_at
            }
            storage.add_subscription(sub)
            
            embed = discord.Embed(
                title="✅ Key Redeemed!",
                description=f"You now have **{plan_data['name']}** plan!",
                color=discord.Color.green()
            )
            embed.add_field(name="Plan", value=plan_data["name"], inline=True)
            embed.add_field(name="Max Accounts", value=str(plan_data["accounts"]), inline=True)
        
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))
