import os
import asyncio
import discord
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    print("❌ DISCORD_BOT_TOKEN not set in .env")
    exit(1)

import storage
import campaign_engine
from views import get_panel_view, _get_dashboard_embed
from admin_views import AdminPanelView, _overview_embed, load_admin_ids, is_admin

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)


def check_expired():
    """Downgrade expired subs and trials."""
    for sub in storage.get_subscriptions():
        if sub["status"] == "confirmed" and sub["plan"] != "lifetime":
            try:
                if datetime.fromisoformat(sub["expires_at"]) < datetime.utcnow():
                    storage.update_subscription(sub["id"], {"status": "expired"})
            except:
                pass
    for u in storage.get_all_users():
        if u.get("trial_active"):
            try:
                if datetime.fromisoformat(u["trial_expires_at"]) < datetime.utcnow():
                    storage.upsert_user(u["discord_id"], {"trial_active": False, "trial_used": True})
            except:
                pass


def user_has_active_access(discord_id):
    """Check if user can actually use the service."""
    # Check trial
    user = storage.get_user(discord_id)
    if user and user.get("trial_active"):
        try:
            if datetime.fromisoformat(user["trial_expires_at"]) > datetime.utcnow():
                return True
        except:
            pass
    
    # Check active subscriptions
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


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    load_admin_ids()
    check_expired()
    camp = campaign_engine.restart_all_campaigns()
    resp = campaign_engine.restart_all_responders()
    print(f"▶️ Restarted {camp} campaigns, {resp} DM responders")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="HUNTER | /panel"
    ))


@bot.slash_command(name="panel", description="Opens the user panel")
async def panel(ctx: discord.ApplicationContext):
    did = str(ctx.author.id)
    
    # Ensure user exists in DB
    if not storage.get_user(did):
        storage.upsert_user(did, {
            "discord_id": did,
            "trial_used": False,
            "trial_active": False,
            "created_at": datetime.utcnow().isoformat()
        })
    
    has_access = user_has_active_access(did)
    embed, view = get_panel_view(did, has_access)
    await ctx.respond(embed=embed, view=view)


@bot.slash_command(name="admin", description="Admin panel (admin-only)")
async def admin(ctx: discord.ApplicationContext):
    load_admin_ids()
    if not is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Unauthorized.", ephemeral=True)
        return
    await ctx.respond(embed=_overview_embed(), view=AdminPanelView(str(ctx.author.id)))


@bot.slash_command(name="redeem", description="Redeems a license key")
async def redeem(ctx: discord.ApplicationContext, key: discord.Option(str, "License key")):
    did = str(ctx.author.id)
    result = storage.redeem_key(key, did)
    if not result:
        await ctx.respond(embed=discord.Embed(
            title="❌ Invalid or Already Used Key",
            color=discord.Color.red()
        ))
        return
    
    plan = result["plan"]
    sid = str(__import__("uuid").uuid4())
    exp = "2099-12-31T23:59:59" if plan == "lifetime" else (
        datetime.utcnow() + __import__("datetime").timedelta(days=30)
    ).isoformat()
    
    storage.add_subscription({
        "id": sid, "discord_id": did, "plan": plan,
        "amount": 0, "status": "confirmed",
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": exp
    })
    
    await ctx.respond(embed=discord.Embed(
        title=f"✅ {storage.get_plan_name(plan)} Activated!",
        description=f"Max {storage.get_plan_max_accounts(plan)} accounts. Use /panel to start.",
        color=discord.Color.green()
    ))


