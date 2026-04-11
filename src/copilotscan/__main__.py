"""
__main__.py — CopilotScan CLI entry point.

Usage:
    python -m copilotscan [OPTIONS]
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml

from copilotscan import __version__

_AUTH_MODE_MAP = {
    "device-code": "DEVICE_CODE",
    "app-secret": "APP_SECRET",
    "app-cert": "APP_CERT",
}


@click.command()
@click.option("--config", default="config.yaml", show_default=True, help="Path to config.yaml.")
@click.option("--output", default=None, help="Output HTML report path (overrides config.yaml).")
@click.option(
    "--flow",
    default=None,
    type=click.Choice(["device_code", "client_credentials"]),
    help="Authentication flow override (device_code or client_credentials).",
)
@click.option(
    "--no-purview", is_flag=True, default=False, help="Skip Purview audit log collection."
)
@click.option(
    "--demo",
    is_flag=True,
    default=False,
    help="Use synthetic demo agents (no auth/Graph required). Useful on tenants without an M365 Copilot licence.",
)
@click.option(
    "--inactivity-days",
    default=None,
    type=int,
    help="Inactivity threshold in days (overrides config.yaml).",
)
@click.option(
    "--timeout-minutes", default=30, show_default=True, help="Purview polling timeout in minutes."
)
@click.option("--verbose", is_flag=True, default=False, help="Enable debug logging.")
@click.version_option(__version__, prog_name="copilotscan")
def cli(
    config: str,
    output: str | None,
    flow: str | None,
    no_purview: bool,
    demo: bool,
    inactivity_days: int | None,
    timeout_minutes: int,
    verbose: bool,
) -> None:
    """Audit Microsoft 365 Copilot agents via the Microsoft Graph API."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    click.echo(f"CopilotScan v{__version__}")

    # ── Load YAML config ──────────────────────────────────────────────
    config_path = Path(config)
    raw_config: dict = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            raw_config = yaml.safe_load(fh) or {}
    else:
        click.echo(f"⚠️  Config file not found: {config}", err=True)

    auth_cfg: dict = raw_config.get("auth", {})
    scan_cfg: dict = raw_config.get("scan", {})
    report_cfg: dict = raw_config.get("report", {})

    # ── Resolve effective settings ────────────────────────────────────
    # --flow device_code / client_credentials overrides config auth.mode
    cfg_mode = auth_cfg.get("mode", "device-code")
    if flow == "client_credentials":
        cfg_mode = "app-secret"
    elif flow == "device_code":
        cfg_mode = "device-code"

    _default_output = report_cfg.get("output_path", "copilotscan_report.html")
    scan_date = datetime.now(timezone.utc)
    if output:
        effective_output: str = output
    else:
        _ts = scan_date.strftime("%Y%m%d_%H%M%S")
        _p = Path(_default_output)
        effective_output = str(_p.with_name(f"{_p.stem}_{_ts}{_p.suffix}"))
    effective_inactivity: int = (
        inactivity_days if inactivity_days is not None else scan_cfg.get("inactivity_days", 90)
    )
    include_purview: bool = (not no_purview) and scan_cfg.get("include_purview", True)
    tenant_name: str = report_cfg.get("tenant_name") or auth_cfg.get("tenant_id", "Unknown Tenant")

    # ── Demo mode (no auth, no Graph) ─────────────────────────────────
    if demo:
        agents = _build_demo_agents()
        click.echo(f"🎭  Demo mode — {len(agents)} synthetic agent(s) loaded (no Graph call made).")
        from copilotscan.risk_engine import evaluate_all  # noqa: PLC0415

        agents = evaluate_all(agents, inactivity_days=effective_inactivity)
        total_flags = sum(len(a.risk_flags) for a in agents)
        click.echo(f"⚙️   Risk evaluation: {total_flags} flag(s) across {len(agents)} agent(s).")
        from copilotscan.report_generator import ReportGenerator  # noqa: PLC0415

        click.echo(f"📄  Generating report → {effective_output}")
        try:
            rg = ReportGenerator(
                tenant_name=tenant_name
                if tenant_name and tenant_name != auth_cfg.get("tenant_id")
                else "Demo Tenant",
                scan_date=scan_date,
                agents=agents,
            )
            rg.generate(effective_output)
        except Exception as exc:
            click.echo(f"❌  Report generation failed: {exc}", err=True)
            sys.exit(1)
        click.echo(f"✅  Report saved to {effective_output}")
        return

    # ── Authenticate ──────────────────────────────────────────────────
    from copilotscan.auth import (  # noqa: PLC0415
        AuthConfig,
        AuthConfigError,
        AuthMode,
        CopilotScanAuthenticator,
    )

    mode_enum = AuthMode(cfg_mode)  # "device-code" etc. match AuthMode values

    cert_path_val = auth_cfg.get("cert_path")
    cert_path_resolved = Path(cert_path_val).expanduser().resolve() if cert_path_val else None

    try:
        auth_config = AuthConfig(
            client_id=auth_cfg.get("client_id", ""),
            tenant_id=auth_cfg.get("tenant_id", ""),
            client_secret=auth_cfg.get("client_secret"),
            cert_path=cert_path_resolved,
            cert_thumbprint=auth_cfg.get("cert_thumb") or auth_cfg.get("cert_thumbprint"),
            auth_mode=mode_enum,
        )
        authenticator = CopilotScanAuthenticator(auth_config)
        token = authenticator.acquire_token()
    except AuthConfigError as exc:
        click.echo(f"❌  Authentication configuration error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"❌  Authentication failed: {exc}", err=True)
        sys.exit(1)

    auth_header = token.authorization_header
    click.echo("✅  Authenticated.")

    # ── Collect agents from Graph ─────────────────────────────────────
    from copilotscan.collectors.graph import GraphCollector  # noqa: PLC0415

    click.echo("🔍  Collecting Copilot agents from Microsoft Graph…")
    try:
        graph = GraphCollector(authorization_header=auth_header)
        agents = graph.collect()
    except Exception as exc:
        click.echo(f"❌  Graph collection failed: {exc}", err=True)
        sys.exit(1)

    click.echo(f"   Found {len(agents)} agent(s).")

    # ── Enrich with Purview data ──────────────────────────────────────
    if include_purview:
        from copilotscan.collectors.purview import PurviewCollector  # noqa: PLC0415
        from copilotscan.exceptions import AuditQueryTimeout  # noqa: PLC0415

        click.echo("📋  Querying Purview audit log (this may take several minutes)…")
        try:
            purview = PurviewCollector(
                authorization_header=auth_header,
                poll_timeout_minutes=timeout_minutes,
            )
            purview_map = purview.collect()
            for agent in agents:
                pd = purview_map.get(agent.id)
                if pd:
                    agent.purview_last_interaction = pd.last_interaction
                    agent.purview_top_knowledge_sources = pd.top_knowledge_sources
            click.echo(f"   Purview data retrieved for {len(purview_map)} agent(s).")
        except AuditQueryTimeout as exc:
            click.echo(f"⚠️   Purview query timed out: {exc}", err=True)
            click.echo("   Continuing with Graph data only.", err=True)
        except Exception as exc:
            click.echo(f"⚠️   Purview collection failed: {exc}", err=True)
            click.echo("   Continuing with Graph data only.", err=True)
    else:
        click.echo("⏭️   Skipping Purview collection (--no-purview).")

    # ── Risk evaluation ───────────────────────────────────────────────
    from copilotscan.risk_engine import evaluate_all  # noqa: PLC0415

    click.echo("⚙️   Running risk evaluation…")
    agents = evaluate_all(agents, inactivity_days=effective_inactivity)
    total_flags = sum(len(a.risk_flags) for a in agents)
    click.echo(f"   {total_flags} flag(s) across {len(agents)} agent(s).")

    # ── Generate report ───────────────────────────────────────────────
    from copilotscan.report_generator import ReportGenerator  # noqa: PLC0415

    click.echo(f"📄  Generating report → {effective_output}")
    try:
        rg = ReportGenerator(
            tenant_name=tenant_name,
            scan_date=scan_date,
            agents=agents,
        )
        rg.generate(effective_output)
    except Exception as exc:
        click.echo(f"❌  Report generation failed: {exc}", err=True)
        sys.exit(1)

    click.echo(f"✅  Report saved to {effective_output}")


# ---------------------------------------------------------------------------
# Demo helpers
# ---------------------------------------------------------------------------


def _build_demo_agents():
    """Return a representative set of synthetic Agent objects for offline testing."""
    from datetime import timedelta

    from copilotscan.models import Agent  # noqa: PLC0415

    now = datetime.now(timezone.utc)

    def _agent(
        agent_id,
        name,
        element_types,
        agent_type,
        publisher_name,
        available_to_type,
        days_modified,
        purview_days=None,
        knowledge_sources=None,
        is_blocked=False,
    ):
        last_modified = now - timedelta(days=days_modified)
        purview_ts = (now - timedelta(days=purview_days)) if purview_days is not None else None
        publisher = {"displayName": publisher_name} if publisher_name else None
        available_to = [{"type": available_to_type}] if available_to_type else []
        a = Agent(
            id=agent_id,
            display_name=name,
            element_types=element_types,
            agent_type=agent_type,
            is_blocked=is_blocked,
            publisher=publisher,
            available_to=available_to,
            deployed_to=[],
            supported_hosts=["TeamsPersonalApp"],
            version="1.0",
            last_modified_datetime=last_modified,
            purview_last_interaction=purview_ts,
            purview_top_knowledge_sources=knowledge_sources or [],
        )
        return a

    return [
        _agent(
            "demo-001",
            "HR Assistant",
            ["DeclarativeAgent"],
            "declarative",
            "Contoso HR",
            "organization",
            days_modified=10,
            purview_days=5,
            knowledge_sources=["https://contoso.sharepoint.com/sites/HR/everyone"],
        ),
        _agent(
            "demo-002",
            "Finance Bot",
            ["DeclarativeAgent"],
            "declarative",
            None,
            "organization",
            days_modified=200,
            purview_days=110,
            knowledge_sources=[],
        ),
        _agent(
            "demo-003",
            "Copilot",
            ["DeclarativeAgent"],
            "declarative",
            "Microsoft",
            "organization",
            days_modified=30,
            purview_days=2,
            knowledge_sources=[],
        ),
        _agent(
            "demo-004",
            "Sales Prospector",
            ["CustomEngineAgent"],
            "custom-engine",
            "Contoso Sales",
            "organization",
            days_modified=15,
            purview_days=7,
            knowledge_sources=["https://contoso.sharepoint.com/sites/Sales"],
        ),
        _agent(
            "demo-005",
            "Personal Study Helper",
            ["DeclarativeAgent"],
            "declarative",
            "Alice Martin",
            None,
            days_modified=45,
            purview_days=None,
            knowledge_sources=[],
        ),
        _agent(
            "demo-006",
            "SharePoint News Agent",
            ["SharePointAgent"],
            "sharepoint",
            "Contoso IT",
            "organization",
            days_modified=60,
            purview_days=None,
            knowledge_sources=[],
        ),
        _agent(
            "demo-007",
            "Blocked Legacy Bot",
            ["DeclarativeAgent"],
            "declarative",
            "Contoso Dev",
            "organization",
            days_modified=180,
            purview_days=None,
            knowledge_sources=[],
            is_blocked=True,
        ),
    ]


if __name__ == "__main__":
    cli()
