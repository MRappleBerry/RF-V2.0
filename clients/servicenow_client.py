"""
ServiceNow Table API client.

Reads incident assignment groups and group members for assignment validation.
Authentication: Basic Auth (username/password from config).
No browser required.
"""
from __future__ import annotations

import requests
import config

_BASE: str = ""
_AUTH: tuple[str, str] = ("", "")


def init() -> None:
    """Initialise from config. Call once after config.load()."""
    global _BASE, _AUTH
    url = config.SNOW_INSTANCE_URL.rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    _BASE = url
    _AUTH = (config.SNOW_USERNAME, config.SNOW_PASSWORD)


def get_incident_by_number(snow_number: str) -> dict:
    """
    Return a ServiceNow incident record by ticket number (e.g. INC0012345).
    Returns {} if not found.
    """
    url = f"{_BASE}/api/now/table/incident"
    params = {
        "sysparm_query": f"number={snow_number}",
        "sysparm_fields": "sys_id,number,assignment_group,assigned_to,state,short_description",
        "sysparm_limit": 1,
        "sysparm_display_value": "all",   # returns both value (sys_id) and display_value (name)
    }
    resp = requests.get(url, params=params, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[0] if results else {}


def get_group_members(group_sys_id: str) -> list[dict]:
    """
    Return active members of a ServiceNow assignment group.
    Each member dict has keys: user.email, user.name, user.sys_id, user.user_name
    """
    url = f"{_BASE}/api/now/table/sys_user_grmember"
    params = {
        "sysparm_query": f"group={group_sys_id}^user.active=true",
        "sysparm_fields": "user.email,user.name,user.sys_id,user.user_name",
        "sysparm_limit": 1000,
    }
    resp = requests.get(url, params=params, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def find_group_by_name(name: str) -> list[dict]:
    """Search for active ServiceNow groups whose name contains the given string.

    Returns up to 10 matching records, each with sys_id, name, and description.
    """
    url = f"{_BASE}/api/now/table/sys_user_group"
    params = {
        "sysparm_query": f"nameLIKE{name}^active=true",
        "sysparm_fields": "sys_id,name,description",
        "sysparm_limit": 10,
    }
    resp = requests.get(url, params=params, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def create_incident(short_description: str, description: str = "", urgency: int = 2,
                    impact: int = 2, assignment_group_sys_id: str = "") -> dict:
    """Create a ServiceNow incident. Returns the full result dict (includes number, sys_id)."""
    url = f"{_BASE}/api/now/table/incident"
    body: dict = {
        "short_description": short_description,
        "description": description,
        "urgency": str(urgency),
        "impact": str(impact),
        "category": "Security",
        "subcategory": "DLP",
    }
    if assignment_group_sys_id:
        body["assignment_group"] = assignment_group_sys_id
    resp = requests.post(url, json=body, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})


def link_incidents(parent_sys_id: str, child_sys_id: str) -> None:
    """Set parent_incident on the child incident record to establish the relationship."""
    url = f"{_BASE}/api/now/table/incident/{child_sys_id}"
    requests.patch(url, json={"parent_incident": parent_sys_id}, auth=_AUTH, timeout=15)


def assign_incident(incident_sys_id: str, user_sys_id: str, group_sys_id: str = "") -> dict:
    """Set assigned_to (and assignment_group) on a ServiceNow incident."""
    url = f"{_BASE}/api/now/table/incident/{incident_sys_id}"
    body: dict = {"assigned_to": user_sys_id}
    if group_sys_id:
        body["assignment_group"] = group_sys_id
    resp = requests.patch(url, json=body, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})
