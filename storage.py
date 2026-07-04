import os
import json
import threading
from datetime import datetime, timedelta

DATA_DIR = "data"
FILES = ["users.json", "accounts.json", "campaigns.json", "subscriptions.json", "keys.json"]

_lock = threading.Lock()

def _init():
    os.makedirs(DATA_DIR, exist_ok=True)
    for fname in FILES:
        path = os.path.join(DATA_DIR, fname)
        if not os.path.exists(path):
            with open(path, "w") as f:
                json.dump([], f)

def _read(fname):
    path = os.path.join(DATA_DIR, fname)
    with open(path, "r") as f:
        return json.load(f)

def _write(fname, data):
    path = os.path.join(DATA_DIR, fname)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ─── Users ──────────────────────────────────────────────

def get_users():
    return _read("users.json")

def get_user(discord_id):
    users = get_users()
    for u in users:
        if u["discord_id"] == discord_id:
            return u
    return None

def upsert_user(discord_id, data):
    with _lock:
        users = get_users()
        for u in users:
            if u["discord_id"] == discord_id:
                u.update(data)
                _write("users.json", users)
                return
        users.append(data)
        _write("users.json", users)

def get_all_users():
    return get_users()

# ─── Accounts ───────────────────────────────────────────

def get_accounts():
    return _read("accounts.json")

def get_user_accounts(discord_id):
    return [a for a in get_accounts() if a["discord_id"] == discord_id]

def add_account(account):
    with _lock:
        accounts = get_accounts()
        accounts.append(account)
        _write("accounts.json", accounts)

def delete_account(account_id, discord_id):
    with _lock:
        accounts = get_accounts()
        new = [a for a in accounts if not (a["id"] == account_id and a["discord_id"] == discord_id)]
        if len(new) == len(accounts):
            return False
        _write("accounts.json", new)
        return True

def get_account_by_id(account_id):
    accounts = get_accounts()
    for a in accounts:
        if a["id"] == account_id:
            return a
    return None

# ─── Campaigns ──────────────────────────────────────────

def get_campaigns():
    return _read("campaigns.json")

def get_user_campaigns(discord_id):
    return [c for c in get_campaigns() if c["discord_id"] == discord_id]

def add_campaign(campaign):
    with _lock:
        camps = get_campaigns()
        camps.append(campaign)
        _write("campaigns.json", camps)

def update_campaign(campaign_id, data):
    with _lock:
        camps = get_campaigns()
        for c in camps:
            if c["id"] == campaign_id:
                c.update(data)
                _write("campaigns.json", camps)
                return

def get_campaign_by_id(campaign_id):
    camps = get_campaigns()
    for c in camps:
        if c["id"] == campaign_id:
            return c
    return None

def delete_campaign(campaign_id, discord_id):
    with _lock:
        camps = get_campaigns()
        new = [c for c in camps if not (c["id"] == campaign_id and c["discord_id"] == discord_id)]
        if len(new) == len(camps):
            return False
        _write("campaigns.json", new)
        return True

# ─── Subscriptions ──────────────────────────────────────

def get_subscriptions():
    return _read("subscriptions.json")

def add_subscription(sub):
    with _lock:
        subs = get_subscriptions()
        subs.append(sub)
        _write("subscriptions.json", subs)

def update_subscription(sub_id, data):
    with _lock:
        subs = get_subscriptions()
        for s in subs:
            if s["id"] == sub_id:
                s.update(data)
                _write("subscriptions.json", subs)
                return

def get_user_subscriptions(discord_id):
    return [s for s in get_subscriptions() if s["discord_id"] == discord_id]

# ─── Keys ───────────────────────────────────────────────

def get_keys():
    return _read("keys.json")

def add_key(key_data):
    with _lock:
        keys = get_keys()
        keys.append(key_data)
        _write("keys.json", keys)

def redeem_key(key_str, discord_id):
    with _lock:
        keys = get_keys()
        for k in keys:
            if k["key"] == key_str and not k.get("redeemed_by"):
                k["redeemed_by"] = discord_id
                k["redeemed_at"] = datetime.utcnow().isoformat()
                _write("keys.json", keys)
                return k
        return None

# ─── Plans ──────────────────────────────────────────────

PLANS = {
    "free":     {"name": "Free",     "price": 0,  "accounts": 1,  "features": ["send_all_once"]},
    "v1":       {"name": "V1",       "price": 3,  "accounts": 1,  "features": ["send_all_once"]},
    "v2":       {"name": "V2",       "price": 5,  "accounts": 3,  "features": ["send_all_once", "image_attachments"]},
    "v3":       {"name": "V3",       "price": 7,  "accounts": 5,  "features": ["send_all_once", "image_attachments", "dm_auto_reply"]},
    "lifetime": {"name": "Lifetime", "price": 30, "accounts": 5,  "features": ["send_all_once", "image_attachments", "dm_auto_reply", "lifetime"]},
}

def get_plan_max_accounts(plan):
    return PLANS.get(plan, PLANS["free"])["accounts"]

def get_plan_features(plan):
    return PLANS.get(plan, PLANS["free"])["features"]

def get_user_effective_plan(discord_id):
    """Return the user's current plan checking subscriptions + trial."""
    user = get_user(discord_id)
    if not user:
        return "free"

    # Check lifetime first
    for sub in get_user_subscriptions(discord_id):
        if sub["plan"] == "lifetime" and sub["status"] == "confirmed":
            return "lifetime"

    # Check active paid subs
    for sub in get_user_subscriptions(discord_id):
        if sub["status"] == "confirmed":
            try:
                expires = datetime.fromisoformat(sub["expires_at"])
                if expires > datetime.utcnow():
                    return sub["plan"]
            except:
                pass

    # Check trial
    if user.get("trial_active"):
        try:
            trial_exp = datetime.fromisoformat(user["trial_expires_at"])
            if trial_exp > datetime.utcnow():
                return "v3"
        except:
            pass

    return "free"

def get_plan_name(plan):
    return PLANS.get(plan, {}).get("name", plan.capitalize())

_init()
