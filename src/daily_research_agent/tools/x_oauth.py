from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlencode, urlparse, parse_qs

import httpx


AUTH_BASE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
DEFAULT_TOKEN_FILENAME = "x_oauth_tokens.json"


@dataclass(frozen=True)
class OAuthState:
    code_verifier: str
    code_challenge: str
    state: str


class XOAuthError(RuntimeError):
    pass


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_oauth_state() -> OAuthState:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url_encode(digest)
    state = secrets.token_urlsafe(32)
    return OAuthState(code_verifier=verifier, code_challenge=challenge, state=state)


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    scopes: List[str],
    oauth_state: OAuthState,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": oauth_state.state,
        "code_challenge": oauth_state.code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_BASE_URL}?{urlencode(params)}"


def parse_redirect_url(url: str) -> Dict[str, str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {key: values[0] for key, values in query.items() if values}


def exchange_code_for_token(
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    client_secret: Optional[str] = None,
) -> Dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_secret:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("ascii")).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"

    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, data=data, headers=headers)
        if resp.status_code >= 400:
            raise XOAuthError(f"Token request failed: {resp.status_code} {resp.text}")
        return resp.json()


def refresh_access_token(
    client_id: str,
    refresh_token: str,
    client_secret: Optional[str] = None,
) -> Dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if client_secret:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("ascii")).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"

    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(TOKEN_URL, data=data, headers=headers)
        if resp.status_code >= 400:
            raise XOAuthError(f"Refresh request failed: {resp.status_code} {resp.text}")
        return resp.json()


def token_file_path(state_dir: Path, filename: str = DEFAULT_TOKEN_FILENAME) -> Path:
    return state_dir / filename


def _normalize_token_payload(payload: Dict[str, str]) -> Dict[str, str]:
    # Keep the raw response mostly intact for debugging, but always add timestamps.
    normalized = dict(payload)
    normalized["obtained_at"] = datetime.now(timezone.utc).isoformat()
    return normalized


def load_token_payload(path: Path) -> Optional[Dict[str, str]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise XOAuthError(f"Failed to read token file: {path}") from exc


def save_token_payload(path: Path, payload: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_token_payload(payload)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_env(name: str, fallback: Optional[str]) -> Optional[str]:
    return fallback or os.getenv(name)
