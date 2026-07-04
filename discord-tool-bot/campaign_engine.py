import threading
import time
from datetime import datetime
import storage
import discord_api
from crypto_utils import decrypt_token

_running_campaigns = {}
_running_responders = {}
_campaign_threads = {}
_responder_threads = {}

def _run_campaign(campaign_id: str):
    """Background thread that runs a channel messaging campaign."""
    campaign = storage.get_campaign_by_id(campaign_id)
    if not campaign:
        return
    
    storage.update_campaign(campaign_id, {"status": "running"})
    
    account = storage.get_account_by_id(campaign["account_id"])
    if not account:
        storage.update_campaign(campaign_id, {"status": "failed", "error": "Account not found"})
        return
    
    try:
        token = decrypt_token(account["encrypted_token"])
    except:
        storage.update_campaign(campaign_id, {"status": "failed", "error": "Token decryption failed"})
        return
    
    channels = campaign["channels"]
    messages = campaign["messages"]
    delay = max(campaign.get("delay", 1), 1)  # minimum 1 second
    
    total_sent = 0
    total_failed = 0
    failed_channels = []
    
    for msg in messages:
        for ch_id in channels:
            if campaign_id in _running_campaigns and not _running_campaigns[campaign_id]:
                storage.update_campaign(campaign_id, {"status": "paused"})
                return
            
            result = discord_api.send_message(token, ch_id, msg)
            if result["status"] == 200:
                total_sent += 1
            else:
                total_failed += 1
                if result["status"] == 403:
                    failed_channels.append(ch_id)
            
            stats = storage.get_campaign_by_id(campaign_id)
            if stats:
                storage.update_campaign(campaign_id, {
                    "messages_sent": (stats.get("messages_sent", 0) + 1) if result["status"] == 200 else stats.get("messages_sent", 0),
                    "messages_failed": (stats.get("messages_failed", 0) + 1) if result["status"] != 200 else stats.get("messages_failed", 0),
                })
            
            time.sleep(delay)
    
    campaign = storage.get_campaign_by_id(campaign_id)
    if campaign and campaign.get("status") == "running":
        storage.update_campaign(campaign_id, {
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        })
    
    if campaign_id in _running_campaigns:
        del _running_campaigns[campaign_id]
    if campaign_id in _campaign_threads:
        del _campaign_threads[campaign_id]


def start_campaign(campaign_id: str):
    """Start or resume a campaign in a background thread."""
    if campaign_id in _campaign_threads and _campaign_threads[campaign_id].is_alive():
        return False
    _running_campaigns[campaign_id] = True
    t = threading.Thread(target=_run_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = t
    t.start()
    return True

def pause_campaign(campaign_id: str):
    """Pause a running campaign."""
    if campaign_id in _running_campaigns:
        _running_campaigns[campaign_id] = False
    storage.update_campaign(campaign_id, {"status": "paused"})

def stop_campaign(campaign_id: str):
    """Fully stop and delete a running campaign from engine."""
    if campaign_id in _running_campaigns:
        del _running_campaigns[campaign_id]
    if campaign_id in _campaign_threads:
        del _campaign_threads[campaign_id]

# ─── DM Auto-Reply Engine ─────────────────────────────

def _run_dm_responder(discord_id: str):
    """Poll for new DMs and auto-reply."""
    while True:
        if discord_id not in _running_responders or not _running_responders[discord_id]:
            return
        
        user = storage.get_user(discord_id)
        if not user:
            return
        
        # Find all active DM auto-reply campaigns for this user
        campaigns = storage.get_user_campaigns(discord_id)
        responder_camps = [
            c for c in campaigns
            if c["type"] == "dm_auto_reply" and c.get("status") == "running"
        ]
        
        if not responder_camps:
            time.sleep(5)
            continue
        
        accounts = storage.get_user_accounts(discord_id)
        
        for camp in responder_camps:
            account = None
            for a in accounts:
                if a["id"] == camp["account_id"]:
                    account = a
                    break
            if not account:
                continue
            
            try:
                token = decrypt_token(account["encrypted_token"])
            except:
                continue
            
            try:
                dms = discord_api.get_dms(token)
                for dm in dms:
                    # Get last message from this DM
                    msgs = discord_api.get_channel_messages(token, dm["id"], limit=1)
                    if msgs:
                        last_msg = msgs[0]
                        if last_msg["author"]["id"] != account.get("discord_user_id"):
                            # It's someone else's message, check if we already replied
                            last_replied_id = camp.get("last_replied_id", "")
                            if last_msg["id"] != last_replied_id:
                                # Check keywords if configured
                                keywords = camp.get("keywords", [])
                                if not keywords or any(kw.lower() in last_msg.get("content", "").lower() for kw in keywords):
                                    for reply_msg in camp.get("messages", []):
                                        discord_api.send_message(token, dm["id"], reply_msg)
                                    
                                    storage.update_campaign(camp["id"], {
                                        "last_replied_id": last_msg["id"],
                                        "replied_count": camp.get("replied_count", 0) + 1
                                    })
            except:
                pass
        
        time.sleep(5)


def start_dm_responder(discord_id: str):
    """Start the DM auto-reply polling for a user."""
    if discord_id in _responder_threads and _responder_threads[discord_id].is_alive():
        return False
    _running_responders[discord_id] = True
    t = threading.Thread(target=_run_dm_responder, args=(discord_id,), daemon=True)
    _responder_threads[discord_id] = t
    t.start()
    return True

def stop_dm_responder(discord_id: str):
    """Stop the DM auto-reply polling for a user."""
    _running_responders[discord_id] = False
    if discord_id in _responder_threads:
        del _responder_threads[discord_id]

def restart_all_responders():
    """Restart all DM responders on bot startup."""
    users = storage.get_all_users()
    count = 0
    for u in users:
        camps = storage.get_user_campaigns(u["discord_id"])
        has_active = any(
            c["type"] == "dm_auto_reply" and c.get("status") == "running"
            for c in camps
        )
        if has_active:
            start_dm_responder(u["discord_id"])
            count += 1
    return count

def restart_all_campaigns():
    """Restart all running campaigns on bot startup."""
    camps = storage.get_campaigns()
    count = 0
    for c in camps:
        if c.get("status") == "running":
            start_campaign(c["id"])
            count += 1
    return count
