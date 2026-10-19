"""
Microsoft Graph Mail client.

Searches a mailbox for DLP notification emails and downloads them as .eml files.
Replaces the Selenium-based Outlook tab in function/tools.py.

Required Graph permission: Mail.Read (Application)
The mailbox is identified by MAIL_USER_ID in config (UPN or Azure AD object ID).
"""

from __future__ import annotations

from pathlib import Path

import requests

import auth.token_manager as token_manager
import config

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _headers(accept: str = "application/json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token_manager.get_graph_token()}",
        "Accept": accept,
    }


def search_dlp_notifications(query: str, top: int = 10) -> list[dict]:
    """
    Search the DLP notification mailbox for messages matching query.

    Returns a list of message objects with fields:
      id, subject, receivedDateTime, from, bodyPreview
    """
    user = config.MAIL_USER_ID
    url = f"{_GRAPH_BASE}/users/{user}/messages"
    resp = requests.get(
        url,
        headers=_headers(),
        params={
            "$search": f'"{query}"',
            "$select": "id,subject,receivedDateTime,from,bodyPreview",
            "$top": top,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


def download_email_as_eml(message_id: str, save_path: Path) -> None:
    """
    Download a mail message as a raw MIME .eml file.

    Args:
        message_id: Graph message ID
        save_path:  Destination file path (should end in .eml)
    """
    user = config.MAIL_USER_ID
    url = f"{_GRAPH_BASE}/users/{user}/messages/{message_id}/$value"
    resp = requests.get(url, headers=_headers(accept="message/rfc822"), stream=True, timeout=60)
    resp.raise_for_status()

    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def find_and_save_dlp_email(alert_name: str, artifact_folder: Path, log_fn=print) -> Path | None:
    """
    Search for a DLP notification email matching the alert name and save it as .eml.

    Returns the saved .eml path, or None if no matching email was found.
    """
    # Use the first ~60 chars of the alert name as the search query
    query = alert_name[:60].strip()
    log_fn(f"Searching mailbox for DLP email: {query!r}")

    messages = search_dlp_notifications(query, top=5)
    if not messages:
        log_fn("  No matching DLP notification email found.")
        return None

    # Take the most recent match
    message = messages[0]
    subject = message.get("subject", "")
    log_fn(f"  Found email: {subject!r}")

    save_path = artifact_folder / "dlp_notification.eml"
    download_email_as_eml(message["id"], save_path)
    log_fn(f"  Saved: {save_path.name}")
    return save_path
