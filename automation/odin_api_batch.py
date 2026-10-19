from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any


SKIP_FILE_NAMES = {
    "odin-response.json",
    "odin-response.txt",
    "odin-response-api.raw.json",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".json",
    ".html",
    ".htm",
    ".eml",
    ".md",
    ".csv",
    ".log",
    ".xml",
}

DEFAULT_PROMPT = """Output plain text only.
Do not add any introduction, explanation, disclaimer, or extra notes.
Use concise, analyst-style language.
Provide sections in this exact order:
ODIN • AI Findings
Confidence
[CONFIDENCE]
[LABEL]

Summary of Assessment
What happened
Why it matters
Recommended next actions
"""


def parse_args() -> argparse.Namespace:
    workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description="Process each artifact folder with ODIN API and save responses without Selenium."
    )
    parser.add_argument(
        "--artifacts-root",
        default=os.path.join(workspace_root, "artifacts"),
        help="Root folder containing artifact subfolders.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("ODIN_API_URL", ""),
        help="ODIN API endpoint URL (or set ODIN_API_URL).",
    )
    parser.add_argument(
        "--api-token",
        default=os.getenv("ODIN_API_TOKEN", ""),
        help="ODIN API token (or set ODIN_API_TOKEN).",
    )
    parser.add_argument(
        "--auth-header",
        default="Authorization",
        help="Auth header name (default: Authorization).",
    )
    parser.add_argument(
        "--auth-scheme",
        default="Bearer",
        help="Auth scheme prefix. Use empty string for raw token.",
    )
    parser.add_argument(
        "--api-token-base64",
        action="store_true",
        help="Decode --api-token / ODIN_API_TOKEN as Base64 text before sending.",
    )
    parser.add_argument(
        "--prompt-field",
        default="message",
        help="JSON field name where prompt is sent.",
    )
    parser.add_argument(
        "--system-prompt",
        default="",
        help="Optional system prompt sent in JSON key 'system'.",
    )
    parser.add_argument(
        "--response-path",
        default="reply",
        help="Dot-path to assistant text in response JSON, e.g. reply or choices.0.message.content.",
    )
    parser.add_argument(
        "--max-file-chars",
        type=int,
        default=12000,
        help="Max characters included from each artifact file.",
    )
    parser.add_argument(
        "--max-total-chars",
        type=int,
        default=80000,
        help="Max total characters included in folder context.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="HTTP timeout seconds.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build prompts and report actions without calling API.",
    )
    return parser.parse_args()


def build_auth_value(token: str, scheme: str) -> str:
    normalized_scheme = scheme.strip()
    return f"{normalized_scheme} {token}" if normalized_scheme else token


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


def _read_nested_value(payload: Any, path: str) -> Any:
    current: Any = payload
    for segment in path.split("."):
        key = segment.strip()
        if not key:
            return None
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
            continue
        if isinstance(current, list):
            if not key.isdigit():
                return None
            idx = int(key)
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            continue
        return None
    return current


def _read_text_file(file_path: str, max_chars: int) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as file_obj:
        text = file_obj.read()
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[TRUNCATED]"
    return text


def _is_candidate_artifact(file_name: str) -> bool:
    lowered = file_name.lower()
    if lowered in SKIP_FILE_NAMES:
        return False
    if lowered == "odin-prompt.txt":
        return False
    _, ext = os.path.splitext(lowered)
    return ext in TEXT_EXTENSIONS


def _folder_files(folder_path: str) -> list[str]:
    results: list[str] = []
    for name in sorted(os.listdir(folder_path)):
        absolute = os.path.join(folder_path, name)
        if os.path.isfile(absolute) and _is_candidate_artifact(name):
            results.append(absolute)
    return results


def _read_folder_prompt(folder_path: str) -> str:
    prompt_path = os.path.join(folder_path, "odin-prompt.txt")
    if os.path.isfile(prompt_path):
        try:
            text = _read_text_file(prompt_path, 20000).strip()
            if text:
                return text
        except OSError:
            pass
    return DEFAULT_PROMPT.strip()


def build_folder_prompt(folder_path: str, max_file_chars: int, max_total_chars: int) -> tuple[str, list[str]]:
    base_prompt = _read_folder_prompt(folder_path)
    files = _folder_files(folder_path)

    lines: list[str] = []
    lines.append(base_prompt)
    lines.append("")
    lines.append("Artifact folder context:")
    lines.append(f"Folder: {os.path.basename(folder_path)}")
    lines.append("Files included:")

    uploaded_names: list[str] = []
    for file_path in files:
        file_name = os.path.basename(file_path)
        uploaded_names.append(file_name)
        lines.append(f"- {file_name}")

    lines.append("")

    used_chars = sum(len(item) + 1 for item in lines)
    for file_path in files:
        if used_chars >= max_total_chars:
            lines.append("[Context limit reached before including all files]")
            break

        file_name = os.path.basename(file_path)
        try:
            content = _read_text_file(file_path, max_file_chars)
        except OSError as exc:
            content = f"[Could not read file: {exc}]"

        block = f"----- BEGIN FILE: {file_name} -----\n{content}\n----- END FILE: {file_name} -----\n"
        remaining = max_total_chars - used_chars
        if len(block) > remaining:
            block = block[: max(0, remaining)] + "\n[TRUNCATED BY TOTAL CONTEXT LIMIT]"
        lines.append(block)
        used_chars += len(block) + 1

    return "\n".join(lines).strip(), uploaded_names


