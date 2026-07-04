import discord
import uuid
from datetime import datetime, timedelta
import storage
import discord_api
from crypto_utils import encrypt_token
import campaign_engine


def _get_dashboard_embed(discord_id):
    user = storage.get_user(discord_id)
    if not user:
        user = {"discord_id": discord_id}
        storage.upsert_user(discord_id, user)

    plan = storage.get_user_effective_plan(discord_id)
    accounts = storage.get_user_accounts(discord_id)
    campaigns = storage.get_user_campaigns(discord_id)
    max_acc = storage.get_plan_max_accounts(plan)

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
    embed.add_field(name="📋 Plan", value=f"**{storage.get_plan_name(plan)}**", inline=True)

    # Active sub info
    for s in storage.get_user_subscriptions(discord_id):
        if s["status"] == "confirmed":
            try:
                expires = datetime.fromisoformat(s["expires_at"])
                if expires > datetime.utcnow():
                    embed.add_field(
                        name="⏳ Expires",
                        value=f"<t:{int(expires.timestamp())}:R>",
                        inline=True
                    )
                    break
            except:
                pass

    # Trial info
    if user and user.get("trial_active"):
        try:
            te = datetime.fromisoformat(user["trial_expires_at"])
            if te > datetime.utcnow():
                embed.add_field(
                    name="🎯 Trial",
                    value=f"<t:{int(te.timestamp())}:R>",
                    inline=True
                )
        except:
            pass

    # Check active access
    has_access = _user_has_access(discord_id)
    if not has_access:
        embed.add_field(
            name="⛔ Status",
            value="**Subscription Expired** — Redeem a key or purchase to continue.",
            inline=False
        )
        embed.color = discord.Color.red()
    else:
        embed.add_field(name="✅ Status", value="**Active**", inline=True)

    embed.add_field(name="👤 Accounts", value=f"{len(accounts)}/{max_acc}", inline=True)
    embed.add_field(name="📨 Campaigns", value=str(len(campaigns)), inline=True)
    embed.add_field(name="✅ Sent", value=str(total_sent), inline=True)
    embed.add_field(name="❌ Failed", value=str(total_failed), inline=True)
    embed.add_field(name="▶️ Running", value=str(running), inline=True)
    embed.add_field(name="⏸️ Paused", value=str(paused), inline=True)
    embed.add_field(name="✅ Completed", value=str(completed), inline=True)

    return embed


def _user_has_access(discord_id):
    """Check if user has active subscription or trial."""
    user = storage.get_user(discord_id)
    if user and user.get("trial_active"):
        try:
            if datetime.fromisoformat(user["trial_expires_at"]) > datetime.utcnow():
                return True
        except:
            pass
    for sub in storage.get_user_subscriptions(discord_id):
        if sub["status"] == "confirmed":
            try:
                if sub["plan"] == "lifetime":
                    return True
                if datetime.fromisoformat(sub["expires_at"]) > datetime.utcnow():
                    return True
            except:
                pass
    return False


def get_panel_view(discord_id, has_access):
    """Return the correct embed and view based on user's access level."""
    embed = _get_dashboard_embed(discord_id)

    if has_access:
        return embed, MainPanelView(discord_id)
    else:
        user = storage.get_user(discord_id) or {}
        trial_used = user.get("trial_used", False)
        return embed, LockedPanelView(discord_id, trial_used)


# ─── LOCKED PANEL (No Subscription) ───────────────

