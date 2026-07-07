import threading
import time
from datetime import datetime, timezone
import storage
import discord_api
from crypto_utils import decrypt_token

# Global flag for the worker
_worker_running = False
_worker_thread = None

# Paused campaigns set
_paused_campaigns = set()

def _campaign_worker():
    """Single background worker that processes all running campaigns."""
    global _worker_running
    print("[WORKER] Campaign worker started")
    
    while _worker_running:
        try:
            # Get all campaigns that should be running
            all_campaigns = storage.get_campaigns()
            running_campaigns = [c for c in all_campaigns if c.get("status") == "running" and c["id"] not in _paused_campaigns]
            
            for campaign in running_campaigns:
                cid = campaign["id"]
                short_id = cid[:8]
                
                # Get current progress
                channels = campaign.get("channels", [])
                messages = campaign.get("messages", [])
                delay = max(campaign.get("delay", 1), 1)
                
                # Figure out where we left off
                sent = campaign.get("messages_sent", 0)
                total_expected = len(channels) * len(messages)
                
                if sent >= total_expected:
                    # Already completed
                    storage.update_campaign(cid, {
                        "status": "completed",
                        "completed_at": datetime.now(timezone.utc).isoformat()
                    })
                    print(f"[WORKER] {short_id} completed")
                    continue
                
                # Calculate which message we're on
                msg_index = sent // len(channels) if channels else 0
                ch_index = sent % len(channels) if channels else 0
                
                if msg_index >= len(messages):
                    storage.update_campaign(cid, {
                        "status": "completed",
                        "completed_at": datetime.now(timezone.utc).isoformat()
                    })
                    print(f"[WORKER] {short_id} completed (all done)")
                    continue
                
                # Get account and token
                account = storage.get_account_by_id(campaign.get("account_id", ""))
                if not account:
                    storage.update_campaign(cid, {"status": "failed", "error": "Account not found"})
                    print(f"[WORKER] {short_id} failed: account not found")
                    continue
                
                try:
                    token = decrypt_token(account["encrypted_token"])
                except Exception as e:
                    storage.update_campaign(cid, {"status": "failed", "error": f"Token error: {e}"})
                    print(f"[WORKER] {short_id} failed: token error")
                    continue
                
                # Send the current message to the current channel
                msg_obj = messages[msg_index]
                content = msg_obj.get("content", "")
                image_url = msg_obj.get("image_url", None)
                ch_id = channels[ch_index]
                
                print(f"[WORKER] {short_id} sending msg {msg_index+1}/{len(messages)} to channel {ch_id}")
                
                result = discord_api.send_message(token, ch_id, content, image_url)
                
                if result.get("status") == 200:
                    storage.update_campaign(cid, {"messages_sent": sent + 1})
                    print(f"[WORKER] {short_id} sent OK")
                else:
                    # Count as failed but continue
                    failed = campaign.get("messages_failed", 0)
                    storage.update_campaign(cid, {"messages_failed": failed + 1, "messages_sent": sent + 1})
                    print(f"[WORKER] {short_id} failed: {result.get('status')}")
            
        except Exception as e:
            print(f"[WORKER] Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Sleep 2 seconds between worker cycles
        time.sleep(2)
    
    print("[WORKER] Campaign worker stopped")


def start_campaign(campaign_id):
    """Mark a campaign as running. The worker will pick it up."""
    storage.update_campaign(campaign_id, {"status": "running"})
    _paused_campaigns.discard(campaign_id)
    print(f"[CAMPAIGN] {campaign_id[:8]} marked as running")
    return True


def pause_campaign(campaign_id):
    """Pause a running campaign."""
    _paused_campaigns.add(campaign_id)
    storage.update_campaign(campaign_id, {"status": "paused"})
    print(f"[CAMPAIGN] {campaign_id[:8]} paused")


def start_worker():
    """Start the background worker thread."""
    global _worker_running, _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        print("[WORKER] Worker already running")
        return
    _worker_running = True
    _worker_thread = threading.Thread(target=_campaign_worker, daemon=True)
    _worker_thread.start()
    print("[WORKER] Worker launched")


def stop_worker():
    """Stop the background worker."""
    global _worker_running
    _worker_running = False


# ─── DM Auto-Reply ────────────────────────────────

_running_responders = {}
_responder_threads = {}

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
    """Mark all running campaigns from storage as ready for the worker."""
    count = 0
    for c in storage.get_campaigns():
        if c.get("status") == "running":
            _paused_campaigns.discard(c["id"])
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