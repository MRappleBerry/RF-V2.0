"""
Microsoft Purview DLP alerts client.

Uses the Microsoft Graph beta endpoint for DLP-specific data:
  GET /beta/security/cases/dlpAlerts
  GET /beta/security/cases/dlpAlerts/{id}?$expand=evidence

Replaces the Selenium-based Purview HTML scraping in function/tools.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

import auth.token_manager as token_manager

_GRAPH_BASE = "https://graph.microsoft.com/beta"

# Purview status → db_info.txt Status field
_STATUS_MAP = {
    "newAlert":       "Active",
    "inProgress":     "Investigating",
    "resolved":       "Resolved",
    "dismissed":      "Dismissed",
    "alertNew":       "Active",
    "alertInProgress":"Investigating",
    "alertResolved":  "Resolved",
}

# Purview severity → capitalised label
_SEVERITY_MAP = {
    "low":      "Low",
    "medium":   "Medium",
    "high":     "High",
    "unknown":  "Unknown",
    "informational": "Informational",
}


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {token_manager.get_graph_token()}"}


def _get(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _format_time(iso: str | None) -> str:
    """Convert ISO-8601 datetime to the display format used by the dashboard."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %-d, %Y %-I:%M %p")
    except Exception:
        return iso


def list_dlp_alerts(top: int = 100) -> list[dict]:
    """
    Return a list of DLP alert dicts in the db_info.txt schema format.

    Tries the Purview-specific beta endpoint first; falls back to the
    stable alerts_v2 endpoint filtered to Purview/DLP sources if the
    tenant doesn't have the Purview DLP license.
    """
    try:
        return _list_dlp_alerts_beta(top)
    except Exception:
        return _list_alerts_v2(top)


def _list_dlp_alerts_beta(top: int) -> list[dict]:
    url = f"{_GRAPH_BASE}/security/cases/dlpAlerts?$top={top}"
    alerts = []

    while url:
        data = _get(url)
        for item in data.get("value", []):
            alerts.append(_map_alert(item))
        url = data.get("@odata.nextLink")
    return alerts


_NON_DLP_ALERT_TITLE_PATTERNS = [
    "allow/block list",
    "allowblock list",
    "safe link",
    "safe attachment",
    "anti-phish",
    "anti-spam",
    "spoof intelligence",
    "quarantine",
    "mail flow rule",
    "transport rule",
]


def _is_dlp_alert_v2(item: dict) -> bool:
    """Return True if this alerts_v2 item appears to be a DLP/Purview alert."""
    title = (item.get("title") or item.get("displayName") or "").lower()
    return not any(pat in title for pat in _NON_DLP_ALERT_TITLE_PATTERNS)


def _list_alerts_v2(top: int) -> list[dict]:
    """Fallback: unified alerts_v2 endpoint filtered to exclude non-DLP alerts."""
    _GRAPH_V1 = "https://graph.microsoft.com/v1.0"
    url = f"{_GRAPH_V1}/security/alerts_v2?$top={top}"
    alerts = []

    while url:
        data = _get(url)
        for item in data.get("value", []):
            if _is_dlp_alert_v2(item):
                alerts.append(_map_alert_v2(item))
        url = data.get("@odata.nextLink")
    return alerts


def _map_alert_v2(item: dict) -> dict:
    """Map a unified alerts_v2 object to the db_info.txt row schema."""
    actor = ""
    for entity in item.get("evidence", []) or []:
        if entity.get("@odata.type", "").endswith("userEvidence"):
            actor = entity.get("userAccount", {}).get("userPrincipalName", "") or actor

    raw_severity = (item.get("severity") or "").lower()
    raw_status   = (item.get("status") or "").lower()

    return {
        "Alert Name":    item.get("title") or item.get("displayName") or "",
        "Severity":      _SEVERITY_MAP.get(raw_severity, raw_severity.capitalize()),
        "Status":        _STATUS_MAP.get(raw_status, raw_status.capitalize()),
        "Time Detected":   _format_time(item.get("createdDateTime")),
        "createdDateTime": item.get("createdDateTime") or "",
        "User":            actor,
        "Location":        item.get("serviceSource") or "",
        "Incident ID":     str(item.get("incidentId") or ""),
        "_graph_id":       item.get("id", ""),
    }


def get_alert_evidence(alert_id: str) -> dict:
    """
    Return the full alert detail including expanded evidence.
    Tries the Purview beta endpoint first; falls back to alerts_v2 if the tenant
    doesn't have the Purview DLP license (or the alert came from alerts_v2).
    """
    try:
        url = f"{_GRAPH_BASE}/security/cases/dlpAlerts/{alert_id}?$expand=evidence"
        return _get(url)
    except Exception:
        url = f"https://graph.microsoft.com/v1.0/security/alerts_v2/{alert_id}"
        return _get(url)


def _map_alert(item: dict) -> dict:
    """Map a Graph DLP alert object to the db_info.txt row schema."""
    actor = (
        item.get("actorDisplayName")
        or item.get("userPrincipalName")
        or ""
    )
    location = _extract_location(item)
    incident_ids = item.get("relatedIncidentIds") or []

    return {
        "Alert Name":   item.get("alertDefinitionDisplayName") or item.get("title") or "",
        "Severity":     _SEVERITY_MAP.get(
                            (item.get("severity") or "").lower(), item.get("severity") or ""
                        ),
        "Status":       _STATUS_MAP.get(
                            item.get("status") or "", item.get("status") or ""
                        ),
        "Time Detected":   _format_time(item.get("createdDateTime")),
        "createdDateTime": item.get("createdDateTime") or "",
        "User":            actor,
        "Location":        location,
        "Incident ID":     str(incident_ids[0]) if incident_ids else "",
        "_graph_id":       item.get("id", ""),
    }


def _extract_location(item: dict) -> str:
    """Best-effort extraction of the workload/location from an alert."""
    service = item.get("service") or ""
    if "OneDrive" in service or "onedrive" in service.lower():
        return "OneDrive"
    if "SharePoint" in service or "sharepoint" in service.lower():
        return "SharePoint"
    if "Exchange" in service or "exchange" in service.lower():
        return "Exchange"
    if "Teams" in service or "teams" in service.lower():
        return "Teams"
    return service or "Unknown"


def extract_classifiers(evidence: dict) -> list[dict]:
    """
    Extract SIT classifier rows from alert evidence.

    Returns a list of dicts with keys:
      SIT Name, Matched Content, Location, Surrounding Characters
    These are used by artifact_builder to generate classifiers.html.
    """
    rows = []
    evidence_list = evidence.get("evidence") or []

    for ev in evidence_list:
        sit_groups = ev.get("classificationGroups") or ev.get("sensitiveInformationTypes") or []
        for group in sit_groups:
            sit_name = group.get("sensitiveTypeDisplayName") or group.get("sensitiveTypeName") or ""
            matches = group.get("classificationAttributes") or group.get("matches") or [{}]
            for match in matches:
                rows.append({
                    "SIT Name":              sit_name,
                    "Matched Content":       match.get("displayValue") or match.get("value") or "",
                    "Location":              ev.get("workload") or ev.get("location") or "",
                    "Surrounding Characters": match.get("surroundingContent") or "",
                })

    return rows
