from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Setting

ACCESS_TOKEN_SETTING = "web_access_token"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_web_access_link(db: Session, settings: Settings, *, lifetime_minutes: int = 15) -> dict[str, str | int]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=max(5, min(lifetime_minutes, 60)))
    payload = json.dumps({"token_hash": _digest(token), "expires_at": expires_at.isoformat()})
    row = db.get(Setting, ACCESS_TOKEN_SETTING)
    if row:
        row.value = payload
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Setting(key=ACCESS_TOKEN_SETTING, value=payload))
    db.commit()
    base = settings.app_base_url.rstrip("/")
    return {
        "url": f"{base}/access?token={quote(token)}",
        "expires_at": expires_at.isoformat(),
        "lifetime_minutes": max(5, min(lifetime_minutes, 60)),
    }


def valid_web_access_token(db: Session, token: str) -> bool:
    row = db.get(Setting, ACCESS_TOKEN_SETTING)
    if not row or not token:
        return False
    try:
        payload = json.loads(row.value)
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc) and hmac.compare_digest(
            str(payload["token_hash"]), _digest(token)
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def consume_web_access_token(db: Session, token: str) -> bool:
    if not valid_web_access_token(db, token):
        return False
    row = db.get(Setting, ACCESS_TOKEN_SETTING)
    if row:
        db.delete(row)
    return True
