"""
Incident resolver — replaces function/incident_resolved.py.

Resolves a Microsoft Defender incident via API — no browser required — and,
when ServiceNow ticket sys_ids are given, resolves those too (state=Resolved,
caller, resolution code/notes). No browser required.

Usage:
    python pipeline/incident_resolver.py \\
        --incident-id 282359 \\
        --status Resolved \\
        --classification "False Positive: Not malicious" \\
        --comment "Reviewed and confirmed benign." \\
        --assigned-to analyst@company.com \\
        --snow-tickets sys_id_1,sys_id_2 \\
        --resolution-code "Solved (Permanently)" \\
        --caller-email analyst@company.com
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import auth.token_manager as token_manager
from clients import defender_client
from clients import servicenow_client

# UI resolution-code labels -> this ServiceNow instance's configured close_code choices
_RESOLUTION_CODE_MAP = {
    "Solved (Permanently)":              "Solution provided",
    "Solved (Work Around)":              "Workaround provided",
    "Not Solved (Not Reproducible)":     "No resolution provided",
    "Closed/Resolved by Caller":         "Resolved by caller",
    "User Error":                        "User error",
}


def _load_credentials() -> None:
    """Populate token_manager / servicenow_client from config.json (same source
    the dashboard server uses) — not config.load(), which only reads .env."""
    cfg_path = _REPO_ROOT / "config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    env = raw.get("envs", {}).get(raw.get("active_env", "tenant"), raw)

    tenant_id = env.get("TENANT_ID", "")
    client_id = env.get("CLIENT_ID", "")
    client_secret = env.get("CLIENT_SECRET", "")
    if tenant_id and client_id and client_secret:
        token_manager.init(tenant_id, client_id, client_secret)

    import config as _config
    _config.SNOW_INSTANCE_URL = env.get("SNOW_INSTANCE") or env.get("SNOW_URL", "")
    _config.SNOW_USERNAME = env.get("SNOW_USER", "")
    _config.SNOW_PASSWORD = env.get("SNOW_PASSWORD", "")
    servicenow_client.init()


def resolve(
    incident_id: str,
    status: str = "Resolved",
    classification: str = "",
    comment: str = "",
    assigned_to: str = "",
    snow_tickets: list[str] | None = None,
    resolution_code: str = "",
    caller_email: str = "",
    log_fn=print,
) -> dict:
    _load_credentials()

    log_fn(f"[IncidentResolver] Resolving incident {incident_id}...")
    result = defender_client.resolve_incident(
        incident_id=incident_id,
        status=status,
        classification=classification,
        comment=comment,
        assigned_to=assigned_to,
        log_fn=log_fn,
    )
    log_fn(f"[IncidentResolver] Done. Defender status: {result.get('status', 'unknown')}")

    # Resolving the incident does NOT cascade to its alerts — the Defender
    # portal's own "Manage alert" view reads each alert's own status/comment,
    # so resolve every alert under this incident too.
    try:
        inc_detail = defender_client.get_incident(incident_id, expand_alerts=True)
        for alert in (inc_detail.get("alerts") or []):
            alert_id = alert.get("id")
            if not alert_id:
                continue
            try:
                defender_client.resolve_alert(
                    alert_id=alert_id,
                    status=status,
                    classification=classification,
                    comment=comment,
                    log_fn=log_fn,
                )
            except Exception as exc:
                log_fn(f"[IncidentResolver] WARNING: failed to resolve alert {alert_id}: {exc}")
    except Exception as exc:
        log_fn(f"[IncidentResolver] WARNING: failed to fetch incident alerts for alert-level resolve: {exc}")

    snow_results = []
    if snow_tickets and status.lower() == "resolved":
        caller_sys_id = ""
        if caller_email:
            caller = servicenow_client.find_user_by_email(caller_email)
            caller_sys_id = caller.get("sys_id", "")
            if not caller_sys_id:
                log_fn(f"[IncidentResolver] WARNING: no ServiceNow user found for {caller_email} — caller left blank.")
        mapped_code = _RESOLUTION_CODE_MAP.get(resolution_code, resolution_code)
        for sys_id in snow_tickets:
            sys_id = sys_id.strip()
            if not sys_id:
                continue
            # One retry on transient failures (network blip, momentary 5xx) —
            # observed in practice: an identical call succeeded immediately
            # on retry after a first attempt silently left the ticket untouched.
            last_exc = None
            for attempt in range(2):
                try:
                    if attempt:
                        time.sleep(2)
                        log_fn(f"[IncidentResolver] Retrying ServiceNow ticket {sys_id}...")
                    snow_result = servicenow_client.resolve_incident(
                        incident_sys_id=sys_id,
                        caller_sys_id=caller_sys_id,
                        resolution_code=mapped_code,
                        resolution_notes=comment,
                    )
                    log_fn(f"[IncidentResolver] ServiceNow ticket {snow_result.get('number', sys_id)} resolved.")
                    snow_results.append(snow_result)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
            if last_exc is not None:
                log_fn(f"[IncidentResolver] WARNING: failed to resolve ServiceNow ticket {sys_id} after retry: {last_exc}")

    return {"defender": result, "servicenow": snow_results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve a Microsoft Defender incident (and linked ServiceNow tickets) via API.")
    parser.add_argument("--incident-id",     required=True,  help="Defender incident ID")
    parser.add_argument("--status",          default="Resolved")
    parser.add_argument("--classification",  default="")
    parser.add_argument("--comment",         default="")
    parser.add_argument("--assigned-to",     default="", help="Email to assign the Defender incident to")
    parser.add_argument("--snow-tickets",    default="", help="Comma-separated ServiceNow incident sys_ids to resolve")
    parser.add_argument("--resolution-code", default="", help="ServiceNow resolution code (UI label)")
    parser.add_argument("--caller-email",    default="", help="Email to set as ServiceNow Caller")
    # Kept for CLI compatibility with old script — no longer used
    parser.add_argument("--profile-directory",  default="", help="(unused — legacy arg)")
    parser.add_argument("--timeout",            default=45,  type=int, help="(unused — legacy arg)")
    parser.add_argument("--validation-seconds", default=60,  type=int, help="(unused — legacy arg)")

    args = parser.parse_args()
    snow_tickets = [t for t in args.snow_tickets.split(",") if t.strip()]
    resolve(
        incident_id=args.incident_id,
        status=args.status,
        classification=args.classification,
        comment=args.comment,
        assigned_to=args.assigned_to,
        snow_tickets=snow_tickets,
        resolution_code=args.resolution_code,
        caller_email=args.caller_email,
    )


if __name__ == "__main__":
    main()
