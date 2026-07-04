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

# ─── Channel Messaging ─────────────────────────────

def _run_campaign(campaign_id):
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
    delay = max(campaign.get("delay", 1), 1)

    for ch_id in channels:
        for msg in messages:
            if campaign_id in _running_campaigns and not _running_campaigns[campaign_id]:
                storage.update_campaign(campaign_id, {"status": "paused"})
                return

            result = discord_api.send_message(token, ch_id, msg)
            sent_ok = result.get("status") == 200

            stats = storage.get_campaign_by_id(campaign_id)
            if stats:
                storage.update_campaign(campaign_id, {
                    "messages_sent": stats.get("messages_sent", 0) + (1 if sent_ok else 0),
                    "messages_failed": stats.get("messages_failed", 0) + (0 if sent_ok else 1),
                })

            time.sleep(delay)

    camp = storage.get_campaign_by_id(campaign_id)
    if camp and camp.get("status") == "running":
        storage.update_campaign(campaign_id, {
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat()
        })

    _running_campaigns.pop(campaign_id, None)
    _campaign_threads.pop(campaign_id, None)


def start_campaign(campaign_id):
    if campaign_id in _campaign_threads and _campaign_threads[campaign_id].is_alive():
        return False
    _running_campaigns[campaign_id] = True
    t = threading.Thread(target=_run_campaign, args=(campaign_id,), daemon=True)
    _campaign_threads[campaign_id] = t
    t.start()
    return True


def pause_campaign(campaign_id):
    _running_campaigns[campaign_id] = False
    storage.update_campaign(campaign_id, {"status": "paused"})


# ─── DM Auto-Reply ────────────────────────────────

def _run_dm_responder(discord_id):
    """Poll for new DMs and auto-reply."""
    while True:
        if discord_id not in _running_responders or not _running_responders[discord_id]:
            return

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

            try:
                token = decrypt_token(account["encrypted_token"])
            except:
                continue

            try:
                dms = discord_api.get_dms(token)
                for dm in dms:
                    msgs = discord_api.get_channel_messages(token, dm["id"], limit=1)
                    if msgs:
                        last_msg = msgs[0]
                        author_id = str(last_msg["author"].get("id", "")) if isinstance(last_msg["author"], dict) else str(last_msg.get("author", {}))
                        
                        # Make sure it's not our own account
                        if str(last_msg.get("author", {}).get("id", "")) != str(account.get("discord_user_id", "")):
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
