"""
Incident resolver — replaces function/incident_resolved.py.

Resolves a Microsoft Defender incident via API — no browser required.
Accepts the same CLI arguments as the original script so dashboard_server.py
needs minimal changes.

Usage:
    python pipeline/incident_resolver.py \\
        --incident-id 282359 \\
        --status Resolved \\
        --classification "False Positive: Not malicious" \\
        --comment "Reviewed and confirmed benign."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import auth.token_manager as token_manager
from clients import defender_client


def resolve(
    incident_id: str,
    status: str = "Resolved",
    classification: str = "",
    comment: str = "",
    log_fn=print,
) -> dict:
    config.load()
    token_manager.init(config.TENANT_ID, config.CLIENT_ID, config.CLIENT_SECRET)

    log_fn(f"[IncidentResolver] Resolving incident {incident_id}...")
    result = defender_client.resolve_incident(
        incident_id=incident_id,
        status=status,
        classification=classification,
        comment=comment,
        log_fn=log_fn,
    )
    log_fn(f"[IncidentResolver] Done. Defender status: {result.get('status', 'unknown')}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve a Microsoft Defender incident via API.")
    parser.add_argument("--incident-id",     required=True,  help="Defender incident ID")
    parser.add_argument("--status",          default="Resolved")
    parser.add_argument("--classification",  default="")
    parser.add_argument("--comment",         default="")
    # Kept for CLI compatibility with old script — no longer used
    parser.add_argument("--profile-directory",  default="", help="(unused — legacy arg)")
    parser.add_argument("--timeout",            default=45,  type=int, help="(unused — legacy arg)")
    parser.add_argument("--validation-seconds", default=60,  type=int, help="(unused — legacy arg)")

    args = parser.parse_args()
    resolve(
        incident_id=args.incident_id,
        status=args.status,
        classification=args.classification,
        comment=args.comment,
    )


if __name__ == "__main__":
    main()
