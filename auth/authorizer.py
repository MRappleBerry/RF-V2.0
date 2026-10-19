"""
Role-based authorization for RedFlagged.

Roles:
  analyst   — view and resolve incidents only; no System section access
  admin     — analyst + assignment, system config, user management, AI/ODIN
  developer — admin + developer config (technical settings)
"""
from __future__ import annotations

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "developer": frozenset({"view", "assign", "reassign", "resolve", "configure", "developer_config"}),
    "admin":     frozenset({"view", "assign", "reassign", "resolve", "configure"}),
    "analyst":   frozenset({"view", "resolve"}),
}


def has_permission(role: str, permission: str) -> bool:
    """Return True if the given role grants the requested permission."""
    allowed = _ROLE_PERMISSIONS.get(role, frozenset())
    return permission in allowed


def require_permission(role: str, permission: str) -> None:
    """
    Assert that the role grants the requested permission.
    Raises PermissionError with a descriptive message if it does not.
    """
    if not has_permission(role, permission):
        raise PermissionError(
            f"Role {role!r} does not have the {permission!r} permission. "
            f"Required role: one of {[r for r, perms in _ROLE_PERMISSIONS.items() if permission in perms]!r}."
        )
