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
    
    If image_url is provided, downloads the image and sends as attachment.
    """
    try:
        if image_url:
            # Download the image first
            img_resp = requests.get(image_url, timeout=10)
            if img_resp.status_code == 200:
                # Get filename from URL
                filename = image_url.split("/")[-1].split("?")[0]
                if not filename or "." not in filename:
                    filename = "image.png"
                
                # Determine content type
                content_type = img_resp.headers.get("content-type", "image/png")
                
                # Upload as attachment using multipart form data
                files = {
                    "file": (filename, img_resp.content, content_type)
                }
                payload = {"content": content} if content else {"content": ""}
                
                r = requests.post(
                    f"{API_BASE}/channels/{channel_id}/messages",
                    headers={
                        "Authorization": token,
                        "User-Agent": "DiscordBot (HUNTER, 1.0.0)"
                    },
                    data=payload,
                    files=files,
                    timeout=15
                )
                return {"status": r.status_code, "data": r.json() if r.text else {}}
            else:
                # Image download failed, send just the text with a note
                return send_message(token, channel_id, f"{content}\n\n[Image failed to load: {image_url}]")
        else:
            # No image, just send text
            r = requests.post(
                f"{API_BASE}/channels/{channel_id}/messages",
                headers=_headers(token),
                json={"content": content},
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