class LockedPanelView(discord.ui.View):
    """Shown to users with no active subscription — only redeem/trial options."""
    def __init__(self, discord_id, trial_used=False):
        super().__init__(timeout=300)
        self.discord_id = discord_id
        self.trial_used = trial_used

    @discord.ui.button(label="🔑 Redeem Key", style=discord.ButtonStyle.primary, row=0)
    async def redeem_btn(self, btn, interaction: discord.Interaction):
        await interaction.response.send_modal(RedeemKeyModal(self.discord_id))

    @discord.ui.button(label="🎯 Free Trial (10min V3)", style=discord.ButtonStyle.success, row=0)
    async def trial_btn(self, btn, interaction: discord.Interaction):
        if self.trial_used:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="❌ Trial Already Used",
                    description="You have already used your free trial. Redeem a license key to continue.",
                    color=discord.Color.red()
                ),
                view=self
            )
            return
        exp = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        storage.upsert_user(self.discord_id, {
            "trial_active": True,
            "trial_expires_at": exp,
            "trial_used": True
        })
        embed = discord.Embed(
            title="🎯 Free Trial Activated!",
            description=f"**10 minutes of V3** — expires <t:{int((datetime.utcnow()+timedelta(minutes=10)).timestamp())}:R>",
            color=discord.Color.green()
        )
        # Refresh to full panel
        full_embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=full_embed, view=MainPanelView(self.discord_id))

    @discord.ui.button(label="💎 Plans & Buy", style=discord.ButtonStyle.danger, row=1)
    async def plans_btn(self, btn, interaction: discord.Interaction):
        embed = discord.Embed(title="💎 Plans & Pricing", color=discord.Color.gold())
        for pname, pdata in storage.PLANS.items():
            price = (
                "**$0 Free**" if pname == "free" else
                "**$30 One-Time**" if pname == "lifetime" else
                f"**${pdata['price']}/month**"
            )
            features = "\n".join(f"• {f.replace('_',' ').title()}" for f in pdata["features"])
            embed.add_field(
                name=f"{pdata['name']} — {price}",
                value=f"Accounts: {pdata['accounts']}\n{features}",
                inline=False
            )
        embed.set_footer(text="Contact admin to purchase and get a license key.")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, btn, interaction: discord.Interaction):
        embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=embed, view=self)


# ─── FULL PANEL (Active Subscription) ─────────────

class MainPanelView(discord.ui.View):
    def __init__(self, discord_id):
        super().__init__(timeout=300)
        self.discord_id = discord_id

    @discord.ui.button(label="📊 Dashboard", style=discord.ButtonStyle.primary, row=0)
    async def dashboard_btn(self, btn, interaction):
        await interaction.response.edit_message(
            embed=_get_dashboard_embed(self.discord_id), view=self
        )

    @discord.ui.button(label="👤 My Accounts", style=discord.ButtonStyle.secondary, row=0)
    async def accounts_btn(self, btn, interaction):
        accounts = storage.get_user_accounts(self.discord_id)
        plan = storage.get_user_effective_plan(self.discord_id)
        max_acc = storage.get_plan_max_accounts(plan)
        embed = discord.Embed(title="👤 My Accounts", color=discord.Color.green())
        embed.set_footer(text=f"Accounts: {len(accounts)}/{max_acc}")
        if not accounts:
            embed.description = "No accounts added yet."
        else:
            for i, a in enumerate(accounts[:10], 1):
                status = "✅ Online" if a.get("valid") else "❌ Invalid"
                embed.add_field(
                    name=f"{i}. {a.get('username', 'Unknown')}",
                    value=f"ID: `{a['id'][:8]}...` | {status}",
                    inline=False
                )
        await interaction.response.edit_message(embed=embed, view=AccountsListView(self.discord_id))

    @discord.ui.button(label="📨 My Campaigns", style=discord.ButtonStyle.secondary, row=0)
    async def campaigns_btn(self, btn, interaction):
        campaigns = storage.get_user_campaigns(self.discord_id)
        embed = discord.Embed(title="📨 My Campaigns", color=discord.Color.purple())
        if not campaigns:
            embed.description = "No campaigns yet."
        else:
            for i, c in enumerate(campaigns[:10], 1):
                emoji = {"running": "▶️", "paused": "⏸️", "completed": "✅", "failed": "❌"}.get(c.get("status",""), "❓")
                ctype = "📢 Channel" if c["type"] == "channel" else "💬 DM Reply"
                embed.add_field(
                    name=f"{emoji} {c.get('name','Unnamed')}",
                    value=f"{ctype} | Sent: {c.get('messages_sent',0)} | Failed: {c.get('messages_failed',0)}",
                    inline=False
                )
        await interaction.response.edit_message(embed=embed, view=CampaignsListView(self.discord_id))

    @discord.ui.button(label="➕ Add Account", style=discord.ButtonStyle.success, row=1)
    async def add_account_btn(self, btn, interaction):
        plan = storage.get_user_effective_plan(self.discord_id)
        max_acc = storage.get_plan_max_accounts(plan)
        if len(storage.get_user_accounts(self.discord_id)) >= max_acc:
            await interaction.response.edit_message(
                embed=discord.Embed(title="❌ Limit Reached", description=f"Your plan allows {max_acc} accounts.", color=discord.Color.red()),
                view=self
            )
            return
        await interaction.response.send_modal(AddAccountModal(self.discord_id))

    @discord.ui.button(label="🆕 New Campaign", style=discord.ButtonStyle.success, row=1)
    async def new_campaign_btn(self, btn, interaction):
        if not storage.get_user_accounts(self.discord_id):
            await interaction.response.edit_message(
                embed=discord.Embed(title="❌ No Accounts", description="Add an account first.", color=discord.Color.red()),
                view=self
            )
            return
        embed = discord.Embed(title="🆕 New Campaign", description="Select type:", color=discord.Color.blue())
        await interaction.response.edit_message(embed=embed, view=NewCampaignTypeView(self.discord_id))

    @discord.ui.button(label="💎 Plans & Buy", style=discord.ButtonStyle.danger, row=1)
    async def plans_btn(self, btn, interaction):
        embed = discord.Embed(title="💎 Plans & Pricing", color=discord.Color.gold())
        for pname, pdata in storage.PLANS.items():
            price = (
                "**$0 Free**" if pname == "free" else
                "**$30 One-Time**" if pname == "lifetime" else
                f"**${pdata['price']}/month**"
            )
            features = "\n".join(f"• {f.replace('_',' ').title()}" for f in pdata["features"])
            embed.add_field(
                name=f"{pdata['name']} — {price}",
                value=f"Accounts: {pdata['accounts']}\n{features}",
                inline=False
            )
        embed.set_footer(text="Contact admin to purchase. Use /redeem to activate a key.")
        await interaction.response.edit_message(embed=embed, view=PlansView(self.discord_id))

    @discord.ui.button(label="🔑 Redeem Key", style=discord.ButtonStyle.secondary, row=2)
    async def redeem_btn(self, btn, interaction):
        await interaction.response.send_modal(RedeemKeyModal(self.discord_id))

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh_btn(self, btn, interaction):
        await interaction.response.edit_message(
            embed=_get_dashboard_embed(self.discord_id), view=self
        )


