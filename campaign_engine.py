import threading
import time
from datetime import datetime, timezone
import storage
import discord_api
from crypto_utils import decrypt_token

_running_campaigns = set()
_running_responders = {}
_campaign_threads = {}
_responder_threads = {}

# ─── Channel Messaging ─────────────────────────────

def _run_campaign(campaign_id):
    """Background thread that runs a channel messaging campaign."""
    short_id = campaign_id[:8]
    print(f"[CAMPAIGN] Thread started for {short_id}")
    
    try:
        campaign = storage.get_campaign_by_id(campaign_id)
        if not campaign:
            print(f"[CAMPAIGN] Campaign {short_id} not found")
            return

        storage.update_campaign(campaign_id, {"status": "running"})
        print(f"[CAMPAIGN] {short_id} marked as running")
        print(f"[CAMPAIGN] Name: {campaign.get('name')}")
        print(f"[CAMPAIGN] Channels: {campaign['channels']}")
        print(f"[CAMPAIGN] Messages count: {len(campaign['messages'])}")

        account = storage.get_account_by_id(campaign["account_id"])
        if not account:
            print(f"[CAMPAIGN] Account not found")
            storage.update_campaign(campaign_id, {"status": "failed", "error": "Account not found"})
            return

        token = decrypt_token(account["encrypted_token"])
        print(f"[CAMPAIGN] Got token for {account.get('username')}")

        channels = campaign["channels"]
        messages = campaign["messages"]
        delay = max(campaign.get("delay", 1), 1)
        
        print(f"[CAMPAIGN] Delay: {delay}s")

        for msg_idx, msg_obj in enumerate(messages):
            if campaign_id not in _running_campaigns:
                print(f"[CAMPAIGN] {short_id} was stopped/paused")
                storage.update_campaign(campaign_id, {"status": "paused"})
                return

            content = msg_obj.get("content", "")
            image_url = msg_obj.get("image_url", None)
            
            preview = content[:40] + "..." if len(content) > 40 else content
            print(f"[CAMPAIGN] Message {msg_idx + 1}/{len(messages)}: '{preview}'")
            
            for ch_idx, ch_id in enumerate(channels):
                if campaign_id not in _running_campaigns:
                    storage.update_campaign(campaign_id, {"status": "paused"})
                    return
                
                print(f"[CAMPAIGN] Sending to channel {ch_id} ({ch_idx + 1}/{len(channels)})")
                
                result = discord_api.send_message(token, ch_id, content, image_url)
                
                current = storage.get_campaign_by_id(campaign_id)
                if current:
                    if result.get("status") == 200:
                        storage.update_campaign(campaign_id, {
                            "messages_sent": current.get("messages_sent", 0) + 1
                        })
                        print(f"[CAMPAIGN] Sent OK to {ch_id}")
                    else:
                        storage.update_campaign(campaign_id, {
                            "messages_failed": current.get("messages_failed", 0) + 1
                        })
                        print(f"[CAMPAIGN] Failed to {ch_id}: status {result.get('status')}")

            if msg_idx < len(messages) - 1:
                print(f"[CAMPAIGN] Waiting {delay}s...")
                time.sleep(delay)

        print(f"[CAMPAIGN] {short_id} completed all messages")
        storage.update_campaign(campaign_id, {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        print(f"[CAMPAIGN] {short_id} ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        storage.update_campaign(campaign_id, {"status": "failed", "error": str(e)})
    
    finally:
        _running_campaigns.discard(campaign_id)
        _campaign_threads.pop(campaign_id, None)
        print(f"[CAMPAIGN] {short_id} thread cleanup done")


def start_campaign(campaign_id):
    """Start a campaign in a background thread."""
    if campaign_id in _campaign_threads:
        old_thread = _campaign_threads[campaign_id]
        if old_thread.is_alive():
            print(f"[CAMPAIGN] {campaign_id[:8]} already has a live thread")
            return False
        _campaign_threads.pop(campaign_id, None)
    
    _running_campaigns.add(campaign_id)
    
    t = threading.Thread(target=_run_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = t
    t.start()
    
    print(f"[CAMPAIGN] {campaign_id[:8]} thread launched (alive: {t.is_alive()})")
    return True


def pause_campaign(campaign_id):
    """Pause a running campaign."""
    _running_campaigns.discard(campaign_id)
    storage.update_campaign(campaign_id, {"status": "paused"})
    print(f"[CAMPAIGN] {campaign_id[:8]} paused")


# ─── DM Auto-Reply ────────────────────────────────

def _run_dm_responder(discord_id):
    """Poll for new DMs and auto-reply."""
    while discord_id in _running_responders and _running_responders[discord_id]:
        try:
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
                account = next((a for a in accounts if a["id"] == camp["account_id"]), None)
                if not account:
                    continue

                token = decrypt_token(account["encrypted_token"])
                dms = discord_api.get_dms(token)
                
                for dm in dms:
                    msgs = discord_api.get_channel_messages(token, dm["id"], limit=1)
                    if msgs:
                        last_msg = msgs[0]
                        author = last_msg.get("author", {})
                        author_id = str(author.get("id", "")) if isinstance(author, dict) else str(author)
                        
                        if author_id != str(account.get("discord_user_id", "")):
                            last_replied = camp.get("last_replied_id", "")
                            if last_msg["id"] != last_replied:
                                keywords = camp.get("keywords", [])
                                content = last_msg.get("content", "")
                                if not keywords or any(kw.lower() in content.lower() for kw in keywords):
                                    for reply in camp.get("messages", []):
                                        discord_api.send_message(token, dm["id"], reply)
                                    storage.update_campaign(camp["id"], {
                                        "last_replied_id": last_msg["id"],
                                        "replied_count": camp.get("replied_count", 0) + 1
                                    })
        except:
            pass
        
        time.sleep(5)


def start_dm_responder(discord_id):
    if discord_id in _responder_threads and _responder_threads[discord_id].is_alive():
        return False
    _running_responders[discord_id] = True
    t = threading.Thread(target=_run_dm_responder, args=(discord_id,), daemon=True)
    _responder_threads[discord_id] = t
    t.start()
    return True


def stop_dm_responder(discord_id):
    _running_responders[discord_id] = False
    _responder_threads.pop(discord_id, None)


def restart_all_campaigns():
    count = 0
    for c in storage.get_campaigns():
        if c.get("status") == "running":
            start_campaign(c["id"])
            count += 1
    return count


def restart_all_responders():
    count = 0
    seen = set()
    for c in storage.get_campaigns():
        if c["type"] == "dm_auto_reply" and c.get("status") == "running" and c["discord_id"] not in seen:
            start_dm_responder(c["discord_id"])
            seen.add(c["discord_id"])
            count += 1
    return count