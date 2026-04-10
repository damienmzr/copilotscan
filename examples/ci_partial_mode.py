"""
ci_partial_mode.py — Client Credentials (partial) mode for CI/CD pipelines

Uses Client Credentials (app-only) flow, which covers usage reports but NOT the
detailed agent catalog: GET /catalog/packages/{id} returns 424 Failed Dependency
in app-only context (a Microsoft server-side restriction, not a CopilotScan bug).

This mode is suitable for scheduled pipelines that need tenant-level usage counts
and do not require per-agent detail or knowledge source inference.

The generated report includes a warning banner explaining the reduced coverage.

Required environment variables:
    COPILOTSCAN_CLIENT_ID
    COPILOTSCAN_TENANT_ID
    COPILOTSCAN_CLIENT_SECRET

Usage:
    python examples/ci_partial_mode.py
"""

from __future__ import annotations

import os
import subprocess
import sys

REQUIRED_ENV_VARS = [
    "COPILOTSCAN_CLIENT_ID",
    "COPILOTSCAN_TENANT_ID",
    "COPILOTSCAN_CLIENT_SECRET",
]


def main() -> None:
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        print(f"Error: missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "copilotscan",
            "--flow",
            "client_credentials",
            "--no-purview",
            "--output",
            "ci_report.html",
        ],
        check=False,
    )

    if result.returncode == 0:
        print("\n✅ Report written to ci_report.html")
        print("ℹ️  Note: per-agent detail is reduced in client_credentials mode.")
    else:
        print(f"\n❌ CopilotScan exited with code {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
