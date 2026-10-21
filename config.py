"""
Configuration loader for RedFlagged.

Production:  reads all secrets from CyberArk CCP at startup.
Local dev:   falls back to a .env file when CYBERARK_HOST is not set.

Required environment variables (always — not secrets):
  CYBERARK_HOST     hostname of your CyberArk CCP server
  CYBERARK_APP_ID   App ID registered in CyberArk for this application
  CYBERARK_SAFE     Safe name (e.g. "RedFlagged-dev" or "RedFlagged-prod")

Secrets stored in CyberArk (object names):
  AzureTenantId          Azure AD tenant ID
  AzureClientId          App Registration client ID
  AzureClientSecret      App Registration client secret
  SqlConnectionString    Azure SQL ODBC connection string

Local dev .env fallback (never commit this file):
  AZURE_TENANT_ID
  AZURE_CLIENT_ID
  AZURE_CLIENT_SECRET
  SQL_CONNECTION_STRING
  MAIL_USER_ID           UPN or object ID of the mailbox to search for DLP notifications
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
# In a PyInstaller-frozen build there is no repo checkout — config.json, .env,
# data/, etc. live next to the running .exe instead of next to this source file.
if getattr(sys, "frozen", False):
    WORKSPACE_ROOT = Path(sys.executable).resolve().parent
else:
    WORKSPACE_ROOT = Path(__file__).parent

# ── optional dotenv for local dev ────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(WORKSPACE_ROOT / ".env")
except ImportError:
    pass

ARTIFACTS_ROOT      = WORKSPACE_ROOT / "artifacts"
INVENTORY_PATH      = WORKSPACE_ROOT / "automation" / "artifacts_inventory.json"
ARTIFACT_CACHE_PATH = WORKSPACE_ROOT / "js" / "artifactData.js"
DB_INFO_CACHE_PATH  = WORKSPACE_ROOT / "js" / "dbInfoData.js"

# ── secrets (populated by load()) ────────────────────────────────────────────
TENANT_ID: str = ""
CLIENT_ID: str = ""
CLIENT_SECRET: str = ""
SQL_CONNECTION_STRING: str = ""
MAIL_USER_ID: str = ""          # UPN or object ID for mailbox search

# ── ServiceNow ────────────────────────────────────────────────────────────────
SNOW_INSTANCE_URL: str = ""     # e.g. https://your-instance.service-now.com
SNOW_USERNAME: str = ""
SNOW_PASSWORD: str = ""

# ── Current analyst (server-side only — never from frontend) ──────────────────
CURRENT_USER_EMAIL: str = ""    # In production: resolved from SSO/session token

# ── Authentication provider ───────────────────────────────────────────────────
AUTH_PROVIDER: str = "json"     # Phase 1: json. Phase 2: cyberark or oidc.


def load() -> None:
    """
    Load all secrets. Called once at pipeline startup.
    Uses CyberArk when CYBERARK_HOST is set; falls back to env vars otherwise.
    """
    global TENANT_ID, CLIENT_ID, CLIENT_SECRET, SQL_CONNECTION_STRING, MAIL_USER_ID, AUTH_PROVIDER

    cyberark_host = os.environ.get("CYBERARK_HOST", "").strip()

    if cyberark_host:
        from auth.cyberark_client import CyberArkClient
        cyberark = CyberArkClient(
            host=cyberark_host,
            app_id=os.environ["CYBERARK_APP_ID"],
            safe=os.environ["CYBERARK_SAFE"],
        )
        TENANT_ID             = cyberark.get_secret("AzureTenantId")
        CLIENT_ID             = cyberark.get_secret("AzureClientId")
        CLIENT_SECRET         = cyberark.get_secret("AzureClientSecret")
        SQL_CONNECTION_STRING = cyberark.get_secret("SqlConnectionString")
    else:
        # Local dev fallback — requires .env file
        TENANT_ID             = os.environ["AZURE_TENANT_ID"]
        CLIENT_ID             = os.environ["AZURE_CLIENT_ID"]
        CLIENT_SECRET         = os.environ["AZURE_CLIENT_SECRET"]
        SQL_CONNECTION_STRING = os.environ["SQL_CONNECTION_STRING"]

    MAIL_USER_ID = os.environ.get("MAIL_USER_ID", "me")

    # ServiceNow (optional — assignment validation only)
    SNOW_INSTANCE_URL  = os.environ.get("SNOW_INSTANCE_URL", "")
    SNOW_USERNAME      = os.environ.get("SNOW_USERNAME", "")
    SNOW_PASSWORD      = os.environ.get("SNOW_PASSWORD", "")

    # Current analyst email (server-side — never trusted from frontend)
    CURRENT_USER_EMAIL = os.environ.get("CURRENT_USER_EMAIL", "")

    # Authentication provider (Phase 1: json)
    AUTH_PROVIDER = os.environ.get("AUTH_PROVIDER", "json")

    # Default to SQLite when no SQL connection string is provided
    if not SQL_CONNECTION_STRING:
        SQL_CONNECTION_STRING = f"sqlite:///{WORKSPACE_ROOT / 'redflagged.db'}"
