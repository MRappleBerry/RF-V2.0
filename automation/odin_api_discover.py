from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


CANDIDATE_PATHS = (
    "/api/chat",
    "/api/v1/chat",
    "/api/v1/chat/completions",
    "/api/odin/chat",
    "/odin/api/chat",
    "/odin/api/v1/chat",
    "/chat",
    "/chat/completions",
)

CANDIDATE_PROMPT_FIELDS = (
    "message",
    "prompt",
    "input",
    "question",
)

CANDIDATE_AUTH_HEADERS = (
    "Authorization",
    "X-API-Key",
    "Api-Key",
)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe likely ODIN API endpoints to discover a working URL/header/payload combination."
    )
    parser.add_argument(
        "--base-url",
        default="https://odin.landpower.net",
        help="Base URL for ODIN host.",
    )
    parser.add_argument(
        "--api-token",
        default=os.getenv("ODIN_API_TOKEN", ""),
        help="API token, or set ODIN_API_TOKEN.",
    )
    parser.add_argument(
        "--api-token-base64",
        action="store_true",
        help="Decode --api-token / ODIN_API_TOKEN as Base64 text before sending.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--report-file",
        default=os.path.join("artifacts", "odin_api_discovery_report.json"),
        help="Path to write JSON discovery report.",
    )
    return parser.parse_args()



def normalize_token(token: str, token_is_base64: bool) -> str:
    value = token.strip()
    if not value:
        return ""

    if token_is_base64:
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8", errors="strict").strip()
            if decoded:
                return decoded
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Token is not valid Base64 text.") from exc

    return value



def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path



def _mask_value(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + ("*" * (len(value) - 8)) + value[-4:]



def _try_request(
    *,
    url: str,
    auth_header: str,
    auth_value: str,
    prompt_field: str,
    timeout: int,
) -> dict[str, Any]:
    payload = {prompt_field: "ping"}
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            auth_header: auth_value,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=max(1, timeout)) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200)),
                "reason": "OK",
                "body_preview": raw[:400],
            }
    except urllib.error.HTTPError as exc:
        try:
            body_preview = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            body_preview = ""
        return {
            "ok": False,
            "status": int(exc.code),
            "reason": str(exc.reason),
            "body_preview": body_preview,
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status": -1,
            "reason": f"Network error: {exc}",
            "body_preview": "",
        }



def _auth_values(token: str) -> tuple[str, ...]:
    return (
        f"Bearer {token}",
        token,
    )



def main() -> int:
    args = parse_args()

    if not args.api_token.strip():
        print("Missing API token. Provide --api-token or set ODIN_API_TOKEN.", file=sys.stderr)
        return 1

    try:
        token = normalize_token(args.api_token, args.api_token_base64)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not token:
        print("Token is empty after normalization.", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "base_url": args.base_url,
        "token_masked": _mask_value(token),
        "attempts": [],
        "first_usable_candidate": None,
    }

    print("[ODIN-DISCOVER] Probing candidate endpoints...")

    for path in CANDIDATE_PATHS:
        url = _join_url(args.base_url, path)
        for auth_header in CANDIDATE_AUTH_HEADERS:
            for auth_value in _auth_values(token):
                for prompt_field in CANDIDATE_PROMPT_FIELDS:
                    result = _try_request(
                        url=url,
                        auth_header=auth_header,
                        auth_value=auth_value,
                        prompt_field=prompt_field,
                        timeout=args.timeout,
                    )

                    attempt = {
                        "url": url,
                        "auth_header": auth_header,
                        "auth_value_scheme": "Bearer" if auth_value.startswith("Bearer ") else "Raw",
                        "prompt_field": prompt_field,
                        "status": result["status"],
                        "reason": result["reason"],
                        "ok": result["ok"],
                        "body_preview": result["body_preview"],
                    }
                    report["attempts"].append(attempt)

                    print(
                        f"[ODIN-DISCOVER] {attempt['status']} {url} "
                        f"header={auth_header} scheme={attempt['auth_value_scheme']} field={prompt_field}"
                    )

                    # 200/201/202 are likely usable. 400 can also indicate endpoint exists but payload shape differs.
                    if report["first_usable_candidate"] is None and attempt["status"] in {200, 201, 202, 400}:
                        report["first_usable_candidate"] = {
                            "url": url,
                            "auth_header": auth_header,
                            "auth_value_scheme": attempt["auth_value_scheme"],
                            "prompt_field": prompt_field,
                            "status": attempt["status"],
                            "reason": attempt["reason"],
                            "body_preview": attempt["body_preview"],
                        }

    report_dir = os.path.dirname(os.path.abspath(args.report_file))
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)

    with open(args.report_file, "w", encoding="utf-8") as file_obj:
        json.dump(report, file_obj, indent=2, ensure_ascii=False)

    candidate = report.get("first_usable_candidate")
    if candidate:
        print("[ODIN-DISCOVER] Found a likely candidate configuration.")
        print(
            "[ODIN-DISCOVER] "
            f"url={candidate['url']} header={candidate['auth_header']} "
            f"scheme={candidate['auth_value_scheme']} prompt_field={candidate['prompt_field']}"
        )
        print(f"[ODIN-DISCOVER] Report saved: {args.report_file}")
        return 0

    print("[ODIN-DISCOVER] No usable candidate found. Check report for details.")
    print(f"[ODIN-DISCOVER] Report saved: {args.report_file}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
