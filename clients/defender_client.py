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

# Display classification strings → Defender API enum values
_CLASSIFICATION_MAP = {
    "true positive: malicious":          "TruePositive",
    "true positive":                     "TruePositive",
    "false positive: not malicious":     "FalsePositive",
    "false positive":                    "FalsePositive",
    "benign positive":                   "BenignPositive",
    "benign":                            "BenignPositive",
    "informational, expected activity":  "InformationalExpectedActivity",
}

# Display status strings → Defender API enum values
_STATUS_MAP = {
    "resolved":   "Resolved",
    "active":     "Active",
    "redirected": "Redirected",
    "inprogress": "InProgress",
    "in progress":"InProgress",
    "unknown":    "Unknown",
}


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {token_manager.get_graph_token()}"}


def _map_classification(value: str) -> str:
    return _CLASSIFICATION_MAP.get(value.lower().strip(), value)


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
    """Assign a Defender incident to a user, then set status to Active."""
    url = f"{_GRAPH_BASE}/incidents/{incident_id}"
    resp = requests.patch(url, json={"assignedTo": email}, headers=_headers(), timeout=30)
    resp.raise_for_status()
    result = resp.json()
    log_fn(f"Incident {incident_id} assigned to {email} in Defender")
    # Best-effort status update — don't fail the whole assignment if this errors
    try:
        resp2 = requests.patch(url, json={"status": "Active"}, headers=_headers(), timeout=30)
        resp2.raise_for_status()
        log_fn(f"Incident {incident_id} status set to Active")
    except Exception as exc:
        log_fn(f"Incident {incident_id} status update failed (non-fatal): {exc}")
    return result


def resolve_incident(
    incident_id: str,
    status: str = "Resolved",
    classification: str = "",
    comment: str = "",
    log_fn=print,
) -> dict:
    """Update a security incident status/classification via Graph."""
    url = f"{_GRAPH_BASE}/incidents/{incident_id}"

    payload: dict = {
        "status": _map_status(status),
    }
    if classification:
        payload["classification"] = _map_classification(classification)
    if comment:
        payload["comments"] = [{"comment": comment}]

    log_fn(f"Resolving incident {incident_id}: {payload}")

    resp = requests.patch(url, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()

    result = resp.json()
    log_fn(f"Incident {incident_id} updated — status: {result.get('status')}")
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
