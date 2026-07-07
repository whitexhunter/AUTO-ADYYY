import os
import asyncio
import discord
import uuid
import zipfile
import shutil
import io
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

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


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    load_admin_ids()
    check_expired()
    
    # Start the campaign worker
    campaign_engine.start_worker()
    
    camp = campaign_engine.restart_all_campaigns()
    resp = campaign_engine.restart_all_responders()
    print(f"▶️ Restarted {camp} campaigns, {resp} DM responders")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="HUNTER | /panel"
    ))


@bot.slash_command(name="panel", description="Opens the user panel")
async def panel(ctx: discord.ApplicationContext):
    did = str(ctx.author.id)
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
    sid = str(uuid.uuid4())
    exp = "2099-12-31T23:59:59" if plan == "lifetime" else (
        datetime.utcnow() + timedelta(days=30)
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
    messages: discord.Option(str, "Messages separated by ||"),
    channels: discord.Option(str, "Channel IDs comma-sep", required=False, default=""),
    delay: discord.Option(int, "Delay seconds", required=False, default=1),
    image_urls: discord.Option(str, "Image URLs separated by || (optional)", required=False, default="")
):
    did = str(ctx.author.id)

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

    # Parse messages separated by ||
    raw_msg_list = [m.strip() for m in messages.split("||") if m.strip()]
    
    # Parse image URLs separated by ||
    img_list = []
    if image_urls:
        img_list = [img.strip() for img in image_urls.split("||") if img.strip()]
    
    # Build message objects
    parsed_messages = []
    for i, msg_content in enumerate(raw_msg_list):
        msg_obj = {"content": msg_content}
        if i < len(img_list) and img_list[i]:
            msg_obj["image_url"] = img_list[i]
        parsed_messages.append(msg_obj)
    
    cid = str(uuid.uuid4())

    if ctype == "channel":
        ch_list = [c.strip() for c in channels.split(",") if c.strip()]
        if not ch_list:
            await ctx.respond("❌ Need at least 1 channel.", ephemeral=True)
            return
        
        # Check image plan gating
        plan = storage.get_user_effective_plan(did)
        features = storage.get_plan_features(plan)
        has_images = any(m.get("image_url") for m in parsed_messages)
        if has_images and "image_attachments" not in features:
            await ctx.respond(
                f"❌ Image attachments require V2+ plan. Your plan: {storage.get_plan_name(plan)}",
                ephemeral=True
            )
            return
        
        storage.add_campaign({
            "id": cid, "discord_id": did, "account_id": target["id"],
            "name": name, "type": "channel", "channels": ch_list,
            "messages": parsed_messages, "delay": max(delay, 1),
            "status": "idle", "messages_sent": 0, "messages_failed": 0,
            "created_at": datetime.utcnow().isoformat()
        })
        campaign_engine.start_campaign(cid)
        
        embed = discord.Embed(title=f"✅ {name} Running!", color=discord.Color.green())
        embed.add_field(name="Channels", value=str(len(ch_list)), inline=True)
        embed.add_field(name="Messages", value=str(len(parsed_messages)), inline=True)
        await ctx.respond(embed=embed)
    else:
        plan = storage.get_user_effective_plan(did)
        if "dm_auto_reply" not in storage.get_plan_features(plan):
            await ctx.respond("❌ DM Auto-Reply requires V3+.", ephemeral=True)
            return
        
        # DM replies don't have image support currently
        flat_msgs = [m["content"] for m in parsed_messages]
        
        storage.add_campaign({
            "id": cid, "discord_id": did, "account_id": target["id"],
            "name": name, "type": "dm_auto_reply",
            "messages": flat_msgs, "keywords": [],
            "status": "running", "replied_count": 0, "last_replied_id": "",
            "created_at": datetime.utcnow().isoformat()
        })
        campaign_engine.start_dm_responder(did)
        
        embed = discord.Embed(title=f"✅ {name} Active!", color=discord.Color.green())
        embed.add_field(name="Replies", value=str(len(flat_msgs)), inline=True)
        await ctx.respond(embed=embed)


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
    load_admin_ids()
    if not is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Unauthorized.", ephemeral=True)
        return
    
    user = storage.get_user(user_id)
    if not user:
        await ctx.respond(f"❌ User `{user_id}` not found in database.", ephemeral=True)
        return
    
    sid = str(uuid.uuid4())
    
    if plan == "lifetime" or days == 0:
        expires_at = "2099-12-31T23:59:59"
        days_str = "Lifetime"
    else:
        expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
        days_str = f"{days} days"
    
    storage.add_subscription({
        "id": sid, "discord_id": user_id, "plan": plan,
        "amount": 0, "status": "confirmed",
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at
    })
    
    # Expire old subs for this user so new one takes priority
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

