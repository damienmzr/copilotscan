"""
basic_scan.py — Minimal CopilotScan example

Full tenant scan using Device Code Flow. Writes the report to ./report.html.

Prerequisites:
    pip install copilotscan

Usage:
    export COPILOTSCAN_CLIENT_ID="your-client-id"
    export COPILOTSCAN_TENANT_ID="your-tenant-id"
    python examples/basic_scan.py
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    if not os.getenv("COPILOTSCAN_CLIENT_ID") or not os.getenv("COPILOTSCAN_TENANT_ID"):
        print(
            "Error: set COPILOTSCAN_CLIENT_ID and COPILOTSCAN_TENANT_ID before running.\n"
            "  export COPILOTSCAN_CLIENT_ID='your-client-id'\n"
            "  export COPILOTSCAN_TENANT_ID='your-tenant-id'"
        )
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, "-m", "copilotscan", "--output", "report.html", "--verbose"],
        check=False,
    )

    if result.returncode == 0:
        print("\n✅ Report written to report.html")
    else:
        print(f"\n❌ CopilotScan exited with code {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