# ─── ACCOUNTS ────────────────────────────────────

class AccountsListView(discord.ui.View):
    def __init__(self, discord_id):
        super().__init__(timeout=120)
        self.discord_id = discord_id

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.danger, row=0)
    async def delete_btn(self, btn, interaction):
        accounts = storage.get_user_accounts(self.discord_id)
        if not accounts:
            await interaction.response.send_message("No accounts.", ephemeral=True)
            return
        opts = [discord.SelectOption(label=f"{a.get('username','?')} ({a['id'][:8]}...)", value=a["id"]) for a in accounts[:25]]
        await interaction.response.edit_message(view=AccountDeleteSelectView(self.discord_id, opts))

    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_get_dashboard_embed(self.discord_id), view=MainPanelView(self.discord_id))


class AccountDeleteSelectView(discord.ui.View):
    def __init__(self, discord_id, options):
        super().__init__(timeout=60)
        self.discord_id = discord_id
        self.add_item(AccountSelect(options, self))

    @discord.ui.button(label="🔙 Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_get_dashboard_embed(self.discord_id), view=MainPanelView(self.discord_id))


class AccountSelect(discord.ui.Select):
    def __init__(self, options, parent):
        self.parent = parent
        super().__init__(placeholder="Select account...", options=options)

    async def callback(self, interaction):
        ok = storage.delete_account(self.values[0], self.parent.discord_id)
        embed = discord.Embed(title="✅ Deleted" if ok else "❌ Failed", color=discord.Color.green() if ok else discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.parent.discord_id))


# ─── ADD ACCOUNT MODAL ───────────────────────────

