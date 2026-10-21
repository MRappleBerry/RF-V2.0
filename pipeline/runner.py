"""
RedFlagged pipeline runner — replaces function/tools.py.

Fetches DLP alerts from Microsoft Graph, builds artifact folders,
and updates the Azure SQL database and dashboard cache files.
No browser required.

Usage (standalone):
    python pipeline/runner.py

Usage (from dashboard server):
    Launched as a subprocess; stdout is streamed to SSE subscribers.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is on the path when run as a subprocess (not needed —
# and not meaningful — in a PyInstaller-frozen build, where imports are
# already bundled statically).
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import auth.token_manager as token_manager
import data.db as db
import data.repository as repository
from clients import purview_client, defender_client
from pipeline import artifact_builder


def _map_incident_to_alert(incident: dict) -> dict:
    """Map a Defender incident to the db_info.txt alert schema."""
    return {
        "Alert Name":    incident.get("displayName") or incident.get("incidentName") or "",
        "Severity":      (incident.get("severity") or "").capitalize(),
        "Status":        (incident.get("status") or "").capitalize(),
        "Time Detected": incident.get("createdTime") or incident.get("createdDateTime") or "",
        "User":          "",
        "Location":      "Microsoft Defender",
        "Incident ID":   str(incident.get("incidentId") or incident.get("id") or ""),
        "_graph_id":     str(incident.get("incidentId") or incident.get("id") or ""),
    }


def run_pipeline(log_fn=print) -> None:
    # ── 1. Load secrets ───────────────────────────────────────────────────────
    log_fn("[RedFlagged] Loading configuration...")
    config.load()

    # ── 2. Init database ──────────────────────────────────────────────────────
    log_fn("[RedFlagged] Connecting to database...")
    db.init(config.SQL_CONNECTION_STRING)

    # ── 3. Authenticate ───────────────────────────────────────────────────────
    log_fn("[RedFlagged] Authenticating to Microsoft Graph...")
    token_manager.init(config.TENANT_ID, config.CLIENT_ID, config.CLIENT_SECRET)
    token_manager.get_graph_token()
    log_fn("[RedFlagged] Authentication successful.")

    # ── 4. Fetch DLP alerts ───────────────────────────────────────────────────
    log_fn("[RedFlagged] Fetching DLP alerts from Microsoft Purview...")
    try:
        alerts = purview_client.list_dlp_alerts()
        log_fn(f"[RedFlagged] Found {len(alerts)} alert(s).")
    except Exception as exc:
        log_fn(f"[RedFlagged] Purview alerts unavailable: {exc}")
        alerts = []

    # ── 4b. Fetch Defender incidents as fallback ──────────────────────────────
    if not alerts:
        log_fn("[RedFlagged] Trying Defender incidents...")
        try:
            incidents = defender_client.list_incidents(top=50)
            alerts = [_map_incident_to_alert(i) for i in incidents]
            log_fn(f"[RedFlagged] Found {len(alerts)} Defender incident(s).")
        except Exception as exc:
            log_fn(f"[RedFlagged] Defender incidents unavailable: {exc}")

    if not alerts:
        log_fn("[RedFlagged] No data available from either source. Pipeline complete.")
        return

    # ── 5. Build artifacts for each alert ────────────────────────────────────
    log_fn("[RedFlagged] Building artifacts...")
    for i, alert in enumerate(alerts, 1):
        graph_id = alert.get("_graph_id", "")
        log_fn(f"[RedFlagged] [{i}/{len(alerts)}] {alert['Alert Name'][:80]}")
        try:
            evidence = purview_client.get_alert_evidence(graph_id) if graph_id else {}
            artifact_builder.build(alert, evidence, log_fn=log_fn)
        except Exception as exc:
            log_fn(f"[RedFlagged]   ERROR: {exc}")

    # ── 6. Refresh dashboard caches ───────────────────────────────────────────
    log_fn("[RedFlagged] Refreshing dashboard cache files...")
    repository.write_dashboard_caches()

    log_fn("[RedFlagged] Pipeline complete.")


if __name__ == "__main__":
    run_pipeline()
