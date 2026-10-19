"""
JSON file-based authentication provider for RedFlagged.

Users are stored in data/users.json as:
    {
        "users": [
            {
                "id": "user-XXXXXXXX",
                "email": "analyst@example.com",
                "display_name": "Jane Analyst",
                "password_hash": "<argon2id hash>",
                "role": "analyst",
                "active": true
            }
        ]
    }

Passwords are hashed with argon2id via argon2-cffi.
Never store, log, or return raw passwords or hashes.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
except ImportError as _argon2_import_error:
    raise RuntimeError(
        "argon2-cffi is required for password hashing. "
        "Install it with: pip install argon2-cffi>=23.1.0"
    ) from _argon2_import_error

from auth.auth_provider import AuthenticationProvider, AuthUser

logger = logging.getLogger(__name__)

_ph = PasswordHasher()


class JsonAuthenticationProvider(AuthenticationProvider):
    """
    Authentication provider backed by a local JSON file.
    Suitable for Phase 1 / local dev. Swap for OIDC or CyberArk in production.
    """

    def __init__(self, users_path: Path) -> None:
        self._users_path = users_path

    # ── Public API ──────────────────────────────────────────────────────────────

    def authenticate(self, email: str, password: str) -> AuthUser | None:
        """
        Return AuthUser when email + password are valid, None otherwise.
        Always runs a verify step (even on missing user) to prevent timing attacks.
        """
        normalized_email = email.strip().lower()
        users = self._load()
        matched = next(
            (u for u in users if u.get("email", "").strip().lower() == normalized_email),
            None,
        )
        if matched is None or not matched.get("active", False):
            self._dummy_verify()
            return None

        if not self._verify(str(matched.get("password_hash", "")), password):
            return None

        return self._to_user(matched)

    def get_user_by_id(self, user_id: str) -> AuthUser | None:
        """Return active AuthUser by ID for session hydration; None if not found."""
        users = self._load()
        matched = next(
            (u for u in users if str(u.get("id", "")) == user_id and u.get("active", False)),
            None,
        )
        if matched is None:
            return None
        return self._to_user(matched)

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _load(self) -> list[dict[str, Any]]:
        """Read data/users.json. Returns empty list on missing or corrupt file."""
        if not self._users_path.is_file():
            return []
        try:
            with self._users_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to load %s — treating as empty user store.", self._users_path)
            return []
        users = data.get("users") if isinstance(data, dict) else []
        return users if isinstance(users, list) else []

    def _verify(self, stored_hash: str, password: str) -> bool:
        """Argon2 verify. Returns True on match, False on any error."""
        if not stored_hash:
            return False
        try:
            _ph.verify(stored_hash, password)
            return True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def _dummy_verify(self) -> None:
        """
        Constant-time no-op called when no user is found.
        Prevents timing-based user enumeration by always spending time in
        the hash verification path.
        """
        try:
            _ph.verify(
                "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$dGVzdGhhc2g",
                "dummy_password_for_timing_safety",
            )
        except Exception:
            pass

    def _to_user(self, data: dict[str, Any]) -> AuthUser:
        """Build an AuthUser from a user record dict. Never includes the hash."""
        return AuthUser(
            id=str(data.get("id", "")),
            email=str(data.get("email", "")).strip().lower(),
            display_name=str(data.get("display_name", "")),
            role=str(data.get("role", "viewer")),
        )