class AddAccountModal(discord.ui.Modal):
    def __init__(self, discord_id):
        super().__init__(title="Add Discord Account")
        self.discord_id = discord_id
        self.add_item(discord.ui.InputText(label="Discord Token", placeholder="Paste your Discord user token...", style=discord.InputTextStyle.long, required=True))

    async def callback(self, interaction):
        token = self.children[0].value.strip()
        await interaction.response.defer(ephemeral=True)
        info = discord_api.validate_token(token)
        if not info:
            await interaction.edit_original_response(embed=discord.Embed(title="❌ Invalid Token", color=discord.Color.red()))
            return
        plan = storage.get_user_effective_plan(self.discord_id)
        max_acc = storage.get_plan_max_accounts(plan)
        accounts = storage.get_user_accounts(self.discord_id)
        if len(accounts) >= max_acc:
            await interaction.edit_original_response(embed=discord.Embed(title="❌ Limit Reached", description=f"Max {max_acc} accounts on {storage.get_plan_name(plan)}.", color=discord.Color.red()))
            return
        for a in accounts:
            if a.get("discord_user_id") == info["id"]:
                await interaction.edit_original_response(embed=discord.Embed(title="❌ Already Added", color=discord.Color.red()))
                return
        aid = str(uuid.uuid4())
        storage.add_account({
            "id": aid, "discord_id": self.discord_id, "discord_user_id": info["id"],
            "username": info.get("username", "?"), "email": info.get("email", "?"),
            "encrypted_token": encrypt_token(token), "valid": True,
            "added_at": datetime.utcnow().isoformat()
        })
        await interaction.edit_original_response(embed=discord.Embed(title=f"✅ {info['username']} Added!", color=discord.Color.green()))


# ─── CAMPAIGNS ───────────────────────────────────

class CampaignsListView(discord.ui.View):
    def __init__(self, discord_id):
        super().__init__(timeout=120)
        self.discord_id = discord_id

    @discord.ui.button(label="▶️ Resume", style=discord.ButtonStyle.success, row=0)
    async def resume_btn(self, btn, interaction):
        camps = [c for c in storage.get_user_campaigns(self.discord_id) if c.get("status") in ("paused", "failed")]
        if not camps:
            await interaction.response.send_message("No paused campaigns.", ephemeral=True)
            return
        opts = [discord.SelectOption(label=f"{c.get('name','?')} ({c.get('messages_sent',0)} sent)", value=c["id"]) for c in camps[:25]]
        await interaction.response.edit_message(view=CampaignResumeSelectView(self.discord_id, opts))

    @discord.ui.button(label="⏸️ Pause All", style=discord.ButtonStyle.grey, row=0)
    async def pause_btn(self, btn, interaction):
        for c in storage.get_user_campaigns(self.discord_id):
            if c.get("status") == "running":
                campaign_engine.pause_campaign(c["id"])
        await interaction.response.edit_message(embed=discord.Embed(title="⏸️ All Paused", color=discord.Color.orange()), view=MainPanelView(self.discord_id))

    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_get_dashboard_embed(self.discord_id), view=MainPanelView(self.discord_id))


class CampaignResumeSelectView(discord.ui.View):
    def __init__(self, discord_id, options):
        super().__init__(timeout=60)
        self.discord_id = discord_id
        self.add_item(CampaignResumeSelect(options, self))

    @discord.ui.button(label="🔙 Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_get_dashboard_embed(self.discord_id), view=MainPanelView(self.discord_id))


class CampaignResumeSelect(discord.ui.Select):
    def __init__(self, options, parent):
        self.parent = parent
        super().__init__(placeholder="Select...", options=options)

    async def callback(self, interaction):
        campaign_engine.start_campaign(self.values[0])
        await interaction.response.edit_message(embed=discord.Embed(title="▶️ Resumed!", color=discord.Color.green()), view=MainPanelView(self.parent.discord_id))


# ─── NEW CAMPAIGN ────────────────────────────────

class NewCampaignTypeView(discord.ui.View):
    def __init__(self, discord_id):
        super().__init__(timeout=120)
        self.discord_id = discord_id

    @discord.ui.button(label="📢 Channel Messaging", style=discord.ButtonStyle.primary, row=0)
    async def ch_btn(self, btn, interaction):
        opts = [discord.SelectOption(label=a.get("username","?"), value=a["id"]) for a in storage.get_user_accounts(self.discord_id)[:25]]
        await interaction.response.edit_message(embed=discord.Embed(title="Select Account", color=discord.Color.blue()), view=CampaignAccountSelectView(self.discord_id, opts, "channel"))

    @discord.ui.button(label="💬 DM Auto-Reply", style=discord.ButtonStyle.primary, row=0)
    async def dm_btn(self, btn, interaction):
        plan = storage.get_user_effective_plan(self.discord_id)
        if "dm_auto_reply" not in storage.get_plan_features(plan):
            await interaction.response.edit_message(embed=discord.Embed(title="❌ V3+ Required", description="DM Auto-Reply needs V3 or Lifetime.", color=discord.Color.red()), view=MainPanelView(self.discord_id))
            return
        opts = [discord.SelectOption(label=a.get("username","?"), value=a["id"]) for a in storage.get_user_accounts(self.discord_id)[:25]]
        await interaction.response.edit_message(embed=discord.Embed(title="Select Account", color=discord.Color.blue()), view=CampaignAccountSelectView(self.discord_id, opts, "dm_auto_reply"))

    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_get_dashboard_embed(self.discord_id), view=MainPanelView(self.discord_id))


