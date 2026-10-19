"""
Session management service for RedFlagged.

Manages in-memory session tokens backed by an AuthenticationProvider.
Tokens are HttpOnly cookies; this module never sees raw passwords.

All auth events are logged with structured fields for auditability:
  action, result, user_id, email, ip
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from auth.auth_provider import AuthenticationProvider, AuthUser

logger = logging.getLogger(__name__)

COOKIE_NAME = "rf_session"

_SESSION_TTL_HOURS = 8


class AuthService:
    """
    Stateful session service. One instance per server process.

    Sessions are stored in memory only — they are lost on server restart.
    Phase 2 can back this with Redis or a DB table.
    """

    def __init__(self, provider: AuthenticationProvider) -> None:
        self._provider = provider
        # token → {"user": AuthUser, "expires": datetime (UTC)}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────────────────

    def create_ms_session(self, email: str, display_name: str, role: str, *, source_ip: str = "") -> str:
        """Create a session for a user authenticated via Microsoft OAuth. No password required."""
        from auth.auth_provider import AuthUser
        user = AuthUser(id=email, email=email, display_name=display_name, role=role)
        token = secrets.token_urlsafe(48)
        expires = datetime.now(tz=timezone.utc) + timedelta(hours=_SESSION_TTL_HOURS)
        with self._lock:
            self._sessions[token] = {"user": user, "expires": expires}
        logger.info("auth_event action=ms_login email=%r ip=%r", email, source_ip)
        return token

    def login(self, email: str, password: str, *, source_ip: str = "") -> str | None:
        """
        Validate credentials and create a session on success.
        Returns the session token (str) or None on failure.
        Never logs or returns the password.
        """
        user = self._provider.authenticate(email, password)
        if user is None:
            logger.warning(
                "auth_event action=login_failure email=%r ip=%r",
                email.strip().lower(),
                source_ip,
            )
            return None

        token = secrets.token_urlsafe(48)
        expires = datetime.now(tz=timezone.utc) + timedelta(hours=_SESSION_TTL_HOURS)
        with self._lock:
            self._sessions[token] = {"user": user, "expires": expires}

        logger.info(
            "auth_event action=login_success user_id=%r email=%r ip=%r",
            user.id,
            user.email,
            source_ip,
        )
        return token

    def get_user(self, token: str) -> AuthUser | None:
        """
        Validate the token and return the associated AuthUser.
        Removes expired sessions. Returns None if token is missing, invalid, or expired.
        """
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if datetime.now(tz=timezone.utc) >= session["expires"]:
                del self._sessions[token]
                user = session["user"]
                logger.info(
                    "auth_event action=session_expired user_id=%r email=%r",
                    user.id,
                    user.email,
                )
                return None
            return session["user"]

    def logout(self, token: str, *, source_ip: str = "") -> None:
        """Invalidate the session. Safe to call with an already-expired or missing token."""
        with self._lock:
            session = self._sessions.pop(token, None)
        if session is not None:
            user: AuthUser = session["user"]
            logger.info(
                "auth_event action=logout user_id=%r email=%r ip=%r",
                user.id,
                user.email,
                source_ip,
            )

    def is_authenticated(self, token: str) -> bool:
        """Boolean shorthand — True iff the token maps to an active session."""
        return self.get_user(token) is not None

    def cleanup_expired(self) -> int:
        """Remove all stale sessions. Returns the count removed."""
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            expired_tokens = [t for t, s in self._sessions.items() if now >= s["expires"]]
            for t in expired_tokens:
                del self._sessions[t]
        if expired_tokens:
            logger.debug("auth_event action=cleanup_expired count=%d", len(expired_tokens))
        return len(expired_tokens)
