"""
Microsoft Defender incidents client.

Uses Microsoft Graph security incidents API:
  GET  https://graph.microsoft.com/v1.0/security/incidents
  PATCH https://graph.microsoft.com/v1.0/security/incidents/{id}

This avoids the WindowsDefenderATP service principal requirement — only
SecurityIncident.Read.All and SecurityIncident.ReadWrite.All (Graph) are needed.
"""

from __future__ import annotations

import requests

import auth.token_manager as token_manager

_GRAPH_BASE = "https://graph.microsoft.com/v1.0/security"

# Display classification strings → Defender API enum values.
# NOTE: the security/incidents endpoint requires lowerCamelCase (e.g. "falsePositive"),
# NOT PascalCase ("FalsePositive") — PascalCase is silently rejected with a 400
# "InvalidRequestBody" that gives no indication which field is wrong. Verified
# directly against the Graph API; don't "fix" this back to PascalCase.
_CLASSIFICATION_MAP = {
    "true positive: malicious":          "truePositive",
    "true positive":                     "truePositive",
    "false positive: not malicious":     "falsePositive",
    "false positive":                    "falsePositive",
    "benign positive":                   "benignPositive",
    "benign":                            "benignPositive",
    "informational, expected activity":  "informationalExpectedActivity",
}

# Display status strings → Defender API enum values (lowerCamelCase — see note above).
_STATUS_MAP = {
    "resolved":   "resolved",
    "active":     "active",
    "redirected": "redirected",
    "inprogress": "inProgress",
    "in progress":"inProgress",
    "unknown":    "unknown",
}


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {token_manager.get_graph_token()}"}


def _map_classification(value: str) -> str:
    vl = (value or "").strip().lower()
    if not vl or vl == "unknown":
        return "unknown"
    if vl in _CLASSIFICATION_MAP:
        return _CLASSIFICATION_MAP[vl]
    # The resolve modal's dropdown sends internal codes like "falsePositive-clean",
    # "truePositive-malware", "informational-securityTesting" — not the display
    # strings above. An unrecognized raw value here (e.g. "falsePositive-clean")
    # was being sent to Graph as-is, which rejects it with an opaque 400 that
    # crashes the whole resolver before it ever reaches the alerts or ServiceNow
    # tickets. Match on substring instead of requiring an exact key.
    if "informational" in vl:
        return "informationalExpectedActivity"
    if "benign" in vl:
        return "benignPositive"
    if "truepositive" in vl or "true positive" in vl:
        return "truePositive"
    if "falsepositive" in vl or "false positive" in vl:
        return "falsePositive"
    return value


def _map_status(value: str) -> str:
    return _STATUS_MAP.get(value.lower().strip(), value)


_DLP_SERVICE_SOURCES = {"MicrosoftComplianceCenter", "microsoftComplianceCenter"}

