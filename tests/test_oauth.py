import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_oauth_metadata_and_authorization_code_flow(client):
    resource_metadata = client.get("/.well-known/oauth-protected-resource/mcp")
    assert resource_metadata.status_code == 200
    assert resource_metadata.json()["resource"] == "http://localhost:8000/mcp"

    registration = client.post(
        "/register",
        json={
            "redirect_uris": ["https://chatgpt.com/connector/oauth/pytest"],
            "token_endpoint_auth_method": "client_secret_post",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "ChatGPT pytest",
            "scope": "classbrief",
        },
    )
    assert registration.status_code == 201
    registered = registration.json()

    verifier = secrets.token_urlsafe(48)
    authorize = client.get(
        "/authorize",
        params={
            "client_id": registered["client_id"],
            "redirect_uri": "https://chatgpt.com/connector/oauth/pytest",
            "response_type": "code",
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": "pytest-state",
            "scope": "classbrief",
            "resource": "http://localhost:8000/mcp",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302
    approval_url = authorize.headers["location"]
    assert "/oauth/approve?request=" in approval_url

    approval_page = client.get(approval_url)
    assert approval_page.status_code == 200
    assert "ClassBrief-Passwort" in approval_page.text
    assert "form-action 'self' https://chatgpt.com https://chat.openai.com" in approval_page.headers[
        "content-security-policy"
    ]
    request_token = parse_qs(urlparse(approval_url).query)["request"][0]

    approval = client.post(
        "/oauth/approve",
        data={"request": request_token, "password": "classbrief", "decision": "approve"},
        follow_redirects=False,
    )
    assert approval.status_code == 302
    callback = urlparse(approval.headers["location"])
    callback_query = parse_qs(callback.query)
    assert callback_query["state"] == ["pytest-state"]
    code = callback_query["code"][0]

    token_response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://chatgpt.com/connector/oauth/pytest",
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
            "code_verifier": verifier,
            "resource": "http://localhost:8000/mcp",
        },
    )
    assert token_response.status_code == 200
    token = token_response.json()
    assert token["token_type"] == "Bearer"
    assert token["scope"] == "classbrief"

    initialize = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token['access_token']}",
            "Accept": "application/json, text/event-stream",
            "Host": "localhost:8000",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )
    assert initialize.status_code == 200
    assert initialize.json()["result"]["serverInfo"]["name"] == "ClassBrief"


def test_oauth_approval_rejects_wrong_classbrief_password(client):
    registration = client.post(
        "/register",
        json={
            "redirect_uris": ["https://chatgpt.com/connector/oauth/pytest-wrong"],
            "token_endpoint_auth_method": "client_secret_post",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "classbrief",
        },
    ).json()
    verifier = secrets.token_urlsafe(48)
    authorize = client.get(
        "/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": "https://chatgpt.com/connector/oauth/pytest-wrong",
            "response_type": "code",
            "code_challenge": _pkce_challenge(verifier),
            "scope": "classbrief",
            "resource": "http://localhost:8000/mcp",
        },
        follow_redirects=False,
    )
    request_token = parse_qs(urlparse(authorize.headers["location"]).query)["request"][0]
    rejected = client.post(
        "/oauth/approve",
        data={"request": request_token, "password": "wrong", "decision": "approve"},
        follow_redirects=False,
    )
    assert rejected.status_code == 401
    assert "stimmt nicht" in rejected.text
