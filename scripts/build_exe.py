"""
Build a standalone, no-Python-required Windows distribution of RedFlagged.

Produces dist/RedFlagged/ containing:
  RedFlagged.exe                   - the dashboard server (main entry point)
  RedFlagged-Runner.exe            - the pipeline/runner.py automation, as its
                                      own exe (dashboard_server.py launches it
                                      as a subprocess instead of python.exe)
  RedFlagged-IncidentResolver.exe  - pipeline/incident_resolver.py, same reason
  *.html, css/, js/                - static web assets, served from disk
  config.json.example, data/*.example
  README_TESTER.txt                - first-run setup instructions

Usage:
    python scripts/build_exe.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_TMP = ROOT / "build_tmp"
BUILD_OUT = ROOT / "build_output"
DIST = ROOT / "dist" / "RedFlagged"

BUILDS = [
    (ROOT / "automation" / "dashboard_server.py", "RedFlagged"),
    (ROOT / "pipeline" / "runner.py", "RedFlagged-Runner"),
    (ROOT / "pipeline" / "incident_resolver.py", "RedFlagged-IncidentResolver"),
]

README_TEXT = """\
RedFlagged — Tester Setup
=========================

1. First-time setup (one-time, before running RedFlagged.exe):

   a. Copy config.json.example -> config.json and fill in the real
      Azure AD / ServiceNow credentials you were given.
      NEVER share config.json — it holds live secrets.

   b. (Optional) Copy data\\system_settings.json.example ->
      data\\system_settings.json and set your ServiceNow assignment group.

2. Run RedFlagged.exe. It prints a line like:
       [Dashboard] Serving dashboard and API at http://0.0.0.0:8765/
   Open http://127.0.0.1:8765/ in a browser.

3. First sign-in: if no users exist yet, RedFlagged.exe creates a default
   local admin account automatically (email: admin, password: 123admin) —
   check the console output on first launch for the exact credentials,
   then sign in and change the password / create your own user from
   User Management. Or use "Sign in with Microsoft" if Azure AD is
   configured in config.json.

4. Leave RedFlagged.exe running (it's the web server). It will launch
   RedFlagged-Runner.exe / RedFlagged-IncidentResolver.exe on its own
   when you use the dashboard's automation controls or resolve/assign an
   incident — you never need to run those two directly.

5. To stop: close the RedFlagged.exe console window, or Ctrl+C in it.

All data this app creates (config.json, data\\*.json, redflagged.db) lives
next to these .exe files, so the whole folder is portable — copy it
elsewhere (or zip it up) and it keeps working the same way.
"""


def run_pyinstaller(entry: Path, name: str) -> None:
    print(f"\n=== Building {name}.exe from {entry.relative_to(ROOT)} ===")
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--onefile",
            "--name", name,
            "--distpath", str(BUILD_OUT),
            "--workpath", str(BUILD_TMP / name),
            "--specpath", str(BUILD_TMP),
            "--noconfirm",
            "--clean",
            str(entry),
        ],
        check=True,
        cwd=str(ROOT),
    )


def assemble_dist() -> None:
    print("\n=== Assembling dist/RedFlagged/ ===")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    for _, name in BUILDS:
        shutil.copy2(BUILD_OUT / f"{name}.exe", DIST / f"{name}.exe")

    for html in ROOT.glob("*.html"):
        shutil.copy2(html, DIST / html.name)

    shutil.copytree(ROOT / "css", DIST / "css", dirs_exist_ok=True)

    (DIST / "js").mkdir(exist_ok=True)
    # Skip artifactData.js / dbInfoData.js — regenerated client-data caches,
    # never ship whatever happens to be sitting in the dev checkout.
    skip_js = {"artifactData.js", "dbInfoData.js"}
    for js in (ROOT / "js").glob("*.js"):
        if js.name in skip_js:
            continue
        shutil.copy2(js, DIST / "js" / js.name)

    shutil.copy2(ROOT / "config.json.example", DIST / "config.json.example")

    (DIST / "data").mkdir(exist_ok=True)
    for example in (ROOT / "data").glob("*.example"):
        shutil.copy2(example, DIST / "data" / example.name)

    (DIST / "README_TESTER.txt").write_text(README_TEXT, encoding="utf-8")

    print(f"\nDone. Hand the whole folder to the tester:\n  {DIST}")


def main() -> int:
    for entry, name in BUILDS:
        run_pyinstaller(entry, name)
    assemble_dist()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