class CampaignAccountSelectView(discord.ui.View):
    def __init__(self, discord_id, options, camp_type):
        super().__init__(timeout=120)
        self.discord_id = discord_id
        self.camp_type = camp_type
        self.add_item(CampaignAccountSelect(options, self))

    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=discord.Embed(title="🆕 New Campaign", color=discord.Color.blue()), view=NewCampaignTypeView(self.discord_id))


class CampaignAccountSelect(discord.ui.Select):
    def __init__(self, options, parent):
        self.parent = parent
        super().__init__(placeholder="Select account...", options=options)

    async def callback(self, interaction):
        acc_id = self.values[0]
        if self.parent.camp_type == "channel":
            await interaction.response.send_modal(ChannelCampaignModal(self.parent.discord_id, acc_id))
        else:
            await interaction.response.send_modal(DmCampaignModal(self.parent.discord_id, acc_id))


class ChannelCampaignModal(discord.ui.Modal):
    def __init__(self, did, aid):
        super().__init__(title="New Channel Campaign")
        self.discord_id = did
        self.account_id = aid
        self.add_item(discord.ui.InputText(label="Name", placeholder="My Campaign", max_length=50, required=True))
        self.add_item(discord.ui.InputText(label="Channel IDs (comma-separated)", placeholder="123456789,987654321", required=True))
        self.add_item(discord.ui.InputText(label="Messages (one per line)", style=discord.InputTextStyle.long, placeholder="Hello!\nSecond message!", required=True))
        self.add_item(discord.ui.InputText(label="Delay (seconds, min 1)", placeholder="1", required=False, value="1"))

    async def callback(self, interaction):
        name = self.children[0].value.strip()
        channels = [c.strip() for c in self.children[1].value.split(",") if c.strip()]
        messages = [m.strip() for m in self.children[2].value.split("\n") if m.strip()]
        delay = max(int(self.children[3].value or "1"), 1)
        if not channels or not messages:
            await interaction.response.edit_message(embed=discord.Embed(title="❌ Missing fields", color=discord.Color.red()))
            return
        cid = str(uuid.uuid4())
        storage.add_campaign({
            "id": cid, "discord_id": self.discord_id, "account_id": self.account_id,
            "name": name, "type": "channel", "channels": channels,
            "messages": messages, "delay": delay,
            "status": "idle", "messages_sent": 0, "messages_failed": 0,
            "created_at": datetime.utcnow().isoformat()
        })
        campaign_engine.start_campaign(cid)
        embed = discord.Embed(title=f"✅ {name} Running!", color=discord.Color.green())
        embed.add_field(name="Channels", value=str(len(channels)), inline=True)
        embed.add_field(name="Messages", value=str(len(messages)), inline=True)
        embed.add_field(name="Delay", value=f"{delay}s", inline=True)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


