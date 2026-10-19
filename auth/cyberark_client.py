"""
CyberArk Central Credential Provider (CCP) REST client.

Retrieves secrets from a CyberArk Safe at runtime — no credentials stored in code or config files.
The application itself authenticates to CyberArk via its registered App ID (certificate-based or
IP-based trust, configured by your CyberArk admin).

Usage:
    client = CyberArkClient(
        host="cyberark.yourcompany.com",
        app_id="RedFlagged",
        safe="RedFlagged-prod"
    )
    secret = client.get_secret("AzureClientSecret")
"""

import requests


class CyberArkError(Exception):
    pass


class CyberArkClient:
    def __init__(self, host: str, app_id: str, safe: str, verify_ssl: bool = True):
        self._base_url = f"https://{host}/AIMWebService/api/Accounts"
        self._app_id = app_id
        self._safe = safe
        self._verify_ssl = verify_ssl
        self._cache: dict[str, str] = {}

    def get_secret(self, object_name: str, use_cache: bool = True) -> str:
        """Retrieve a secret value from CyberArk by object name."""
        if use_cache and object_name in self._cache:
            return self._cache[object_name]

        try:
            resp = requests.get(
                self._base_url,
                params={
                    "AppID": self._app_id,
                    "Safe": self._safe,
                    "Object": object_name,
                },
                verify=self._verify_ssl,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise CyberArkError(f"CyberArk request failed for '{object_name}': {exc}") from exc

        if resp.status_code != 200:
            raise CyberArkError(
                f"CyberArk returned HTTP {resp.status_code} for object '{object_name}': {resp.text[:200]}"
            )

        try:
            value = resp.json()["Content"]
        except (ValueError, KeyError) as exc:
            raise CyberArkError(f"Unexpected CyberArk response format for '{object_name}'") from exc

        self._cache[object_name] = value
        return value

    def clear_cache(self) -> None:
        self._cache.clear()
