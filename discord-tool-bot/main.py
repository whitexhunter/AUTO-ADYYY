import os
import discord
from dotenv import load_dotenv
from datetime import datetime

import storage
import campaign_engine
from views import MainPanelView, _get_dashboard_embed
from admin_views import AdminPanelView, _get_admin_overview_embed, _load_admin_ids

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ DISCORD_BOT_TOKEN not set in .env file!")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"🌐 Connected to {len(bot.guilds)} guilds")
    
    _load_admin_ids()
    
    # Restart running campaigns and responders
    camp_count = campaign_engine.restart_all_campaigns()
    resp_count = campaign_engine.restart_all_responders()
    print(f"▶️ Restarted {camp_count} campaigns, {resp_count} DM responders")
    
    # Check and expire subscriptions
    _check_expired_subscriptions()
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="HUNTER Panel | /panel"
    ))

def _check_expired_subscriptions():
    """Auto-downgrade expired subscriptions."""
    subs = storage.get_subscriptions()
    for sub in subs:
        if sub["status"] == "confirmed":
            try:
                expires = datetime.fromisoformat(sub["expires_at"])
                if expires < datetime.utcnow() and sub["plan"] != "lifetime":
                    storage.update_subscription(sub["id"], {"status": "expired"})
                    print(f"⏰ Expired subscription {sub['id'][:8]}... for user {sub['discord_id']}")
            except:
                pass
    
    # Check trials
    users = storage.get_all_users()
    for u in users:
        if u.get("trial_active"):
            try:
                trial_exp = datetime.fromisoformat(u["trial_expires_at"])
                if trial_exp < datetime.utcnow():
                    storage.upsert_user(u["discord_id"], {"trial_active": False})
                    print(f"⏰ Expired trial for user {u['discord_id']}")
            except:
                pass

# ─── SLASH COMMANDS ────────────────────────────────

@bot.slash_command(name="panel", description="Opens the main user panel")
async def panel(ctx: discord.ApplicationContext):
    """Open the main user panel with buttons."""
    discord_id = str(ctx.author.id)
    
    # Ensure user exists
    user = storage.get_user(discord_id)
    if not user:
        storage.upsert_user(discord_id, {
            "discord_id": discord_id,
            "plan": "free",
            "trial_used": False,
            "trial_active": False,
            "created_at": datetime.utcnow().isoformat()
        })
    
    embed = _get_dashboard_embed(discord_id)
    view = MainPanelView(discord_id)
    
    await ctx.respond(embed=embed, view=view, ephemeral=False)

@bot.slash_command(name="admin", description="Opens the admin panel (admin-only)")
async def admin(ctx: discord.ApplicationContext):
    """Open the admin panel."""
    discord_id = str(ctx.author.id)
    _load_admin_ids()
    
    from admin_views import _is_admin
    if not _is_admin(discord_id):
        await ctx.respond("❌ You are not authorized to use this command.", ephemeral=True)
        return
    
    embed = _get_admin_overview_embed()
    view = AdminPanelView(discord_id)
    
    await ctx.respond(embed=embed, view=view, ephemeral=True)

@bot.slash_command(name="redeem", description="Redeems a license key")
async def redeem(
    ctx: discord.ApplicationContext,
    key: discord.Option(str, "Your license key (e.g., HUNTER-XXXX-XXXX-XXXX)")
):
    """Redeem a license key."""
    discord_id = str(ctx.author.id)
    
    result = storage.redeem_key(key, discord_id)
    
    if not result:
        embed = discord.Embed(
            title="❌ Invalid or Already Used Key",
            description="This key doesn't exist or has already been redeemed by someone.",
            color=discord.Color.red()
        )
        await ctx.respond(embed=embed, ephemeral=True)
        return
    
    plan = result["plan"]
    plan_data = storage.PLANS.get(plan, storage.PLANS["free"])
    
    import uuid
    sub_id = str(uuid.uuid4())
    
    if plan == "lifetime":
        expires_at = "2099-12-31T23:59:59"
    else:
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()
    
    sub = {
        "id": sub_id,
        "discord_id": discord_id,
        "plan": plan,
        "amount": 0,
        "status": "confirmed",
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at
    }
    storage.add_subscription(sub)
    
    embed = discord.Embed(
        title="✅ Key Redeemed Successfully!",
        description=f"You now have the **{plan_data['name']}** plan!",
        color=discord.Color.green()
    )
    embed.add_field(name="Plan", value=plan_data["name"], inline=True)
    embed.add_field(name="Max Accounts", value=str(plan_data["accounts"]), inline=True)
    
    await ctx.respond(embed=embed, ephemeral=False)