@bot.slash_command(name="backup", description="Create a backup of all data (admin-only)")
async def backup(ctx: discord.ApplicationContext):
    """Create a timestamped backup ZIP of all data files."""
    load_admin_ids()
    if not is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Unauthorized.", ephemeral=True)
        return
    
    import shutil
    import io
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"hunter_backup_{timestamp}.zip"
    
    # Create ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in storage.FILES:
            filepath = os.path.join(storage.DATA_DIR, fname)
            if os.path.exists(filepath):
                zf.write(filepath, fname)
        
        # Also include fernet key
        fernet_path = "data/fernet.key"
        if os.path.exists(fernet_path):
            zf.write(fernet_path, "fernet.key")
        
        # Add a manifest with timestamp info
        manifest = f"Backup created at: {datetime.now(timezone.utc).isoformat()}\n"
        manifest += f"Total files: {len(storage.FILES) + 1}\n"
        for fname in storage.FILES:
            filepath = os.path.join(storage.DATA_DIR, fname)
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                manifest += f"  {fname}: {size} bytes\n"
        zf.writestr("manifest.txt", manifest)
    
    zip_buffer.seek(0)
    
    await ctx.respond(
        embed=discord.Embed(
            title="✅ Backup Created",
            description=f"`{backup_filename}` ({zip_buffer.getbuffer().nbytes / 1024:.1f} KB)",
            color=discord.Color.green()
        ),
        file=discord.File(zip_buffer, backup_filename),
        ephemeral=True
    )


@bot.slash_command(name="restore", description="Restore data from a backup file (admin-only)")
async def restore(
    ctx: discord.ApplicationContext,
    backup_file: discord.Option(discord.Attachment, "Upload the backup ZIP file")
):
    """Restore all data from a previously created backup ZIP."""
    load_admin_ids()
    if not is_admin(str(ctx.author.id)):
        await ctx.respond("❌ Unauthorized.", ephemeral=True)
        return
    
    if not backup_file.filename.endswith(".zip"):
        await ctx.respond("❌ Please upload a .zip backup file.", ephemeral=True)
        return
    
    await ctx.defer(ephemeral=True)
    
    try:
        # Download the file
        zip_bytes = await backup_file.read()
        
        import zipfile
        import io
        
        # Create a backup of current state first (safety)
        safety_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safety_backup = f"data/pre_restore_{safety_timestamp}"
        os.makedirs(safety_backup, exist_ok=True)
        for fname in storage.FILES:
            src = os.path.join(storage.DATA_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(safety_backup, fname))
        fernet_src = "data/fernet.key"
        if os.path.exists(fernet_src):
            shutil.copy2(fernet_src, os.path.join(safety_backup, "fernet.key"))
        
        # Now restore from uploaded ZIP
        restored_files = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            for fname in zf.namelist():
                if fname in storage.FILES or fname == "fernet.key":
                    # Extract to data directory
                    zf.extract(fname, storage.DATA_DIR)
                    restored_files.append(fname)
        
        # Reload data by reinitializing storage
        storage._init()
        
        embed = discord.Embed(
            title="✅ Data Restored Successfully!",
            color=discord.Color.green()
        )
        embed.add_field(name="Restored Files", value="\n".join(restored_files) or "None", inline=False)
        embed.add_field(name="Safety Backup", value=f"Saved to `{safety_backup}/`", inline=False)
        
        await ctx.respond(embed=embed, ephemeral=True)
        
    except Exception as e:
        await ctx.respond(
            embed=discord.Embed(
                title="❌ Restore Failed",
                description=f"Error: {str(e)}",
                color=discord.Color.red()
            ),
            ephemeral=True
        )

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
