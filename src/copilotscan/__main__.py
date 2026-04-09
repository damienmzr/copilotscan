"""
__main__.py — CopilotScan CLI entry point.

Usage:
    python -m copilotscan [OPTIONS]
"""

from __future__ import annotations

import sys

import click

from copilotscan import __version__


@click.command()
@click.option("--config", default="config.yaml", show_default=True, help="Path to config.yaml.")
@click.option("--output", default="report.html", show_default=True, help="Output HTML report path.")
@click.option(
    "--flow",
    default="device_code",
    show_default=True,
    type=click.Choice(["device_code", "client_credentials"]),
    help="Authentication flow.",
)
@click.option("--no-purview", is_flag=True, default=False, help="Skip Purview audit log collection.")
@click.option("--inactivity-days", default=90, show_default=True, help="Inactivity threshold in days.")
@click.option("--timeout-minutes", default=30, show_default=True, help="Purview polling timeout in minutes.")
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging.")
@click.version_option(__version__, prog_name="copilotscan")
def cli(
    config: str,
    output: str,
    flow: str,
    no_purview: bool,
    inactivity_days: int,
    timeout_minutes: int,
    verbose: bool,
) -> None:
    """Audit Microsoft 365 Copilot agents via the Microsoft Graph API."""
    import logging

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    click.echo(f"CopilotScan v{__version__}")
    click.echo(f"Config : {config}")
    click.echo(f"Output : {output}")
    click.echo(f"Flow   : {flow}")
    click.echo("")
    click.echo("⚙️  Core modules not yet implemented — see src/copilotscan/")
    sys.exit(0)


if __name__ == "__main__":
    cli()
