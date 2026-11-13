from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ChatGPTConnection, ChatGPTPairing

OPENAI_ISSUER = "https://auth.openai.com"
OPENAI_JWKS = f"{OPENAI_ISSUER}/.well-known/jwks.json"
OPENAI_DISCOVERY = f"{OPENAI_ISSUER}/.well-known/openid-configuration"
OPENAI_TOKEN_ENDPOINT = f"{OPENAI_ISSUER}/api/accounts/oauth/token"
OPENAI_REVOCATION_ENDPOINT = f"{OPENAI_ISSUER}/api/accounts/oauth/revoke"
PLAN_SCOPE = "chatgpt.tokens.use.direct"


class ChatGPTPlanError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fernet(settings: Settings) -> Fernet:
    material = settings.credential_encryption_key or settings.session_secret
    if not material or material == "dev-only-change-me" and settings.production:
        raise ChatGPTPlanError("CREDENTIAL_ENCRYPTION_KEY ist in Produktion nicht eingerichtet")
    digest = hashlib.sha256(("classbrief:chatgpt-plan:" + material).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_credentials(settings: Settings, credentials: dict) -> str:
    raw = json.dumps(credentials, ensure_ascii=False, separators=(",", ":")).encode()
    return _fernet(settings).encrypt(raw).decode()


def decrypt_credentials(settings: Settings, connection: ChatGPTConnection) -> dict:
    try:
        raw = _fernet(settings).decrypt(connection.encrypted_credentials.encode())
        value = json.loads(raw)
    except (InvalidToken, json.JSONDecodeError, TypeError) as exc:
        raise ChatGPTPlanError("Die geschützte ChatGPT-Verbindung konnte nicht gelesen werden") from exc
    if not isinstance(value, dict):
        raise ChatGPTPlanError("Die ChatGPT-Verbindung ist ungültig")
    return value


def active_connection(db: Session) -> ChatGPTConnection | None:
    return db.scalar(
        select(ChatGPTConnection)
        .where(ChatGPTConnection.status == "connected")
        .order_by(ChatGPTConnection.updated_at.desc())
        .limit(1)
    )


def provider_mode(db: Session, settings: Settings) -> str:
    if active_connection(db):
        return "chatgpt_plan"
    return settings.ai_provider


def create_pairing(db: Session) -> tuple[ChatGPTPairing, str, str | None]:
    connection = active_connection(db)
    raw_token = secrets.token_urlsafe(36)
    pairing = ChatGPTPairing(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        host_id=connection.host_id if connection else f"urn:uuid:{uuid.uuid4()}",
        expires_at=_now() + timedelta(minutes=15),
    )
    db.add(pairing)
    db.commit()
    return pairing, raw_token, connection.client_id if connection else None


def get_pairing(db: Session, raw_token: str) -> ChatGPTPairing:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    pairing = db.scalar(select(ChatGPTPairing).where(ChatGPTPairing.token_hash == token_hash))
    if pairing is None or pairing.consumed_at is not None or _aware(pairing.expires_at) <= _now():
        raise ChatGPTPlanError("Dieser Kopplungscode ist ungültig oder abgelaufen")
    return pairing


async def validate_id_token(id_token: str, client_id: str, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256"}:
            raise ChatGPTPlanError("Nicht unterstützte OpenAI-Signatur")
        async with httpx.AsyncClient(timeout=20, verify=True) as client:
            response = await client.get(OPENAI_JWKS)
            response.raise_for_status()
        key_set = jwt.PyJWKSet.from_dict(response.json())
        signing_key = next((item.key for item in key_set.keys if item.key_id == header.get("kid")), None)
        if signing_key is None:
            raise ChatGPTPlanError("OpenAI-Signaturschlüssel nicht gefunden")
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=client_id,
            issuer=OPENAI_ISSUER,
            options={"require": ["exp", "iss", "sub"]},
        )
    except ChatGPTPlanError:
        raise
    except (httpx.HTTPError, jwt.PyJWTError, StopIteration, ValueError) as exc:
        raise ChatGPTPlanError("Die OpenAI-Identität konnte nicht verifiziert werden") from exc
    if claims.get("nonce") != nonce:
        raise ChatGPTPlanError("Die OpenAI-Anmeldung gehört nicht zu diesem Kopplungsvorgang")
    return claims


async def import_connection(
    db: Session,
    settings: Settings,
    *,
    pairing: ChatGPTPairing,
    client_id: str,
    id_token: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scope: str,
    nonce: str,
) -> ChatGPTConnection:
    scopes = set(scope.split())
    if PLAN_SCOPE not in scopes:
        raise ChatGPTPlanError("ChatGPT-Plan-Nutzung wurde nicht freigegeben")
    claims = await validate_id_token(id_token, client_id, nonce)
    expires_at = _now() + timedelta(seconds=max(60, min(int(expires_in), 86400)))
    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "expires_at": expires_at.isoformat(),
    }
    existing = db.scalar(select(ChatGPTConnection).where(ChatGPTConnection.client_id == client_id))
    if existing is None:
        for old in db.scalars(select(ChatGPTConnection).where(ChatGPTConnection.status == "connected")):
            old.status = "replaced"
        existing = ChatGPTConnection(
            email=str(claims.get("email") or "ChatGPT-Konto"),
            openai_subject=str(claims["sub"]),
            client_id=client_id,
            host_id=pairing.host_id,
            encrypted_credentials="",
        )
        db.add(existing)
    existing.email = str(claims.get("email") or existing.email)
    existing.openai_subject = str(claims["sub"])
    existing.host_id = pairing.host_id
    existing.encrypted_credentials = encrypt_credentials(settings, credentials)
    existing.scopes = " ".join(sorted(scopes))
    existing.status = "connected"
    existing.updated_at = _now()
    pairing.consumed_at = _now()
    db.commit()
    db.refresh(existing)
    return existing