@bot.slash_command(name="campaign_create", description="Creates a new messaging campaign")
async def campaign_create(
    ctx: discord.ApplicationContext,
    name: discord.Option(str, "Campaign name"),
    ctype: discord.Option(str, "Campaign type", choices=["channel", "dm_auto_reply"]),
    account_id: discord.Option(str, "Account ID (first 8 chars from /panel > My Accounts)"),
    channels: discord.Option(str, "Channel IDs (comma-separated, for channel type only)", required=False, default=""),
    messages: discord.Option(str, "Messages (separate with | for multiple)"),
    delay: discord.Option(int, "Delay in seconds (min 1, default 1)", required=False, default=1)
):
    """Create a new campaign directly via slash command."""
    discord_id = str(ctx.author.id)
    
    # Find the account
    accounts = storage.get_user_accounts(discord_id)
    target_account = None
    for a in accounts:
        if a["id"].startswith(account_id):
            target_account = a
            break
    
    if not target_account:
        await ctx.respond(f"❌ Account starting with `{account_id}` not found. Use `/panel` > My Accounts to see your accounts.", ephemeral=True)
        return
    
    import uuid
    campaign_id = str(uuid.uuid4())
    
    msg_list = [m.strip() for m in messages.split("|") if m.strip()]
    
    if ctype == "channel":
        ch_list = [c.strip() for c in channels.split(",") if c.strip()]
        if not ch_list:
            await ctx.respond("❌ You must provide at least 1 channel ID for channel campaigns.", ephemeral=True)
            return
        
        campaign = {
            "id": campaign_id,
            "discord_id": discord_id,
            "account_id": target_account["id"],
            "name": name,
            "type": "channel",
            "channels": ch_list,
            "messages": msg_list,
            "delay": max(delay, 1),
            "status": "idle",
            "messages_sent": 0,
            "messages_failed": 0,
            "created_at": datetime.utcnow().isoformat()
        }
        storage.add_campaign(campaign)
        campaign_engine.start_campaign(campaign_id)
        
        embed = discord.Embed(title="✅ Channel Campaign Created!", color=discord.Color.green())
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Channels", value=str(len(ch_list)), inline=True)
        embed.add_field(name="Messages", value=str(len(msg_list)), inline=True)
        
    else:  # dm_auto_reply
        plan = storage.get_user_effective_plan(discord_id)
        features = storage.get_plan_features(plan)
        if "dm_auto_reply" not in features:
            await ctx.respond("❌ DM Auto-Reply requires **V3** or **Lifetime** plan. Use `/redeem` or `/panel` > Plans & Buy.", ephemeral=True)
            return
        
        campaign = {
            "id": campaign_id,
            "discord_id": discord_id,
            "account_id": target_account["id"],
            "name": name,
            "type": "dm_auto_reply",
            "messages": msg_list,
            "keywords": [],
            "status": "running",
            "replied_count": 0,
            "last_replied_id": "",
            "created_at": datetime.utcnow().isoformat()
        }
        storage.add_campaign(campaign)
        campaign_engine.start_dm_responder(discord_id)
        
        embed = discord.Embed(title="✅ DM Auto-Reply Campaign Created!", color=discord.Color.green())
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Reply Messages", value=str(len(msg_list)), inline=True)
    
    await ctx.respond(embed=embed, ephemeral=False)

@bot.slash_command(name="genkey", description="Generates license keys (admin-only)")
async def genkey(
    ctx: discord.ApplicationContext,
    plan: discord.Option(str, "Plan type", choices=["v1", "v2", "v3", "lifetime"]),
    count: discord.Option(int, "Number of keys to generate (1-50)", min_value=1, max_value=50)
):
    """Generate license keys (admin-only)."""
    _load_admin_ids()
    from admin_views import _is_admin
    if not _is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Admin only command.", ephemeral=True)
        return
    
    import string, secrets
    keys_generated = []
    for _ in range(count):
        part1 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        part2 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        part3 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        key_str = f"HUNTER-{part1}-{part2}-{part3}"
        
        key_data = {
            "key": key_str,
            "plan": plan,
            "created_at": datetime.utcnow().isoformat(),
            "created_by": str(ctx.author.id),
            "redeemed_by": None,
            "redeemed_at": None
        }
        storage.add_key(key_data)
        keys_generated.append(key_str)
    
    key_list = "\n".join(keys_generated[:15])
    if count > 15:
        key_list += f"\n... and {count - 15} more"
    
    embed = discord.Embed(
        title=f"✅ Generated {count} Keys",
        description=f"Plan: **{plan.upper()}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Keys", value=f"```{key_list}```", inline=False)
    
    await ctx.respond(embed=embed, ephemeral=True)

# ─── EXPIRATION CHECK LOOP ─────────────────────────

import asyncio

async def expiration_check_loop():
    """Check for expired subscriptions every 5 minutes."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            _check_expired_subscriptions()
        except Exception as e:
            print(f"[EXPIRATION CHECK ERROR] {e}")
        await asyncio.sleep(300)

# ─── RUN BOT ───────────────────────────────────────

if __name__ == "__main__":
    bot.loop.create_task(expiration_check_loop())
    bot.run(BOT_TOKEN)
