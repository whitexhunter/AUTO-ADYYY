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
from views import MainPanelView, _get_dashboard_embed
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
                    storage.upsert_user(u["discord_id"], {"trial_active": False})
            except:
                pass


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    load_admin_ids()
    check_expired()
    camp = campaign_engine.restart_all_campaigns()
    resp = campaign_engine.restart_all_responders()
    print(f"▶️ Restarted {camp} campaigns, {resp} DM responders")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="HUNTER | /panel"))


@bot.slash_command(name="panel", description="Opens the main user panel")
async def panel(ctx: discord.ApplicationContext):
    did = str(ctx.author.id)
    if not storage.get_user(did):
        storage.upsert_user(did, {"discord_id": did, "trial_used": False, "trial_active": False, "created_at": datetime.utcnow().isoformat()})
    await ctx.respond(embed=_get_dashboard_embed(did), view=MainPanelView(did))


@bot.slash_command(name="admin", description="Opens the admin panel (admin-only)")
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
        await ctx.respond(embed=discord.Embed(title="❌ Invalid or Used Key", color=discord.Color.red()))
        return
    plan = result["plan"]
    sid = str(__import__("uuid").uuid4())
    exp = "2099-12-31T23:59:59" if plan == "lifetime" else (datetime.utcnow() + __import__("datetime").timedelta(days=30)).isoformat()
    storage.add_subscription({"id": sid, "discord_id": did, "plan": plan, "amount": 0, "status": "confirmed", "created_at": datetime.utcnow().isoformat(), "expires_at": exp})
    await ctx.respond(embed=discord.Embed(title=f"✅ {storage.get_plan_name(plan)} Activated!", description=f"Max {storage.get_plan_max_accounts(plan)} accounts", color=discord.Color.green()))


@bot.slash_command(name="campaign_create", description="Creates a new campaign")
async def campaign_create(
    ctx: discord.ApplicationContext,
    name: discord.Option(str, "Campaign name"),
    ctype: discord.Option(str, "Type", choices=["channel", "dm_auto_reply"]),
    account_id: discord.Option(str, "Account ID prefix (from /panel)"),
    messages: discord.Option(str, "Messages separated by |"),
    channels: discord.Option(str, "Channel IDs comma-separated (for channel type)", required=False, default=""),
    delay: discord.Option(int, "Delay seconds (default 1)", required=False, default=1)
):
    did = str(ctx.author.id)
    target = None
    for a in storage.get_user_accounts(did):
        if a["id"].startswith(account_id):
            target = a
            break
    if not target:
        await ctx.respond(f"❌ Account `{account_id}` not found.", ephemeral=True)
        return

    msg_list = [m.strip() for m in messages.split("|") if m.strip()]
    cid = str(__import__("uuid").uuid4())

    if ctype == "channel":
        ch_list = [c.strip() for c in channels.split(",") if c.strip()]
        if not ch_list:
            await ctx.respond("❌ Need at least 1 channel.", ephemeral=True)
            return
        storage.add_campaign({"id": cid, "discord_id": did, "account_id": target["id"], "name": name, "type": "channel", "channels": ch_list, "messages": msg_list, "delay": max(delay, 1), "status": "idle", "messages_sent": 0, "messages_failed": 0, "created_at": datetime.utcnow().isoformat()})
        campaign_engine.start_campaign(cid)
        await ctx.respond(embed=discord.Embed(title=f"✅ {name} Running!", color=discord.Color.green()).add_field(name="Channels", value=str(len(ch_list))).add_field(name="Messages", value=str(len(msg_list))))
    else:
        plan = storage.get_user_effective_plan(did)
        if "dm_auto_reply" not in storage.get_plan_features(plan):
            await ctx.respond("❌ DM Auto-Reply requires V3+.", ephemeral=True)
            return
        storage.add_campaign({"id": cid, "discord_id": did, "account_id": target["id"], "name": name, "type": "dm_auto_reply", "messages": msg_list, "keywords": [], "status": "running", "replied_count": 0, "last_replied_id": "", "created_at": datetime.utcnow().isoformat()})
        campaign_engine.start_dm_responder(did)
        await ctx.respond(embed=discord.Embed(title=f"✅ {name} Active!", color=discord.Color.green()).add_field(name="Replies", value=str(len(msg_list))))


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
        storage.add_key({"key": k, "plan": plan, "created_at": datetime.utcnow().isoformat(), "created_by": str(ctx.author.id), "redeemed_by": None, "redeemed_at": None})
        keys.append(k)

    embed = discord.Embed(title=f"✅ {count} {plan.upper()} Keys", color=discord.Color.green())
    embed.add_field(name="Keys", value=f"```{chr(10).join(keys[:10])}{chr(10)+'...'+str(count-10)+' more' if count>10 else ''}```", inline=False)
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
