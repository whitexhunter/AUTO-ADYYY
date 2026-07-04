import requests

API_BASE = "https://discord.com/api/v10"

def _headers(token):
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (HUNTER, 1.0.0)"
    }

def validate_token(token):
    """Validate a Discord user token, return user info or None."""
    try:
        r = requests.get(f"{API_BASE}/users/@me", headers=_headers(token), timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None

def send_message(token, channel_id, content, image_url=None):
    """Send a message to a channel using a user token.
    
    If image_url is provided, sends the message with an embedded image.
    """
    try:
        data = {"content": content}
        
        if image_url:
            # Send as embed with image
            data["embeds"] = [{"image": {"url": image_url}}]
        
        r = requests.post(
            f"{API_BASE}/channels/{channel_id}/messages",
            headers=_headers(token),
            json=data,
            timeout=10
        )
        return {"status": r.status_code, "data": r.json() if r.text else {}}
    except Exception as e:
        return {"status": 0, "error": str(e)}

def get_channel_messages(token, channel_id, limit=5):
    """Fetch recent messages from a channel."""
    try:
        r = requests.get(
            f"{API_BASE}/channels/{channel_id}/messages?limit={limit}",
            headers=_headers(token),
            timeout=10
        )
        return r.json() if r.status_code == 200 else []
    except:
        return []

def get_dms(token):
    """Get DM channels for a user token."""
    try:
        r = requests.get(f"{API_BASE}/users/@me/channels", headers=_headers(token), timeout=10)
        return r.json() if r.status_code == 200 else []
    except:
        return []
