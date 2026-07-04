import os
import json
from cryptography.fernet import Fernet

FERNET_KEY_FILE = "data/fernet.key"

def _get_fernet_key():
    """Load or generate Fernet key from file."""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(FERNET_KEY_FILE):
        with open(FERNET_KEY_FILE, "rb") as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        with open(FERNET_KEY_FILE, "wb") as f:
            f.write(key)
    return key

_fernet = Fernet(_get_fernet_key())

def encrypt_token(plain_token: str) -> str:
    """Encrypt a Discord token."""
    return _fernet.encrypt(plain_token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a Discord token."""
    return _fernet.decrypt(encrypted_token.encode()).decode()
