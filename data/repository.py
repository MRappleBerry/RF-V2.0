"""
Database repository — all read/write operations for RedFlagged.

Also responsible for regenerating the JS dashboard cache files
(js/dbInfoData.js and js/artifactData.js) from DB state so the
existing dashboard frontend continues to work without modification.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

import config
from data.db import get_session
from data.models import Artifact, DlpAlert


# ── DLP Alerts ────────────────────────────────────────────────────────────────

def upsert_alert(alert_dict: dict) -> None:
    """Insert or update a DLP alert row from a purview_client dict."""
    with get_session() as session:
        existing = session.get(DlpAlert, alert_dict["_graph_id"])
        if existing:
            _update_alert_from_dict(existing, alert_dict)
        else:
            row = DlpAlert(id=alert_dict["_graph_id"])
            _update_alert_from_dict(row, alert_dict)
            session.add(row)


def _update_alert_from_dict(row: DlpAlert, d: dict) -> None:
    row.alert_name  = d.get("Alert Name", "")
    row.severity    = d.get("Severity")
    row.status      = d.get("Status")
    row.actor       = d.get("User")
    row.location    = d.get("Location")
    row.incident_id = d.get("Incident ID") or None
    raw_time = d.get("Time Detected", "")
    if raw_time:
        try:
            row.time_detected = datetime.strptime(raw_time, "%b %d, %Y %I:%M %p")
        except ValueError:
            pass
    row.updated_at = datetime.utcnow()


def get_all_alerts(session: Session) -> list[DlpAlert]:
    return session.query(DlpAlert).order_by(DlpAlert.time_detected.desc()).all()


def get_alert_by_id(alert_id: str, session: Session) -> DlpAlert | None:
    return session.get(DlpAlert, alert_id)


# ── Artifacts ─────────────────────────────────────────────────────────────────

def upsert_artifact(
    alert_id: str,
    file_path: Path,
    artifacts_root: Path,
    alert_status: str = "",
) -> None:
    """Register a single artifact file in the database."""
    relative = file_path.relative_to(artifacts_root).as_posix()
    folder_name = file_path.parent.name
    stat = file_path.stat()

    with get_session() as session:
        existing = (
            session.query(Artifact)
            .filter_by(alert_id=alert_id, relative_path=relative)
            .first()
        )
        if existing:
            existing.modified_at = datetime.fromtimestamp(stat.st_mtime)
            existing.size_bytes  = stat.st_size
            existing.deletable   = alert_status.lower() == "resolved"
        else:
            row = Artifact(
                alert_id=alert_id,
                file_name=file_path.name,
                relative_path=relative,
                folder_name=folder_name,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                size_bytes=stat.st_size,
                deletable=alert_status.lower() == "resolved",
            )
            session.add(row)


def delete_artifact(relative_path: str) -> bool:
    """Remove an artifact from the database. Returns True if it existed."""
    with get_session() as session:
        row = session.query(Artifact).filter_by(relative_path=relative_path).first()
        if row:
            session.delete(row)
            return True
    return False


def get_all_artifacts(session: Session) -> list[Artifact]:
    return session.query(Artifact).order_by(Artifact.created_at.desc()).all()


# ── Dashboard cache generation ────────────────────────────────────────────────

def write_dashboard_caches() -> None:
    """
    Regenerate js/dbInfoData.js and js/artifactData.js from the database.
    The existing dashboard JS reads these files at page load — format is unchanged.
    """
    with get_session() as session:
        alerts    = get_all_alerts(session)
        artifacts = get_all_artifacts(session)

    _write_db_info_cache(alerts)
    _write_artifact_cache(alerts, artifacts)


def _write_db_info_txt(records: list[dict], headers: list[str]) -> None:
    """Write automation/db_info.txt — the file the dashboard JS fetches directly."""
    db_info_path = config.WORKSPACE_ROOT / "automation" / "db_info.txt"
    payload = {"headers": headers, "purview": records}
    db_info_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_db_info_cache(alerts: list[DlpAlert]) -> None:
    headers = [
        "Alert Name", "Severity", "Status", "Time Detected",
        "User", "Location", "Incident ID",
    ]
    records = []
    for a in alerts:
        records.append({
            "Alert Name":    a.alert_name,
            "Severity":      a.severity or "",
            "Status":        a.status or "",
            "Time Detected": a.time_detected.strftime("%b %-d, %Y %-I:%M %p") if a.time_detected else "",
            "User":          a.actor or "",
            "Location":      a.location or "",
            "Incident ID":   a.incident_id or "",
            "Odin Summary":  a.odin_summary or "",
            "Odin Confidence":       a.odin_confidence or "",
            "Odin Confidence Label": a.odin_confidence_label or "",
            "Odin What Happened":    a.odin_what_happened or "",
            "Odin Why It Matters":   a.odin_why_it_matters or "",
            "Odin Recommended Next Actions": a.odin_next_actions or "",
            "Odin Raw Response":     a.odin_raw_response or "",
        })

    payload = {"headers": headers, "purview": records}
    js = f"window.__REDFLAGGED_DB_INFO__ = {json.dumps(payload, indent=2)};\n"
    config.DB_INFO_CACHE_PATH.write_text(js, encoding="utf-8")
    _write_db_info_txt(records, headers)


def _write_artifact_cache(alerts: list[DlpAlert], artifacts: list[Artifact]) -> None:
    # Build a quick lookup: alert_id → alert
    alert_map = {a.id: a for a in alerts}

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    files = []
    for art in artifacts:
        parent_alert = alert_map.get(art.alert_id)
        files.append({
            "name":          art.file_name or "",
            "relative_path": art.relative_path or "",
            "folder_name":   art.folder_name or "",
            "folder_path":   art.folder_name or "",
            "modified_iso":  art.modified_at.isoformat() if art.modified_at else "",
            "size":          art.size_bytes or 0,
            "alert_name":    parent_alert.alert_name if parent_alert else "",
            "alert_status":  parent_alert.status if parent_alert else "",
            "incident_id":   parent_alert.incident_id or "" if parent_alert else "",
            "ticket":        "",
            "deletable":     art.deletable,
        })

    payload = {"updated_at": now, "files": files}
    # Also write to automation/artifacts_inventory.json for compatibility
    config.INVENTORY_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    js = f"window.__REDFLAGGED_ARTIFACTS__ = {json.dumps(payload, indent=2)};\n"
    config.ARTIFACT_CACHE_PATH.write_text(js, encoding="utf-8")


def build_report_payload() -> dict:
    """
    Build the /report-data response payload from the most recent alert in the DB.
    Matches the format expected by the dashboard's loadCaseDetails() function.
    """
    with get_session() as session:
        alert = session.query(DlpAlert).order_by(DlpAlert.time_detected.desc()).first()

    if not alert:
        return {"ok": False}

    odin_lines = []
    if alert.odin_summary:
        odin_lines = [l.strip() for l in alert.odin_summary.splitlines() if l.strip()]

    location = alert.location or ""
    source = f"Microsoft Purview - {location}" if location else "Microsoft Purview"

    return {
        "ok":               True,
        "incident_id":      alert.incident_id or "",
        "service_now_ticket": "",
        "service_now_link":   "",
        "status":           alert.status or "In Progress",
        "assignee":         "Security Managed Services",
        "source":           source,
        "odin_summary":     odin_lines,
    }
