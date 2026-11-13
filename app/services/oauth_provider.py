from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote, urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl, TypeAdapter

from app.config import Settings
from app.database import SessionLocal
from app.models import Setting


OAUTH_SCOPE = "classbrief"
ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
# The approval page is completed manually in ChatGPT's in-app browser. Keep the
# signed, single-use request alive long enough for users to switch views and
# retrieve their ClassBrief password without racing a short timeout.
APPROVAL_TTL_SECONDS = 60 * 60


class OAuthRequestError(ValueError):
    pass


class ClassBriefOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Persistent, single-user OAuth provider for the private ClassBrief server."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.issuer_url = settings.app_base_url.rstrip("/")
        self.resource_url = f"{self.issuer_url}/mcp"
        self.approval_url = f"{self.issuer_url}/oauth/approve"

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @classmethod
    def _key(cls, prefix: str, value: str) -> str:
        return f"oauth.{prefix}.{cls._digest(value)}"

    @staticmethod
    def _save_value(key: str, value: str) -> None:
        with SessionLocal() as db:
            row = db.get(Setting, key)
            if row is None:
                db.add(Setting(key=key, value=value))
            else:
                row.value = value
            db.commit()

    @staticmethod
    def _load_value(key: str) -> str | None:
        with SessionLocal() as db:
            row = db.get(Setting, key)
            return row.value if row else None

    @staticmethod
    def _delete_value(key: str) -> None:
        with SessionLocal() as db:
            row = db.get(Setting, key)
            if row is not None:
                db.delete(row)
                db.commit()

    @classmethod
    def _save_model(cls, prefix: str, token: str, model: Any) -> None:
        cls._save_value(cls._key(prefix, token), model.model_dump_json())

    @classmethod
    def _load_model(cls, prefix: str, token: str, model_type):
        raw = cls._load_value(cls._key(prefix, token))
        return model_type.model_validate_json(raw) if raw else None

    def _sign_request_id(self, request_id: str) -> str:
        return hmac.new(self.settings.session_secret.encode(), request_id.encode(), hashlib.sha256).hexdigest()

    def _request_token(self, request_id: str) -> str:
        return f"{request_id}.{self._sign_request_id(request_id)}"

    def _parse_request_token(self, request_token: str) -> str:
        try:
            request_id, signature = request_token.rsplit(".", 1)
        except ValueError as exc:
            raise OAuthRequestError("Ungültige Freigabeanfrage") from exc
        if not hmac.compare_digest(signature, self._sign_request_id(request_id)):
            raise OAuthRequestError("Ungültige Freigabeanfrage")
        return request_id

    def read_authorization_request(self, request_token: str, *, consume: bool = False) -> dict[str, Any]:
        request_id = self._parse_request_token(request_token)
        key = self._key("pending", request_id)
        raw = self._load_value(key)
        if raw is None:
            raise OAuthRequestError("Die Freigabeanfrage ist nicht mehr verfügbar")
        payload = json.loads(raw)
        if float(payload.get("expires_at", 0)) <= time.time():
            self._delete_value(key)
            raise OAuthRequestError("Die Freigabeanfrage ist abgelaufen")
        if consume:
            self._delete_value(key)
        return payload

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._load_model("client", client_id, OAuthClientInformationFull)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError("invalid_client_metadata", "client_id fehlt")
        allowed_hosts = {"chatgpt.com", "chat.openai.com"}
        if not self.settings.production:
            allowed_hosts.update({"localhost", "127.0.0.1", "testserver"})
        for redirect_uri in client_info.redirect_uris or []:
            parsed = urlparse(str(redirect_uri))
            if parsed.scheme not in {"https", "http"} or parsed.hostname not in allowed_hosts:
                raise RegistrationError("invalid_redirect_uri", "Nicht erlaubte OAuth-Weiterleitungsadresse")
            if self.settings.production and parsed.scheme != "https":
                raise RegistrationError("invalid_redirect_uri", "OAuth-Weiterleitungen müssen HTTPS verwenden")
        self._save_model("client", client_info.client_id, client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not client.client_id:
            raise AuthorizeError("invalid_request", "client_id fehlt")
        resource = params.resource or self.resource_url
        if resource.rstrip("/") != self.resource_url.rstrip("/"):
            raise AuthorizeError("invalid_request", "Ungültige MCP-Ressource")
        request_id = secrets.token_urlsafe(32)
        payload = {
            "client_id": client.client_id,
            "client_name": client.client_name or "ChatGPT",
            "state": params.state,
            "scopes": params.scopes or [OAUTH_SCOPE],
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": self.resource_url,
            "expires_at": time.time() + APPROVAL_TTL_SECONDS,
        }
        self._save_value(self._key("pending", request_id), json.dumps(payload, separators=(",", ":")))
        return f"{self.approval_url}?request={quote(self._request_token(request_id))}"

    async def complete_authorization(self, request_token: str) -> str:
        payload = self.read_authorization_request(request_token, consume=True)
        client = await self.get_client(payload["client_id"])
        if client is None:
            raise OAuthRequestError("Der OAuth-Client ist nicht mehr registriert")
        code_value = secrets.token_urlsafe(32)
        authorization_code = AuthorizationCode(
            code=code_value,
            scopes=payload["scopes"],
            expires_at=time.time() + 5 * 60,
            client_id=payload["client_id"],
            code_challenge=payload["code_challenge"],
            redirect_uri=TypeAdapter(AnyUrl).validate_python(payload["redirect_uri"]),
            redirect_uri_provided_explicitly=payload["redirect_uri_provided_explicitly"],
            resource=payload["resource"],
            subject="classbrief-single-user",
        )
        self._save_model("code", code_value, authorization_code)
        return construct_redirect_uri(payload["redirect_uri"], code=code_value, state=payload.get("state"))

    def deny_authorization(self, request_token: str) -> str:
        payload = self.read_authorization_request(request_token, consume=True)
        return construct_redirect_uri(
            payload["redirect_uri"],
            error="access_denied",
            error_description="ClassBrief-Zugriff wurde nicht freigegeben",
            state=payload.get("state"),
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._load_model("code", authorization_code, AuthorizationCode)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    def _issue_tokens(
        self, client_id: str, scopes: list[str], resource: str | None, subject: str | None
    ) -> OAuthToken:
        now = int(time.time())
        access_value = secrets.token_urlsafe(48)
        refresh_value = secrets.token_urlsafe(48)
        access_token = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource or self.resource_url,
            subject=subject,
            claims={"iss": self.issuer_url},
        )
        refresh_token = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
            resource=resource or self.resource_url,
            subject=subject,
        )
        self._save_model("access", access_value, access_token)
        self._save_model("refresh", refresh_value, refresh_token)
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh_value,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if authorization_code.client_id != client.client_id:
            raise TokenError("invalid_grant", "Der Autorisierungscode gehört zu einem anderen Client")
        self._delete_value(self._key("code", authorization_code.code))
        return self._issue_tokens(
            authorization_code.client_id,
            authorization_code.scopes,
            authorization_code.resource,
            authorization_code.subject,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self._load_model("refresh", refresh_token, RefreshToken)
        if token is None or token.client_id != client.client_id:
            return None
        if token.expires_at is not None and token.expires_at <= int(time.time()):
            self._delete_value(self._key("refresh", refresh_token))
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if refresh_token.client_id != client.client_id:
            raise TokenError("invalid_grant", "Das Aktualisierungstoken gehört zu einem anderen Client")
        self._delete_value(self._key("refresh", refresh_token.token))
        return self._issue_tokens(
            refresh_token.client_id,
            scopes,
            refresh_token.resource,
            refresh_token.subject,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self._load_model("access", token, AccessToken)
        if access_token is None:
            return None
        if access_token.expires_at is not None and access_token.expires_at <= int(time.time()):
            self._delete_value(self._key("access", token))
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._delete_value(self._key("access", token.token))
        self._delete_value(self._key("refresh", token.token))
