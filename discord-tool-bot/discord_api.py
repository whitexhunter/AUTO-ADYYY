import requests
import json

API_BASE = "https://discord.com/api/v10"

def _headers(token: str) -> dict:
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (HUNTER, 1.0.0)"
    }

def validate_token(token: str) -> dict | None:
    """Validate a Discord user token and return user info."""
    try:
        r = requests.get(f"{API_BASE}/users/@me", headers=_headers(token), timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None

def send_message(token: str, channel_id: str, content: str) -> dict:
    """Send a message to a channel using a user token."""
    data = {"content": content}
    r = requests.post(
        f"{API_BASE}/channels/{channel_id}/messages",
        headers=_headers(token),
        json=data,
        timeout=10
    )
    return {"status": r.status_code, "data": r.json() if r.text else {}}

def get_channel_messages(token: str, channel_id: str, limit: int = 5) -> list:
    """Fetch recent messages from a channel."""
    r = requests.get(
        f"{API_BASE}/channels/{channel_id}/messages?limit={limit}",
        headers=_headers(token),
        timeout=10
    )
    if r.status_code == 200:
        return r.json()
    return []

def get_dms(token: str) -> list:
    """Get DM channels for a user token."""
    r = requests.get(
        f"{API_BASE}/users/@me/channels",
        headers=_headers(token),
        timeout=10
    )
    if r.status_code == 200:
        return r.json()
    return []

def get_user_info_from_token(token: str) -> dict:
    """Get user info using the raw token."""
    r = requests.get(f"{API_BASE}/users/@me", headers=_headers(token), timeout=10)
    if r.status_code == 200:
        return r.json()
    return {}