@bot.slash_command(name="campaign_create", description="Creates a new campaign")
async def campaign_create(
    ctx: discord.ApplicationContext,
    name: discord.Option(str, "Campaign name"),
    ctype: discord.Option(str, "Type", choices=["channel", "dm_auto_reply"]),
    account_id: discord.Option(str, "Account ID prefix"),
    messages: discord.Option(str, "Messages separated by |"),
    channels: discord.Option(str, "Channel IDs comma-sep", required=False, default=""),
    delay: discord.Option(int, "Delay seconds", required=False, default=1)
):
    did = str(ctx.author.id)
    
    # GATE: check access
    if not user_has_active_access(did):
        await ctx.respond(
            embed=discord.Embed(
                title="❌ No Active Subscription",
                description="You need an active license key or trial to create campaigns. Use `/panel` to redeem a key or start a free trial.",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
    
    target = None
    for a in storage.get_user_accounts(did):
        if a["id"].startswith(account_id):
            target = a
            break
    if not target:
        await ctx.respond(f"❌ Account `{account_id}` not found.", ephemeral=True)
        return

    raw_message_list = [m.strip() for m in messages.split("||") if m.strip()]
    msg_list = [{"content": m} for m in raw_message_list]
    cid = str(__import__("uuid").uuid4())

    if ctype == "channel":
    ch_list = [c.strip() for c in channels.split(",") if c.strip()]
    if not ch_list:
        await ctx.respond("❌ Need at least 1 channel.", ephemeral=True)
        return
    
    # Parse messages (separated by ||)
    raw_msgs = [m.strip() for m in messages.split("||") if m.strip()]
    parsed_messages = [{"content": m} for m in raw_msgs]
    
    storage.add_campaign({
        "id": cid, "discord_id": did, "account_id": target["id"],
        "name": name, "type": "channel", "channels": ch_list,
        "messages": parsed_messages, "delay": max(delay, 1),
        "status": "idle", "messages_sent": 0, "messages_failed": 0,
        "created_at": datetime.utcnow().isoformat()
    })
    campaign_engine.start_campaign(cid)
    await ctx.respond(embed=discord.Embed(
        title=f"✅ {name} Running!", color=discord.Color.green()
    ).add_field(name="Channels", value=str(len(ch_list)))
     .add_field(name="Messages", value=str(len(parsed_messages))))


@bot.slash_command(name="genkey", description="Generate license keys (admin-only)")
async def genkey(
    ctx: discord.ApplicationContext,
    plan: discord.Option(str, "Plan", choices=["v1", "v2", "v3", "lifetime"]),
    count: discord.Option(int, "Count (1-50)", min_value=1, max_value=50)
):
    load_admin_ids()
    if not is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Unauthorized.", ephemeral=True)
        return

    import string, secrets
    keys = []
    for _ in range(count):
        k = f"HUNTER-{''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(4))}-{''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(4))}-{''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(4))}"
        storage.add_key({
            "key": k, "plan": plan,
            "created_at": datetime.utcnow().isoformat(),
            "created_by": str(ctx.author.id),
            "redeemed_by": None, "redeemed_at": None
        })
        keys.append(k)

    embed = discord.Embed(title=f"✅ {count} {plan.upper()} Keys", color=discord.Color.green())
    key_list = "\n".join(keys[:10])
    if count > 10:
        key_list += f"\n... and {count - 10} more"
    embed.add_field(name="Keys", value=f"```{key_list}```", inline=False)
    await ctx.respond(embed=embed)


@bot.slash_command(name="extend", description="Extend a user's subscription (admin-only)")
async def extend(
    ctx: discord.ApplicationContext,
    user_id: discord.Option(str, "Discord user ID to extend"),
    plan: discord.Option(str, "New plan", choices=["v1", "v2", "v3", "lifetime"]),
    days: discord.Option(int, "Days to add (0 for lifetime)", min_value=0, max_value=365)
):
    """Admin command to extend or change a user's subscription."""
    load_admin_ids()
    if not is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Unauthorized.", ephemeral=True)
        return
    
    # Check if user exists
    user = storage.get_user(user_id)
    if not user:
        await ctx.respond(f"❌ User `{user_id}` not found in database.", ephemeral=True)
        return
    
    sid = str(__import__("uuid").uuid4())
    
    if plan == "lifetime" or days == 0:
        expires_at = "2099-12-31T23:59:59"
        days_str = "Lifetime"
    else:
        expires_at = (datetime.utcnow() + __import__("datetime").timedelta(days=days)).isoformat()
        days_str = f"{days} days"
    
    storage.add_subscription({
        "id": sid, "discord_id": user_id, "plan": plan,
        "amount": 0, "status": "confirmed",
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at
    })
    
    # Also disable any expired ones for this user so the new one takes priority
    for sub in storage.get_user_subscriptions(user_id):
        if sub["status"] == "confirmed" and sub["id"] != sid:
            try:
                if datetime.fromisoformat(sub["expires_at"]) < datetime.utcnow():
                    storage.update_subscription(sub["id"], {"status": "expired"})
            except:
                pass
    
    embed = discord.Embed(
        title="✅ Subscription Extended!",
        description=f"User `{user_id}` → **{storage.get_plan_name(plan)}** ({days_str})",
        color=discord.Color.green()
    )
    await ctx.respond(embed=embed)


async def expiration_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            check_expired()
        except Exception as e:
            print(f"[EXPIRATION] {e}")
        await asyncio.sleep(300)


if __name__ == "__main__":
    bot.loop.create_task(expiration_loop())
    bot.run(TOKEN)
