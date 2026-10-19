from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


CANDIDATE_API_PATHS = (
    "/api/chat",
    "/api/v1/chat",
    "/api/v1/chat/completions",
    "/api/odin/chat",
    "/odin/api/chat",
    "/odin/api/v1/chat",
    "/chat",
    "/chat/completions",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive ODIN chatbot client using an API URL and token. "
            "Token can be provided via --api-token or ODIN_API_TOKEN."
        )
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("ODIN_API_URL", ""),
        help="ODIN chat endpoint URL (or set ODIN_API_URL).",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("ODIN_BASE_URL", "https://odin.landpower.net"),
        help="Base ODIN host used for endpoint discovery when --api-url is missing.",
    )
    parser.add_argument(
        "--api-token",
        default=os.getenv("ODIN_API_TOKEN", ""),
        help="ODIN API token (or set ODIN_API_TOKEN).",
    )
    parser.add_argument(
        "--auth-header",
        default="Authorization",
        help="HTTP header used for token auth (default: Authorization).",
    )
    parser.add_argument(
        "--auth-scheme",
        default="Bearer",
        help="Auth scheme prefix before token, e.g. Bearer. Use empty string for raw token.",
    )
    parser.add_argument(
        "--api-token-base64",
        action="store_true",
        help="Decode --api-token / ODIN_API_TOKEN as Base64 text before sending.",
    )
    parser.add_argument(
        "--prompt-field",
        default="message",
        help="JSON field name where the prompt is sent (default: message).",
    )
    parser.add_argument(
        "--system-prompt",
        default="",
        help="Optional system prompt sent with each request.",
    )
    parser.add_argument(
        "--response-path",
        default="reply",
        help=(
            "Dot-path to read assistant text from response JSON "
            "(default: reply). Examples: response, data.answer, choices.0.message.content"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (for trusted internal environments only).",
    )
    parser.add_argument(
        "--ca-bundle",
        default=os.getenv("ODIN_CA_BUNDLE", ""),
        help="Path to CA bundle PEM file for TLS validation.",
    )
    return parser.parse_args()


def _read_nested_value(payload: Any, path: str) -> Any:
    current = payload
    for raw_part in path.split("."):
        part = raw_part.strip()
        if not part:
            return None

        if isinstance(current, list):
            if not part.isdigit():
                return None
            index = int(part)
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue

        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue

        return None
    return current


def build_auth_value(token: str, scheme: str) -> str:
    normalized_scheme = scheme.strip()
    if normalized_scheme:
        return f"{normalized_scheme} {token}"
    return token


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


def call_odin_api(
    *,
    api_url: str,
    auth_header: str,
    auth_value: str,
    prompt_field: str,
    user_prompt: str,
    system_prompt: str,
    timeout: int,
    ssl_context: ssl.SSLContext,
) -> dict[str, Any]:
    payload: dict[str, Any] = {prompt_field: user_prompt}
    if system_prompt.strip():
        payload["system"] = system_prompt.strip()

    request_body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        auth_header: auth_value,
    }

    request = urllib.request.Request(
        api_url,
        data=request_body,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=max(1, timeout), context=ssl_context) as response:
        raw = response.read().decode("utf-8", errors="replace")

    try:
        parsed: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ODIN API returned non-JSON response: {raw[:800]}") from exc

    return parsed


def discover_api_url(
    *,
    base_url: str,
    auth_header: str,
    auth_value: str,
    prompt_field: str,
    timeout: int,
    ssl_context: ssl.SSLContext,
) -> str | None:
    normalized_base = base_url.strip().rstrip("/")
    if not normalized_base:
        return None

    body = json.dumps({prompt_field: "ping"}).encode("utf-8")

    for path in CANDIDATE_API_PATHS:
        candidate_url = normalized_base + path
        request = urllib.request.Request(
            candidate_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                auth_header: auth_value,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=max(1, timeout), context=ssl_context) as response:
                status = int(getattr(response, "status", 200))
                if status in {200, 201, 202}:
                    return candidate_url
        except urllib.error.HTTPError as exc:
            # 400/401/403/422 can still indicate endpoint exists but payload/auth differs.
            if int(exc.code) in {400, 401, 403, 422}:
                return candidate_url
        except urllib.error.URLError:
            continue

    return None


def build_ssl_context(insecure: bool, ca_bundle: str) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()

    if ca_bundle.strip():
        return ssl.create_default_context(cafile=ca_bundle.strip())

    return ssl.create_default_context()


def main() -> int:
    args = parse_args()

    if not args.api_token.strip():
        print("Missing API token. Provide --api-token or set ODIN_API_TOKEN.", file=sys.stderr)
        return 1

    try:
        normalized_token = normalize_token(args.api_token, args.api_token_base64)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    auth_value = build_auth_value(normalized_token, args.auth_scheme)

    try:
        ssl_context = build_ssl_context(args.insecure, args.ca_bundle)
    except Exception as exc:  # noqa: BLE001
        print(f"TLS configuration error: {exc}", file=sys.stderr)
        return 1

    api_url = args.api_url.strip()
    if not api_url:
        discovered_url = discover_api_url(
            base_url=args.base_url,
            auth_header=args.auth_header.strip(),
            auth_value=auth_value,
            prompt_field=args.prompt_field.strip(),
            timeout=max(8, args.timeout),
            ssl_context=ssl_context,
        )
        if discovered_url:
            api_url = discovered_url
            print(f"Discovered API URL: {api_url}")
        else:
            api_url = args.base_url.strip().rstrip("/") + "/api/chat"
            print(
                f"Could not verify API endpoint automatically. Falling back to: {api_url}",
                file=sys.stderr,
            )

    print("ODIN chatbot started. Type 'exit' to quit.")

    while True:
        try:
            user_prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            return 0

        if not user_prompt:
            continue

        if user_prompt.lower() in {"exit", "quit"}:
            print("Session ended.")
            return 0

        try:
            response_json = call_odin_api(
                api_url=api_url,
                auth_header=args.auth_header.strip(),
                auth_value=auth_value,
                prompt_field=args.prompt_field.strip(),
                user_prompt=user_prompt,
                system_prompt=args.system_prompt,
                timeout=args.timeout,
                ssl_context=ssl_context,
            )
            assistant_text = _read_nested_value(response_json, args.response_path.strip())
            if assistant_text is None:
                assistant_text = json.dumps(response_json, indent=2, ensure_ascii=True)
            print(f"ODIN: {assistant_text}")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            print(f"HTTP error {exc.code}: {exc.reason}", file=sys.stderr)
            if error_body:
                print(error_body[:1200], file=sys.stderr)
        except urllib.error.URLError as exc:
            print(f"Network error: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"Request failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