class DmCampaignModal(discord.ui.Modal):
    def __init__(self, did, aid):
        super().__init__(title="New DM Auto-Reply")
        self.discord_id = did
        self.account_id = aid
        self.add_item(discord.ui.InputText(label="Name", placeholder="Auto-Reply", max_length=50, required=True))
        self.add_item(discord.ui.InputText(label="Reply Messages (one per line)", style=discord.InputTextStyle.long, placeholder="Thanks!\nI'll reply soon.", required=True))
        self.add_item(discord.ui.InputText(label="Keywords (comma-sep, optional)", placeholder="help,support,hello", required=False))

    async def callback(self, interaction):
        name = self.children[0].value.strip()
        messages = [m.strip() for m in self.children[1].value.split("\n") if m.strip()]
        keywords = [k.strip().lower() for k in self.children[2].value.split(",") if k.strip()] if self.children[2].value.strip() else []
        if not messages:
            await interaction.response.edit_message(embed=discord.Embed(title="❌ Need messages", color=discord.Color.red()))
            return
        cid = str(uuid.uuid4())
        storage.add_campaign({
            "id": cid, "discord_id": self.discord_id, "account_id": self.account_id,
            "name": name, "type": "dm_auto_reply", "messages": messages,
            "keywords": keywords, "status": "running", "replied_count": 0,
            "last_replied_id": "", "created_at": datetime.utcnow().isoformat()
        })
        campaign_engine.start_dm_responder(self.discord_id)
        embed = discord.Embed(title=f"✅ {name} Active!", color=discord.Color.green())
        embed.add_field(name="Replies", value=str(len(messages)), inline=True)
        embed.add_field(name="Keywords", value=", ".join(keywords) if keywords else "All", inline=True)
        await interaction.response.edit_message(embed=embed, view=MainPanelView(self.discord_id))


# ─── PLANS VIEW ──────────────────────────────────

class PlansView(discord.ui.View):
    def __init__(self, discord_id):
        super().__init__(timeout=120)
        self.discord_id = discord_id

    @discord.ui.button(label="💳 V1 ($3)", style=discord.ButtonStyle.primary, row=0)
    async def buy_v1(self, btn, interaction):
        await self._pay_info(interaction, "v1")

    @discord.ui.button(label="💳 V2 ($5)", style=discord.ButtonStyle.primary, row=0)
    async def buy_v2(self, btn, interaction):
        await self._pay_info(interaction, "v2")

    @discord.ui.button(label="💳 V3 ($7)", style=discord.ButtonStyle.primary, row=0)
    async def buy_v3(self, btn, interaction):
        await self._pay_info(interaction, "v3")

    @discord.ui.button(label="💳 Lifetime ($30)", style=discord.ButtonStyle.danger, row=1)
    async def buy_lt(self, btn, interaction):
        await self._pay_info(interaction, "lifetime")

    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, btn, interaction):
        await interaction.response.edit_message(embed=_get_dashboard_embed(self.discord_id), view=MainPanelView(self.discord_id))

    async def _pay_info(self, interaction, plan):
        pd = storage.PLANS[plan]
        embed = discord.Embed(title=f"💳 Purchase {pd['name']}", color=discord.Color.gold())
        embed.add_field(name="Price", value=f"**${pd['price']}**", inline=True)
        embed.add_field(name="Accounts", value=str(pd["accounts"]), inline=True)
        embed.description = "Contact an admin to purchase. They will provide a license key.\n\nThen use **/redeem** or the Redeem Key button to activate."
        embed.set_footer(text="Admin will generate a key via /genkey")
        await interaction.response.edit_message(embed=embed, view=self)


# ─── REDEEM KEY MODAL ────────────────────────────

class RedeemKeyModal(discord.ui.Modal):
    def __init__(self, discord_id):
        super().__init__(title="Redeem License Key")
        self.discord_id = discord_id
        self.add_item(discord.ui.InputText(label="License Key", placeholder="HUNTER-XXXX-XXXX-XXXX", required=True, min_length=10, max_length=50))

    async def callback(self, interaction):
        key = self.children[0].value.strip()
        result = storage.redeem_key(key, self.discord_id)
        if not result:
            embed = discord.Embed(title="❌ Invalid or Already Used Key", color=discord.Color.red())
            await interaction.response.edit_message(embed=embed)
            return

        plan = result["plan"]
        sid = str(uuid.uuid4())
        exp = "2099-12-31T23:59:59" if plan == "lifetime" else (datetime.utcnow() + timedelta(days=30)).isoformat()
        storage.add_subscription({
            "id": sid, "discord_id": self.discord_id, "plan": plan,
            "amount": 0, "status": "confirmed",
            "created_at": datetime.utcnow().isoformat(), "expires_at": exp
        })

        embed = discord.Embed(
            title=f"✅ {storage.get_plan_name(plan)} Activated!",
            description=f"Max {storage.get_plan_max_accounts(plan)} accounts. Full access granted!",
            color=discord.Color.green()
        )
        full_embed = _get_dashboard_embed(self.discord_id)
        await interaction.response.edit_message(embed=full_embed, view=MainPanelView(self.discord_id))
