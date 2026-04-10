"""
report_generator.py — CopilotScan HTML audit report
=====================================================
Produces a single standalone HTML file from a completed scan.

Input  : tenant_name (str), scan_date (datetime), agents (list[Agent])
         Agents must already have risk_flags populated by risk_engine.evaluate_all().
Output : single .html file — no external dependencies except Chart.js CDN

Usage
-----
    from copilotscan.report_generator import ReportGenerator
    from copilotscan.risk_engine import evaluate_all

    agents = evaluate_all(agents)
    rg = ReportGenerator(tenant_name="Contoso", scan_date=datetime.now(UTC), agents=agents)
    rg.generate("report.html")
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from copilotscan import __version__
from copilotscan.models import Agent, AgentOrigin, RiskLevel
from copilotscan.risk_engine import classify_origin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Badge colour maps  (Tailwind-style class names rendered inline in template)
# ---------------------------------------------------------------------------

RISK_BADGE_COLOR: dict[str, str] = {
    RiskLevel.HIGH.value:   "#dc2626",   # red-600
    RiskLevel.MEDIUM.value: "#ea580c",   # orange-600
    RiskLevel.LOW.value:    "#ca8a04",   # yellow-600
    RiskLevel.INFO.value:   "#6b7280",   # gray-500
    "none":                 "#16a34a",   # green-600
}

ORIGIN_BADGE_COLOR: dict[str, str] = {
    AgentOrigin.AGENT_BUILDER.value:      "#dc2626",
    AgentOrigin.SHAREPOINT_AGENT.value:   "#dc2626",
    AgentOrigin.COPILOT_STUDIO.value:     "#ea580c",
    AgentOrigin.PRO_CODE.value:           "#ea580c",
    AgentOrigin.MICROSOFT_PREBUILT.value: "#16a34a",
    AgentOrigin.UNKNOWN.value:            "#6b7280",
}

DATA_SOURCE_BADGE_COLOR: dict[str, str] = {
    "graph":           "#16a34a",
    "purview":         "#2563eb",
    "purview-inferred":"#ea580c",
    "unavailable":     "#6b7280",
}


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """
    Renders a standalone HTML audit report for a CopilotScan run.

    Args:
        tenant_name : Display name of the scanned Microsoft 365 tenant.
        scan_date   : UTC datetime when the scan was performed.
        agents      : Agents with risk_flags already populated.
        version     : Package version string (defaults to copilotscan.__version__).
    """

    # Path to the Jinja2 templates directory (sibling of this file)
    _TEMPLATES_DIR = Path(__file__).parent / "templates"

    def __init__(
        self,
        tenant_name: str,
        scan_date: datetime,
        agents: list[Agent],
        version: Optional[str] = None,
    ) -> None:
        self.tenant_name = tenant_name
        self.scan_date   = scan_date.astimezone(timezone.utc)
        self.agents      = agents
        self.version     = version or __version__

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, output_path: str) -> None:
        """
        Render the HTML report and write it to *output_path*.

        Creates parent directories if they do not exist.
        """
        stats       = self._compute_stats()
        agents_data = [self._serialize_agent(a) for a in self.agents]

        env = Environment(
            loader=FileSystemLoader(str(self._TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("report_template.html")

        html = template.render(
            tenant_name   = self.tenant_name,
            scan_date     = self.scan_date.strftime("%Y-%m-%d %H:%M UTC"),
            version       = self.version,
            stats         = stats,
            agents        = agents_data,
            agents_json   = json.dumps(agents_data, ensure_ascii=False),
            risk_colors   = RISK_BADGE_COLOR,
            origin_colors = ORIGIN_BADGE_COLOR,
            ds_colors     = DATA_SOURCE_BADGE_COLOR,
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        logger.info("ReportGenerator: report written to %s (%d bytes)", out, len(html))

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def _compute_stats(self) -> dict[str, Any]:
        total           = len(self.agents)
        high_risk_count = sum(
            1 for a in self.agents
            if any(f.level == RiskLevel.HIGH for f in a.risk_flags)
        )
        orphan_count = sum(
            1 for a in self.agents
            if any(f.rule_id == "ORPHAN" for f in a.risk_flags)
        )
        shared_count  = sum(1 for a in self.agents if a.is_org_scoped)
        private_count = total - shared_count

        origin_counts: Counter[str] = Counter()
        for agent in self.agents:
            origin_counts[classify_origin(agent).value] += 1

        # Highest risk level across all flags for each agent
        risk_level_counts: Counter[str] = Counter()
        for agent in self.agents:
            if not agent.risk_flags:
                risk_level_counts["none"] += 1
            else:
                worst = max(
                    agent.risk_flags,
                    key=lambda f: ["INFO", "LOW", "MEDIUM", "HIGH"].index(f.level.value),
                )
                risk_level_counts[worst.level.value] += 1

        return {
            "total_agents":     total,
            "high_risk_count":  high_risk_count,
            "orphan_count":     orphan_count,
            "shared_count":     shared_count,
            "private_count":    private_count,
            "agents_by_origin": dict(origin_counts),
            "agents_by_risk":   dict(risk_level_counts),
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _serialize_agent(self, agent: Agent) -> dict[str, Any]:
        """Convert an Agent to a JSON-serialisable dict for the Jinja2 template."""
        origin = classify_origin(agent)

        # Worst risk level
        if not agent.risk_flags:
            worst_level = "none"
        else:
            worst_level = max(
                agent.risk_flags,
                key=lambda f: ["INFO", "LOW", "MEDIUM", "HIGH"].index(f.level.value),
            ).level.value

        last_modified = (
            agent.last_modified_datetime.strftime("%Y-%m-%d")
            if agent.last_modified_datetime
            else "—"
        )
        last_interaction = (
            agent.purview_last_interaction.strftime("%Y-%m-%d")
            if agent.purview_last_interaction
            else "—"
        )

        publisher_name = (
            (agent.publisher or {}).get("displayName") or "—"
        )

        flags = [
            {
                "rule_id":     f.rule_id,
                "level":       f.level.value,
                "message_en":  f.message_en,
                "message_fr":  f.message_fr,
                "data_source": f.data_source,
                "color":       RISK_BADGE_COLOR.get(f.level.value, "#6b7280"),
                "ds_color":    DATA_SOURCE_BADGE_COLOR.get(f.data_source, "#6b7280"),
            }
            for f in agent.risk_flags
        ]

        return {
            "id":                agent.id,
            "display_name":      agent.display_name,
            "agent_type":        agent.agent_type,
            "element_types":     agent.element_types,
            "is_blocked":        agent.is_blocked,
            "is_org_scoped":     agent.is_org_scoped,
            "publisher":         publisher_name,
            "version":           agent.version or "—",
            "last_modified":     last_modified,
            "last_interaction":  last_interaction,
            "origin":            origin.value,
            "origin_color":      ORIGIN_BADGE_COLOR.get(origin.value, "#6b7280"),
            "worst_level":       worst_level,
            "worst_color":       RISK_BADGE_COLOR.get(worst_level, "#16a34a"),
            "flags":             flags,
            "knowledge_sources": agent.purview_top_knowledge_sources,
        }
