"""
Authentication provider abstraction for RedFlagged.
Concrete providers subclass AuthenticationProvider.
Business logic depends only on AuthUser and AuthService — never on the provider.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthUser:
    """Immutable authenticated user record."""
    id: str
    email: str
    display_name: str
    role: str  # admin | analyst | viewer


class AuthenticationProvider(ABC):
    """Provider contract. Swap implementations without touching business logic."""

    @abstractmethod
    def authenticate(self, email: str, password: str) -> AuthUser | None:
        """Return AuthUser on valid credentials, None on any failure."""
        ...

    @abstractmethod
    def get_user_by_id(self, user_id: str) -> AuthUser | None:
        """Return active AuthUser by ID (session hydration). None if not found."""
        ...
