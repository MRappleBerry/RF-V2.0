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


def get_fields_by_numbers(snow_numbers: list[str], fields: list[str] | None = None) -> dict[str, dict[str, str]]:
    """
    Batch-fetch current field values for a list of ticket numbers in a single
    call. Returns {number: {field: display_value, ...}}, omitting numbers that
    weren't found. Empty input returns {}. Defaults to state + description.
    """
    numbers = [n for n in dict.fromkeys(snow_numbers) if n]  # dedupe, drop blanks
    if not numbers:
        return {}
    fields = fields or ["state", "description"]
    url = f"{_BASE}/api/now/table/incident"
    out: dict[str, dict[str, str]] = {}
    # ServiceNow caps query length in practice — chunk to be safe on large reports.
    chunk_size = 100
    for i in range(0, len(numbers), chunk_size):
        chunk = numbers[i:i + chunk_size]
        params = {
            "sysparm_query": "numberIN" + ",".join(chunk),
            "sysparm_fields": "number," + ",".join(fields),
            "sysparm_limit": len(chunk),
            "sysparm_display_value": "true",
        }
        resp = requests.get(url, params=params, auth=_AUTH, timeout=30)
        resp.raise_for_status()
        for row in resp.json().get("result", []):
            out[row.get("number", "")] = {f: row.get(f, "") for f in fields}
    return out


def get_states_by_numbers(snow_numbers: list[str]) -> dict[str, str]:
    """Batch-fetch just current state (display value, e.g. "Resolved")."""
    return {num: row.get("state", "") for num, row in get_fields_by_numbers(snow_numbers, ["state"]).items()}


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
                    impact: int = 2, assignment_group_sys_id: str = "", caller_sys_id: str = "") -> dict:
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
    if caller_sys_id:
        body["caller_id"] = caller_sys_id
    resp = requests.post(url, json=body, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})


def link_incidents(parent_sys_id: str, child_sys_id: str) -> None:
    """Set parent_incident on the child incident record to establish the relationship."""
    url = f"{_BASE}/api/now/table/incident/{child_sys_id}"
    requests.patch(url, json={"parent_incident": parent_sys_id}, auth=_AUTH, timeout=15)


# incident.state: In Progress
_STATE_IN_PROGRESS = "2"


def assign_incident(incident_sys_id: str, user_sys_id: str, group_sys_id: str = "") -> dict:
    """Set assigned_to (and assignment_group) on a ServiceNow incident, and move
    its state to In Progress — being assigned is what picks a ticket up."""
    url = f"{_BASE}/api/now/table/incident/{incident_sys_id}"
    body: dict = {"assigned_to": user_sys_id, "state": _STATE_IN_PROGRESS}
    if group_sys_id:
        body["assignment_group"] = group_sys_id
    resp = requests.patch(url, json=body, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})


def find_user_by_email(email: str) -> dict:
    """Return a ServiceNow user record by email. Returns {} if not found."""
    url = f"{_BASE}/api/now/table/sys_user"
    params = {
        "sysparm_query": f"email={email}",
        "sysparm_fields": "sys_id,name,email",
        "sysparm_limit": 1,
    }
    resp = requests.get(url, params=params, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("result", [])
    return results[0] if results else {}


# incident.state: Resolved
_STATE_RESOLVED = "6"


def resolve_incident(incident_sys_id: str, caller_sys_id: str = "", resolution_code: str = "",
                      resolution_notes: str = "") -> dict:
    """Resolve a ServiceNow incident: state=Resolved, caller, closed_by, close_code, close_notes.

    closed_by and resolved_by ("Resolved by" on the ticket — this instance has
    both fields) are separate from caller_id ("Caller") — left unset, ServiceNow
    shows whichever account the API Basic Auth credentials belong to (e.g.
    "System Administrator"), not the analyst who actually resolved it. We set
    all three to the resolving identity."""
    url = f"{_BASE}/api/now/table/incident/{incident_sys_id}"
    body: dict = {"state": _STATE_RESOLVED}
    if caller_sys_id:
        body["caller_id"] = caller_sys_id
        body["closed_by"] = caller_sys_id
        body["resolved_by"] = caller_sys_id
    if resolution_code:
        body["close_code"] = resolution_code
    if resolution_notes:
        body["close_notes"] = resolution_notes
    resp = requests.patch(url, json=body, auth=_AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", {})
