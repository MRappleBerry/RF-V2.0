"""
Microsoft OAuth2 Authorization Code Flow helpers for RedFlagged.
Uses the tenant's own CLIENT_ID/CLIENT_SECRET — no extra Azure app registration needed
beyond adding the redirect URI in the existing app's Authentication settings.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlencode

import requests as _requests

_AAD_BASE = "https://login.microsoftonline.com"
_SCOPES   = "openid profile email"


def build_auth_url(tenant_id: str, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id":     client_id,
        "response_type": "code",
        "redirect_uri":  redirect_uri,
        "scope":         _SCOPES,
        "state":         state,
        "response_mode": "query",
        "prompt":        "select_account",
    }
    return f"{_AAD_BASE}/{tenant_id}/oauth2/v2.0/authorize?" + urlencode(params)


def exchange_code(tenant_id: str, client_id: str, client_secret: str,
                  code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens. Returns the full token response dict."""
    resp = _requests.post(
        f"{_AAD_BASE}/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "code":          code,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def decode_id_token(id_token: str) -> dict:
    """Decode the JWT payload (no signature verification — token came from HTTPS token endpoint)."""
    parts = id_token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    # Pad to multiple of 4
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return {}


def get_user_email(claims: dict) -> str:
    """Extract email from id_token claims (preferred_username or email)."""
    return (claims.get("preferred_username") or claims.get("email") or claims.get("upn") or "").strip().lower()


def get_display_name(claims: dict) -> str:
    return (claims.get("name") or claims.get("given_name") or "").strip()
