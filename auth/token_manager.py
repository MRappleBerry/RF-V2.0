"""
MSAL token manager for Microsoft Graph and Microsoft Defender API.

Acquires and caches OAuth 2.0 tokens using the client credentials flow
(app-only, no user sign-in required). Tokens are refreshed automatically
when they expire.

Scopes:
  Graph:    https://graph.microsoft.com/.default
  Defender: https://api.security.microsoft.com/.default
"""

from __future__ import annotations

import msal


_graph_app: msal.ConfidentialClientApplication | None = None
_defender_app: msal.ConfidentialClientApplication | None = None

_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
_DEFENDER_SCOPE = ["https://api.security.microsoft.com/.default"]


def _make_app(tenant_id: str, client_id: str, client_secret: str) -> msal.ConfidentialClientApplication:
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )


def init(tenant_id: str, client_id: str, client_secret: str) -> None:
    """Initialise both MSAL app instances. Call this once after config is loaded."""
    global _graph_app, _defender_app
    _graph_app = _make_app(tenant_id, client_id, client_secret)
    _defender_app = _make_app(tenant_id, client_id, client_secret)


def _acquire(app: msal.ConfidentialClientApplication, scope: list[str]) -> str:
    result = app.acquire_token_silent(scope, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=scope)

    if "access_token" not in result:
        error = result.get("error_description") or result.get("error") or "Unknown MSAL error"
        raise RuntimeError(f"Token acquisition failed: {error}")

    return result["access_token"]


def get_graph_token() -> str:
    if _graph_app is None:
        raise RuntimeError("token_manager.init() has not been called.")
    return _acquire(_graph_app, _GRAPH_SCOPE)


def get_defender_token() -> str:
    if _defender_app is None:
        raise RuntimeError("token_manager.init() has not been called.")
    return _acquire(_defender_app, _DEFENDER_SCOPE)