async def access_token(db: Session, settings: Settings, connection: ChatGPTConnection) -> str:
    credentials = decrypt_credentials(settings, connection)
    try:
        expires_at = datetime.fromisoformat(str(credentials["expires_at"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ChatGPTPlanError("Ablaufzeit der ChatGPT-Verbindung fehlt") from exc
    if _aware(expires_at) > _now() + timedelta(minutes=2):
        return str(credentials["access_token"])
    try:
        async with httpx.AsyncClient(timeout=30, verify=True) as client:
            response = await client.post(
                OPENAI_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": connection.client_id,
                    "refresh_token": credentials["refresh_token"],
                    "resource": "https://api.openai.com/v1",
                },
            )
        response.raise_for_status()
        refreshed = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        connection.status = "needs_reconnect"
        db.commit()
        raise ChatGPTPlanError("ChatGPT muss erneut verbunden werden") from exc
    credentials.update(
        access_token=refreshed["access_token"],
        refresh_token=refreshed.get("refresh_token", credentials["refresh_token"]),
        id_token=refreshed.get("id_token", credentials.get("id_token")),
        expires_at=(_now() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))).isoformat(),
    )
    connection.encrypted_credentials = encrypt_credentials(settings, credentials)
    if refreshed.get("scope"):
        connection.scopes = str(refreshed["scope"])
    connection.updated_at = _now()
    db.commit()
    return str(credentials["access_token"])


async def disconnect(db: Session, settings: Settings, connection: ChatGPTConnection) -> None:
    try:
        credentials = decrypt_credentials(settings, connection)
        async with httpx.AsyncClient(timeout=20, verify=True) as client:
            revocation_endpoint = OPENAI_REVOCATION_ENDPOINT
            try:
                discovery = await client.get(OPENAI_DISCOVERY)
                discovery.raise_for_status()
                revocation_endpoint = discovery.json().get("revocation_endpoint") or revocation_endpoint
            except (httpx.HTTPError, ValueError, TypeError):
                pass
            await client.post(
                revocation_endpoint,
                data={
                    "token": credentials.get("refresh_token", ""),
                    "token_type_hint": "refresh_token",
                    "client_id": connection.client_id,
                },
            )
    finally:
        db.delete(connection)
        db.commit()
