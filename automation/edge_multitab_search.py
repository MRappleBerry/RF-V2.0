"""Compatibility entrypoint for the dashboard automation workflow.

This delegates to function/tools.py so both legacy and current commands use the
same Purview export path and db_info.txt container.
"""

from __future__ import annotations

import os
import sys


WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from function.tools import main


if __name__ == "__main__":
    raise SystemExit(main())
