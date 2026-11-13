from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import Settings
from app.models import Setting
from sqlalchemy.orm import Session

PASSWORD_HASH_SETTING = "app_password_hash"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$", 5)
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.urlsafe_b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


def effective_app_password_hash(settings: Settings, db: Session | None = None) -> str:
    if db is not None:
        override = db.get(Setting, PASSWORD_HASH_SETTING)
        if override and override.value:
            return override.value
    return settings.app_password_hash


def verify_app_password(password: str, settings: Settings, db: Session | None = None) -> bool:
    stored = effective_app_password_hash(settings, db)
    if stored:
        return verify_password(password, stored)
    return not settings.production and hmac.compare_digest(password, "classbrief")


def sign_session(secret: str, max_age_seconds: int = 60 * 60 * 24 * 30) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"auth": True, "exp": int(time.time()) + max_age_seconds}).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session(token: str | None, secret: str) -> bool:
    if not token or "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return data.get("auth") is True and data.get("exp", 0) > time.time()
    except (ValueError, json.JSONDecodeError):
        return False