def call_odin_api(
    *,
    api_url: str,
    auth_header: str,
    auth_value: str,
    prompt_field: str,
    user_prompt: str,
    system_prompt: str,
    timeout: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {prompt_field: user_prompt}
    if system_prompt.strip():
        payload["system"] = system_prompt.strip()

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            auth_header: auth_value,
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=max(1, timeout)) as response:
        raw_text = response.read().decode("utf-8", errors="replace")

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ODIN API returned non-JSON response: {raw_text[:1200]}") from exc


def write_outputs(
    folder_path: str,
    *,
    prompt_text: str,
    uploaded_files: list[str],
    assistant_text: str,
    response_json: dict[str, Any],
) -> None:
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    output_payload = {
        "Odin Summary": assistant_text,
        "Odin Confidence": "",
        "Odin Confidence Label": "",
        "Odin What Happened": "",
        "Odin Why It Matters": "",
        "Odin Recommended Next Actions": "",
        "Odin Raw Response": assistant_text,
        "Odin Uploaded Files": "\n".join(uploaded_files),
        "Odin Prompt": prompt_text,
        "Odin Response Updated At": now_text,
    }

    with open(os.path.join(folder_path, "odin-response.json"), "w", encoding="utf-8") as file_obj:
        json.dump(output_payload, file_obj, indent=2, ensure_ascii=False)

    with open(os.path.join(folder_path, "odin-response.txt"), "w", encoding="utf-8") as file_obj:
        file_obj.write(assistant_text)

    with open(os.path.join(folder_path, "odin-response-api.raw.json"), "w", encoding="utf-8") as file_obj:
        json.dump(response_json, file_obj, indent=2, ensure_ascii=False)


def process_folder(args: argparse.Namespace, folder_path: str) -> bool:
    folder_name = os.path.basename(folder_path)
    prompt_text, uploaded_files = build_folder_prompt(
        folder_path,
        max_file_chars=max(500, args.max_file_chars),
        max_total_chars=max(2000, args.max_total_chars),
    )

    print(f"[ODIN-API] Folder: {folder_name}")
    print(f"[ODIN-API] Included files: {len(uploaded_files)}")

    if args.dry_run:
        print("[ODIN-API] Dry-run enabled; skipping API call.")
        return True

    try:
        auth_value = build_auth_value(args.api_token.strip(), args.auth_scheme)
        response_json = call_odin_api(
            api_url=args.api_url.strip(),
            auth_header=args.auth_header.strip(),
            auth_value=auth_value,
            prompt_field=args.prompt_field.strip(),
            user_prompt=prompt_text,
            system_prompt=args.system_prompt,
            timeout=args.timeout,
        )
        extracted = _read_nested_value(response_json, args.response_path.strip())
        if extracted is None:
            assistant_text = json.dumps(response_json, indent=2, ensure_ascii=False)
        else:
            assistant_text = str(extracted)

        write_outputs(
            folder_path,
            prompt_text=prompt_text,
            uploaded_files=uploaded_files,
            assistant_text=assistant_text,
            response_json=response_json,
        )
        print("[ODIN-API] Saved odin-response.json, odin-response.txt, odin-response-api.raw.json")
        return True
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        print(f"[ODIN-API] HTTP error {exc.code} on folder '{folder_name}': {exc.reason}", file=sys.stderr)
        if error_body:
            print(error_body[:1200], file=sys.stderr)
        return False
    except urllib.error.URLError as exc:
        print(f"[ODIN-API] Network error on folder '{folder_name}': {exc}", file=sys.stderr)
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[ODIN-API] Failed folder '{folder_name}': {exc}", file=sys.stderr)
        return False


def main() -> int:
    args = parse_args()

    if not os.path.isdir(args.artifacts_root):
        print(f"Artifacts root not found: {args.artifacts_root}", file=sys.stderr)
        return 1

    if not args.dry_run and not args.api_url.strip():
        print("Missing API URL. Provide --api-url or set ODIN_API_URL.", file=sys.stderr)
        return 1

    if not args.dry_run and not args.api_token.strip():
        print("Missing API token. Provide --api-token or set ODIN_API_TOKEN.", file=sys.stderr)
        return 1

    if not args.dry_run:
        try:
            args.api_token = normalize_token(args.api_token, args.api_token_base64)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    folders = [
        os.path.join(args.artifacts_root, name)
        for name in sorted(os.listdir(args.artifacts_root))
        if os.path.isdir(os.path.join(args.artifacts_root, name))
    ]

    if not folders:
        print("No artifact folders found.")
        return 0

    print(f"[ODIN-API] Found {len(folders)} artifact folders")

    success_count = 0
    for folder_path in folders:
        if process_folder(args, folder_path):
            success_count += 1

    print(f"[ODIN-API] Completed: {success_count}/{len(folders)} folders")
    return 0 if success_count == len(folders) else 2


if __name__ == "__main__":
    raise SystemExit(main())
