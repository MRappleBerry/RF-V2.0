"""
Assignment eligibility validator.

Checks all conditions before allowing a user to self-assign an incident:
  1. Incident is unassigned in ServiceNow
  2. Incident has a ServiceNow Assignment Group
  3. Current user's email is an active member of that group
  4. Current user's email can be resolved in Microsoft Defender XDR (Graph)

Usage:
    result = validate(snow_number="INC0012345", current_user_email="john@company.com")
    if result["eligible"]:
        perform_assignment(result)
"""
from __future__ import annotations

import requests

import auth.token_manager as token_manager
from clients import servicenow_client


def validate(snow_number: str, current_user_email: str, *, allow_reassign: bool = False, fallback_group_name: str = "", require_defender_check: bool = True) -> dict:
    """
    Full eligibility check.

    Returns on success:
        {"eligible": True, "email": ..., "assignment_group": ...,
         "assignment_group_sys_id": ..., "snow_incident_sys_id": ...,
         "snow_user_sys_id": ..., "servicenow_user_found": True,
         "assignment_group_member": True, "defender_user_found": True}

    Returns on failure:
        {"eligible": False, "reason": "REASON_CODE", "email": ...,
         "assignment_group": ..., [optional "detail": ...]}
    """
    email = current_user_email.strip().lower()
    if not email:
        return _deny("NO_USER_EMAIL", email, "")

    # ── Step 1: Get incident from ServiceNow ──────────────────────────────────
    try:
        incident = servicenow_client.get_incident_by_number(snow_number)
    except Exception as exc:
        return _deny("SERVICENOW_UNREACHABLE", email, "", detail=str(exc))

    if not incident:
        return _deny("INCIDENT_NOT_FOUND", email, "")

    # ── Step 2: Check if already assigned ────────────────────────────────────
    assigned_raw = incident.get("assigned_to") or {}
    if isinstance(assigned_raw, dict):
        assigned_value = (assigned_raw.get("value") or "").strip()
    else:
        assigned_value = str(assigned_raw).strip()

    if assigned_value and not allow_reassign:
        return _deny("INCIDENT_ALREADY_ASSIGNED", email, "")

    # ── Step 3: Get assignment group ──────────────────────────────────────────
    group_raw = incident.get("assignment_group") or {}
    if isinstance(group_raw, dict):
        group_sys_id = (group_raw.get("value") or "").strip()
        group_name   = (group_raw.get("display_value") or "").strip()
    else:
        group_sys_id = str(group_raw).strip()
        group_name   = group_sys_id

    if not group_sys_id:
        if fallback_group_name:
            # Incident has no group — look up the configured default group by name
            try:
                groups = servicenow_client.find_group_by_name(fallback_group_name)
                if not groups:
                    return _deny("NO_ASSIGNMENT_GROUP", email, fallback_group_name,
                                 detail=f"Group '{fallback_group_name}' not found in ServiceNow.")
                group_sys_id = groups[0].get("sys_id", "").strip()
                group_name   = groups[0].get("name", fallback_group_name).strip()
            except Exception as exc:
                return _deny("SERVICENOW_UNREACHABLE", email, fallback_group_name, detail=str(exc))
        else:
            return _deny("NO_ASSIGNMENT_GROUP", email, "")

    # ── Step 4: Get group members and match current user ──────────────────────
    try:
        members = servicenow_client.get_group_members(group_sys_id)
    except Exception as exc:
        return _deny("SERVICENOW_UNREACHABLE", email, group_name, detail=str(exc))

    member_map: dict[str, str] = {}   # normalised email → user sys_id
    for m in members:
        member_email   = (m.get("user.email") or "").strip().lower()
        member_sys_id  = (m.get("user.sys_id") or "").strip()
        if member_email and member_sys_id:
            member_map[member_email] = member_sys_id

    if email not in member_map:
        return {
            "eligible": False,
            "reason": "USER_NOT_MEMBER_OF_ASSIGNMENT_GROUP",
            "email": email,
            "assignment_group": group_name,
        }

    user_sys_id = member_map[email]

    # ── Step 5: Validate user exists in Defender XDR (Graph) ─────────────────
    if require_defender_check:
        defender_found = _validate_in_defender(email)
        if not defender_found:
            sys_id_raw2 = incident.get("sys_id") or {}
            incident_sys_id2 = (sys_id_raw2.get("value") or str(sys_id_raw2)).strip() if isinstance(sys_id_raw2, dict) else str(sys_id_raw2).strip()
            return {
                "eligible": False,
                "reason": "USER_NOT_FOUND_IN_DEFENDER",
                "email": email,
                "assignment_group": group_name,
                "assignment_group_sys_id": group_sys_id,
                "snow_incident_sys_id": incident_sys_id2,
                "snow_user_sys_id": user_sys_id,
                "servicenow_user_found": True,
                "assignment_group_member": True,
                "defender_user_found": False,
            }
    else:
        defender_found = True

    # ── Resolve the incident sys_id (value field from display_value=all) ──────
    sys_id_raw = incident.get("sys_id") or {}
    if isinstance(sys_id_raw, dict):
        incident_sys_id = (sys_id_raw.get("value") or "").strip()
    else:
        incident_sys_id = str(sys_id_raw).strip()

    return {
        "eligible": True,
        "email": email,
        "assignment_group": group_name,
        "assignment_group_sys_id": group_sys_id,
        "snow_incident_sys_id": incident_sys_id,
        "snow_user_sys_id": user_sys_id,
        "servicenow_user_found": True,
        "assignment_group_member": True,
        "defender_user_found": True,
    }


def perform_assignment(result: dict) -> dict:
    """
    Execute the ServiceNow assignment after validation passes.
    Sets both assigned_to and assignment_group on the incident.
    Defender XDR assignment is handled separately via defender_client.
    """
    try:
        servicenow_client.assign_incident(
            result["snow_incident_sys_id"],
            result["snow_user_sys_id"],
            group_sys_id=result.get("assignment_group_sys_id", ""),
        )
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _validate_in_defender(email: str) -> bool:
    """Check if a user with the given email exists in the Microsoft tenant (Graph)."""
    try:
        token = token_manager.get_graph_token()
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/users",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "$filter": f"mail eq '{email}' or userPrincipalName eq '{email}'",
                "$top": 1,
                "$select": "id,mail,userPrincipalName",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return len(resp.json().get("value", [])) > 0
    except Exception:
        return False


def _deny(reason: str, email: str, group: str, *, detail: str = "") -> dict:
    out: dict = {
        "eligible": False,
        "reason": reason,
        "email": email,
        "assignment_group": group,
    }
    if detail:
        out["detail"] = detail
    return out