# Incident title substrings that identify non-DLP/non-Purview incidents to exclude.
_NON_DLP_TITLE_PATTERNS = [
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


def _is_dlp_incident(incident: dict) -> bool:
    """Return True if this incident appears to be a DLP/Purview incident.

    Primary check: title-based exclusion of known non-DLP incident types.
    Secondary check: alert serviceSource when alert data is expanded.
    Default: include (to avoid over-filtering unknown incident types).
    """
    title = (incident.get("displayName") or incident.get("title") or "").lower()
    if any(pat in title for pat in _NON_DLP_TITLE_PATTERNS):
        return False

    alerts = incident.get("alerts") or []
    if alerts:
        return any(
            (a.get("serviceSource") or "") in _DLP_SERVICE_SOURCES
            or "dlp" in (a.get("category") or "").lower()
            for a in alerts
        )

    return True  # no alert data expanded — include by default


def list_incidents(status: str | None = None, top: int = 50, dlp_only: bool = False) -> list[dict]:  # Graph max is 50
    """Return a list of security incidents via Graph.

    dlp_only=True filters to Purview/DLP incidents only.
    """
    qs = f"$top={top}"
    if status:
        qs += f"&$filter=status eq '{_map_status(status)}'"
    url = f"{_GRAPH_BASE}/incidents?{qs}"

    incidents = []
    while url:
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        incidents.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    if dlp_only:
        incidents = [i for i in incidents if _is_dlp_incident(i)]

    return incidents


def get_incident(incident_id: str, expand_alerts: bool = False) -> dict:
    """Return a single incident by ID, optionally with its alerts expanded."""
    qs = "?$expand=alerts" if expand_alerts else ""
    url = f"{_GRAPH_BASE}/incidents/{incident_id}{qs}"
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def assign_incident(incident_id: str, email: str, log_fn=print) -> dict:
    """Assign a Defender incident to a user, then move its status to In Progress
    — matching the ServiceNow side, where assign_incident() also sets state=In
    Progress. Being assigned is what picks an incident up."""
    url = f"{_GRAPH_BASE}/incidents/{incident_id}"
    resp = requests.patch(url, json={"assignedTo": email}, headers=_headers(), timeout=30)
    resp.raise_for_status()
    result = resp.json()
    log_fn(f"Incident {incident_id} assigned to {email} in Defender")
    # Best-effort status update — don't fail the whole assignment if this errors
    try:
        resp2 = requests.patch(url, json={"status": "inProgress"}, headers=_headers(), timeout=30)
        resp2.raise_for_status()
        result = resp2.json()
        log_fn(f"Incident {incident_id} status set to In Progress")
    except Exception as exc:
        log_fn(f"Incident {incident_id} status update failed (non-fatal): {exc}")
    return result


def resolve_incident(
    incident_id: str,
    status: str = "Resolved",
    classification: str = "",
    comment: str = "",
    assigned_to: str = "",
    log_fn=print,
) -> dict:
    """Update a security incident status/classification/assignee via Graph.

    assignedTo must be sent in its own PATCH, separate from status — combining
    them in one request is rejected by this API (see project notes on
    Defender incident assignment quirks). Comments are a distinct sub-resource
    (POST /incidents/{id}/comments) — a "comments" array in the main PATCH
    body is silently accepted (200) but never actually persists. Verified
    directly against the Graph API.

    resolvingComment is a SEPARATE field from comments/timeline — it's what
    the Defender portal's "Manage incident" dialog shows as the required
    "Explain why you resolved this incident" box. It combines fine with
    status/classification in the same PATCH (unlike assignedTo)."""
    url = f"{_GRAPH_BASE}/incidents/{incident_id}"

    payload: dict = {
        "status": _map_status(status),
    }
    if classification:
        payload["classification"] = _map_classification(classification)
    if comment and _map_status(status) == "resolved":
        payload["resolvingComment"] = comment

    log_fn(f"Resolving incident {incident_id}: {payload}")

    resp = requests.patch(url, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    result = resp.json()
    log_fn(f"Incident {incident_id} updated — status: {result.get('status')}")

    if assigned_to:
        resp2 = requests.patch(url, json={"assignedTo": assigned_to}, headers=_headers(), timeout=30)
        try:
            resp2.raise_for_status()
            result = resp2.json()
            log_fn(f"Incident {incident_id} assignedTo set to {assigned_to}")
        except Exception as exc:
            log_fn(f"Incident {incident_id} assignedTo update failed (non-fatal): {exc}")

    if comment:
        try:
            resp3 = requests.post(f"{url}/comments", json={"comment": comment}, headers=_headers(), timeout=30)
            resp3.raise_for_status()
            log_fn(f"Incident {incident_id} comment added")
        except Exception as exc:
            log_fn(f"Incident {incident_id} comment failed (non-fatal): {exc}")

    return result


def resolve_alert(
    alert_id: str,
    status: str = "Resolved",
    classification: str = "",
    comment: str = "",
    log_fn=print,
) -> dict:
    """Update an individual Defender alert's status/classification/comment via Graph.

    Resolving an incident does NOT automatically resolve the alerts under it —
    the Defender portal's "Manage alert" view reads each alert's own status
    separately. Call this for every alert in the incident alongside
    resolve_incident() so the portal shows a consistent resolved state.

    Comments are a distinct sub-resource (POST /alerts_v2/{id}/comments) — a
    "comments" array in the main PATCH body is silently accepted (200) but
    never actually persists. Verified directly against the Graph API."""
    url = f"{_GRAPH_BASE}/alerts_v2/{alert_id}"

    payload: dict = {
        "status": _map_status(status),
    }
    if classification:
        payload["classification"] = _map_classification(classification)

    log_fn(f"Resolving alert {alert_id}: {payload}")

    resp = requests.patch(url, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    result = resp.json()
    log_fn(f"Alert {alert_id} updated — status: {result.get('status')}")

    if comment:
        try:
            resp2 = requests.post(f"{url}/comments", json={"comment": comment}, headers=_headers(), timeout=30)
            resp2.raise_for_status()
            log_fn(f"Alert {alert_id} comment added")
        except Exception as exc:
            log_fn(f"Alert {alert_id} comment failed (non-fatal): {exc}")

    return result


def run_hunting_query(kql: str) -> dict:
    """Run a KQL Advanced Hunting query via Graph API.

    Returns {"ok": True, "results": [...], "schema": [...]}
    or      {"ok": False, "error": "..."}.
    Requires ThreatHunting.Read.All application permission.
    """
    url = "https://graph.microsoft.com/v1.0/security/runHuntingQuery"
    resp = requests.post(url, json={"query": kql}, headers=_headers(), timeout=60)
    if resp.status_code == 403:
        return {"ok": False, "error": "ThreatHunting.Read.All permission not granted.", "status": 403}
    if resp.status_code == 400:
        try:
            err_body = resp.json()
            inner = err_body.get("error") or {}
            msg = inner.get("message") or inner.get("code") or str(err_body)
        except Exception:
            msg = resp.text[:500]
        return {"ok": False, "error": f"KQL error: {msg}", "status": 400}
    resp.raise_for_status()
    data = resp.json()
    return {
        "ok": True,
        "results": data.get("results") or [],
        "schema":  data.get("schema")  or [],
    }
