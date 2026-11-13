#!/usr/bin/env python3
"""One-time local OAuth bridge for a self-hosted ClassBrief instance.

This file intentionally uses only Python's standard library so the downloaded
helper can run without installing packages. It never receives a ClassBrief or
ChatGPT password; it transfers the OAuth result through one short-lived code.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def request_json(url: str, *, token: str = "", payload: dict | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "ClassBrief-Connector/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"ClassBrief meldet HTTP {exc.code}: {detail}") from exc


def exchange_code(*, client_id: str, code: str, verifier: str, redirect_uri: str) -> dict:
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "resource": RESOURCE,
    }).encode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"OpenAI-Anmeldung fehlgeschlagen ({exc.code}): {detail}") from exc


class CallbackHandler(BaseHTTPRequestHandler):
    result: dict | None = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_error(404)
            return
        type(self).result = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        body = (
            "<!doctype html><html lang='de'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>ClassBrief verbunden</title><body style='font:18px system-ui;padding:48px;max-width:620px;margin:auto'>"
            "<h1>Fast geschafft.</h1><p>Du kannst dieses Fenster schließen und zu ClassBrief zurückkehren.</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, _format, *_args):
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="ClassBrief einmalig mit deinem ChatGPT-Abo verbinden")
    parser.add_argument("--classbrief", required=True, help="ClassBrief-Basisadresse")
    parser.add_argument("--pair-token", required=True, help="15 Minuten gültiger Kopplungscode")
    args = parser.parse_args()
    base_url = args.classbrief.rstrip("/")
    pairing = request_json(f"{base_url}/api/chatgpt/pairing", token=args.pair_token)

    CallbackHandler.result = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    client_id = pairing.get("client_id") or "dynamic_agent_client"
    params = {
        "client_id": client_id,
        "ext_agent_host_id": pairing["host_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = "ClassBrief"
    authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    print("\nDie sichere OpenAI-Anmeldung wird geöffnet …")
    print("Falls kein Browser erscheint, öffne diese Adresse manuell:\n" + authorize_url + "\n")
    webbrowser.open(authorize_url)
    server.serve_forever()
    callback = CallbackHandler.result or {}
    if callback.get("state") != state:
        raise SystemExit("Die Anmeldung konnte nicht sicher zugeordnet werden.")
    if callback.get("error"):
        raise SystemExit("OpenAI-Anmeldung abgebrochen: " + callback.get("error_description", callback["error"]))
    issued_client_id = callback.get("client_id") or (client_id if client_id != "dynamic_agent_client" else "")
    if not issued_client_id or not callback.get("code"):
        raise SystemExit("OpenAI hat keine vollständige Freigabe zurückgegeben.")
    tokens = exchange_code(client_id=issued_client_id, code=callback["code"], verifier=verifier, redirect_uri=redirect_uri)
    result = request_json(
        f"{base_url}/api/chatgpt/pairing",
        token=args.pair_token,
        payload={
            "client_id": issued_client_id,
            "id_token": tokens["id_token"],
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_in": tokens.get("expires_in", 3600),
            "scope": tokens.get("scope") or callback.get("scope") or SCOPES,
            "nonce": nonce,
        },
    )
    print("\n✓ " + result.get("message", "ChatGPT wurde mit ClassBrief verbunden."))
    print("Du kannst dieses Fenster jetzt schließen.")


if __name__ == "__main__":
    main()
