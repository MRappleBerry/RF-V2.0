from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL_PATTERN = re.compile(r"https?://[^\s]+")
IGNORED_URL_SNIPPETS = (
    "twitter.com/localhost_run",
    "admin.localhost.run",
    "localhost.run/docs",
    "localhost:3000/docs",
)


class EmbeddedServerThread(threading.Thread):
    def __init__(self, host: str, port: int, app: Any) -> None:
        super().__init__(daemon=True)
        self._server = make_server(host, port, app)
        self._context = app.app_context()
        self._context.push()

    def run(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._context.pop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Share the local RedFlagged Flask app through an outbound SSH tunnel."
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=5000,
        help="Local RedFlagged port to expose (default: 5000).",
    )
    parser.add_argument(
        "--local-host",
        default="127.0.0.1",
        help="Local host where the RedFlagged app is reachable (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--remote",
        default="nokey@localhost.run",
        help="SSH tunnel provider target (default: nokey@localhost.run).",
    )
    parser.add_argument(
        "--url-timeout",
        type=int,
        default=25,
        help="Seconds to wait for the tunnel provider to emit a public URL (default: 25).",
    )
    return parser.parse_args()


def is_port_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def wait_for_http_ready(url: str, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except URLError:
            time.sleep(0.5)
    return False


def extract_public_url_from_line(line: str) -> str:
    candidates = [line]

    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        for key in ("address", "listen_host"):
            value = str(parsed.get(key) or "").strip()
            if value and value != "localhost.run":
                return f"https://{value}"
        candidates.extend(str(value) for value in parsed.values())
    elif isinstance(parsed, list):
        candidates.extend(str(value) for value in parsed)

    for candidate_line in candidates:
        for raw_match in PUBLIC_URL_PATTERN.findall(candidate_line):
            normalized = raw_match.rstrip("]').,\"")
            if any(snippet in normalized for snippet in IGNORED_URL_SNIPPETS):
                continue
            return normalized
    return ""


def start_embedded_app(local_host: str, local_port: int) -> EmbeddedServerThread:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import app  # Imported lazily so an already-running app does not need re-import work.

    server_thread = EmbeddedServerThread(local_host, local_port, app)
    server_thread.start()
    return server_thread


def main() -> int:
    args = parse_args()

    if shutil.which("ssh") is None:
        print("OpenSSH client was not found on this machine.", file=sys.stderr)
        return 1

    local_url = f"http://{args.local_host}:{args.local_port}/"
    server_thread: EmbeddedServerThread | None = None
    app_started_here = False

    if is_port_listening(args.local_host, args.local_port):
        print(f"Using existing RedFlagged app on {local_url}")
    else:
        print(f"No app detected on {local_url}. Starting an embedded RedFlagged server...")
        server_thread = start_embedded_app(args.local_host, args.local_port)
        app_started_here = True
        if not wait_for_http_ready(local_url, 15):
            print("The embedded RedFlagged server did not become ready in time.", file=sys.stderr)
            if server_thread is not None:
                server_thread.shutdown()
            return 1
        print(f"Embedded RedFlagged server is running on {local_url}")

    print(
        "Warning: this fallback creates a public HTTPS tunnel. Use it only if your organization allows external sharing of this app."
    )

    tunnel_command = [
        "ssh",
        "-T",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-R",
        f"80:{args.local_host}:{args.local_port}",
        args.remote,
        "--",
        "--output",
        "json",
    ]

    process = subprocess.Popen(
        tunnel_command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    public_url = ""
    url_deadline = time.time() + max(5, args.url_timeout)

    try:
        assert process.stdout is not None
        while True:
            line = process.stdout.readline()
            if line:
                clean_line = line.rstrip("\r\n")
                print(clean_line)
                if not public_url:
                    public_url = extract_public_url_from_line(clean_line)
                    if not public_url and clean_line.endswith(".lhr.life"):
                        public_url = f"https://{clean_line}"
                    if public_url:
                        print(f"\nShare this URL: {public_url}\n")
            else:
                if process.poll() is not None:
                    break
                time.sleep(0.1)

            if not public_url and time.time() > url_deadline:
                print(
                    "The tunnel connected but did not print a public URL yet. Leave this session open and watch the output.",
                    file=sys.stderr,
                )
                url_deadline = float("inf")

        return process.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping RedFlagged tunnel...")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout is not None:
            process.stdout.close()
        if app_started_here and server_thread is not None:
            server_thread.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())