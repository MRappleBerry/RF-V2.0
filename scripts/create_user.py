#!/usr/bin/env python3
"""
CLI tool to add users to data/users.json.

Usage:
    python scripts/create_user.py

Prompts for email, display name, role, and password (via getpass).
Hashes the password with argon2id before storing — the raw password is
never written to disk or printed.
"""
from __future__ import annotations

import getpass
import json
import sys
import uuid
from pathlib import Path

# Add repo root to sys.path so auth/data packages are importable
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from argon2 import PasswordHasher
except ImportError:
    print("ERROR: argon2-cffi is required. Install it with: pip install argon2-cffi>=23.1.0")
    sys.exit(1)

_VALID_ROLES = {"admin", "analyst", "viewer"}
_MIN_PASSWORD_LENGTH = 12
_USERS_FILE = _REPO_ROOT / "data" / "users.json"

_ph = PasswordHasher()


def _load_users() -> list[dict]:
    if not _USERS_FILE.is_file():
        return []
    try:
        with _USERS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    users = data.get("users") if isinstance(data, dict) else []
    return users if isinstance(users, list) else []


def _save_users(users: list[dict]) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _USERS_FILE.open("w", encoding="utf-8") as fh:
        json.dump({"users": users}, fh, indent=2)
        fh.write("\n")


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def main() -> int:
    print("RedFlagged — Create User")
    print("=" * 40)

    # ── Collect inputs ──────────────────────────────────────────────────────────
    email = _prompt("Email").lower()
    if not email or "@" not in email:
        print("ERROR: A valid email address is required.")
        return 1

    display_name = _prompt("Display name")
    if not display_name:
        print("ERROR: Display name is required.")
        return 1

    role = _prompt("Role (admin/analyst/viewer)", default="analyst").lower()
    if role not in _VALID_ROLES:
        print(f"ERROR: Role must be one of {sorted(_VALID_ROLES)}.")
        return 1

    password = getpass.getpass("Password (min 12 chars): ")
    if len(password) < _MIN_PASSWORD_LENGTH:
        print(f"ERROR: Password must be at least {_MIN_PASSWORD_LENGTH} characters.")
        return 1

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("ERROR: Passwords do not match.")
        return 1

    # ── Check for duplicate email ───────────────────────────────────────────────
    users = _load_users()
    if any(u.get("email", "").strip().lower() == email for u in users):
        print(f"ERROR: A user with email {email!r} already exists.")
        return 1

    # ── Hash password + build record ────────────────────────────────────────────
    password_hash = _ph.hash(password)
    # Immediately delete the plaintext reference — belt and braces
    del password
    del confirm

    user_id = f"user-{uuid.uuid4().hex[:8].upper()}"
    new_user = {
        "id": user_id,
        "email": email,
        "display_name": display_name,
        "password_hash": password_hash,
        "role": role,
        "active": True,
    }

    users.append(new_user)
    _save_users(users)

    print(f"\nUser created successfully.")
    print(f"  ID:    {user_id}")
    print(f"  Email: {email}")
    print(f"  Role:  {role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
