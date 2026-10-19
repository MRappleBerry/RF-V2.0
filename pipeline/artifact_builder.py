"""
Artifact builder — creates the per-alert artifact folder and files from API data.

For each DLP alert, creates:
  artifacts/{safe_alert_name}/
    alert-record.json       — flat metadata dict (same schema as existing)
    classifiers.html        — SIT classifier table from Graph evidence
    SIT_analysis.html       — same table, "All Full Names" rows removed
    dlp_notification.eml    — matching email (Exchange alerts only)

Writes alert and artifact records to the database via repository.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import config
import data.repository as repository
from clients import mail_client, purview_client


# Characters not allowed in Windows folder names
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_folder_name(name: str) -> str:
    """Replace Windows-invalid characters with underscores."""
    return _INVALID_CHARS.sub("_", name).strip()


def _get_or_create_folder(alert_name: str) -> Path:
    folder = config.ARTIFACTS_ROOT / _safe_folder_name(alert_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ── HTML generation ───────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ background: #0078d4; color: #fff; padding: 6px 10px; text-align: left; }}
  td {{ border: 1px solid #ddd; padding: 5px 10px; vertical-align: top; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
</style>
</head>
<body>
<table>
<thead><tr>{headers}</tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>
"""


def _build_classifiers_html(classifier_rows: list[dict], include_all_full_names: bool = True) -> str:
    """Generate classifiers.html content from a list of classifier dicts."""
    if not classifier_rows:
        return "<p>No classifier data available.</p>"

    cols = ["SIT Name", "Matched Content", "Location", "Surrounding Characters"]
    headers_html = "".join(f"<th>{c}</th>" for c in cols)

    row_html_parts = []
    for row in classifier_rows:
        sit_name = row.get("SIT Name", "")
        if not include_all_full_names and sit_name == "All Full Names":
            continue
        cells = "".join(
            f"<td>{row.get(c, '')}</td>" for c in cols
        )
        row_html_parts.append(f"<tr>{cells}</tr>")

    return _HTML_TEMPLATE.format(
        headers=headers_html,
        rows="\n".join(row_html_parts),
    )


# ── Main build entry point ────────────────────────────────────────────────────

def build(alert: dict, evidence: dict, log_fn=print) -> None:
    """
    Build the full artifact folder for a single DLP alert.

    Args:
        alert:    Row dict from purview_client.list_dlp_alerts()
        evidence: Full detail dict from purview_client.get_alert_evidence()
        log_fn:   Logger function
    """
    alert_name = alert["Alert Name"]
    graph_id   = alert.get("_graph_id", "")
    location   = alert.get("Location", "")

    log_fn(f"  Building artifacts for: {alert_name[:80]}")

    folder = _get_or_create_folder(alert_name)

    # ── alert-record.json ─────────────────────────────────────────────────────
    record = {k: v for k, v in alert.items() if not k.startswith("_")}
    record.update({
        "Odin Summary":                  "",
        "Odin Confidence":               "",
        "Odin Confidence Label":         "",
        "Odin What Happened":            "",
        "Odin Why It Matters":           "",
        "Odin Recommended Next Actions": "",
        "Odin Raw Response":             "",
        "Odin Uploaded Files":           "",
        "Odin Prompt":                   "",
        "Odin Response Updated At":      "",
    })
    (folder / "alert-record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── classifiers.html + SIT_analysis.html ──────────────────────────────────
    classifier_rows = purview_client.extract_classifiers(evidence)

    classifiers_html = _build_classifiers_html(classifier_rows, include_all_full_names=True)
    (folder / "classifiers.html").write_text(classifiers_html, encoding="utf-8")

    sit_html = _build_classifiers_html(classifier_rows, include_all_full_names=False)
    (folder / "SIT_analysis.html").write_text(sit_html, encoding="utf-8")

    # ── email artifact (Exchange alerts only) ─────────────────────────────────
    if "Exchange" in location or "exchange" in location.lower():
        mail_client.find_and_save_dlp_email(alert_name, folder, log_fn=log_fn)

    # ── persist to database + register artifacts ──────────────────────────────
    if graph_id:
        repository.upsert_alert(alert)

        for file_path in folder.iterdir():
            if file_path.is_file():
                repository.upsert_artifact(
                    alert_id=graph_id,
                    file_path=file_path,
                    artifacts_root=config.ARTIFACTS_ROOT,
                    alert_status=alert.get("Status", ""),
                )

    log_fn(f"  Done: {folder.name}/")
