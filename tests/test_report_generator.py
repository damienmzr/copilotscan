"""
tests/test_report_generator.py — Unit tests for ReportGenerator.

Covers:
  - _compute_stats()  : KPI counts match the fixture agents
  - _serialize_agent(): all expected fields present, badge colours correct
  - generate()        : file written, non-empty, HTML contains tenant name

No network calls, no live Graph/Purview — all Agent objects built in-process.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from copilotscan.models import Agent, AgentOrigin, RiskLevel
from copilotscan.report_generator import (
    DATA_SOURCE_BADGE_COLOR,
    ORIGIN_BADGE_COLOR,
    RISK_BADGE_COLOR,
    ReportGenerator,
)
from copilotscan.risk_engine import evaluate_all

# ---------------------------------------------------------------------------
# UTC shorthand
# ---------------------------------------------------------------------------

UTC = timezone.utc
NOW = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Factory — mirrors _make_agent in test_risk_engine.py
# ---------------------------------------------------------------------------

def _agent(
    id: str,
    name: str,
    element_types: list[str] | None = None,
    available_to: list[dict] | None = None,
    publisher: dict | None = None,
    days_since_activity: int | None = None,
    knowledge_sources: list[str] | None = None,
    is_blocked: bool = False,
) -> Agent:
    return Agent(
        id=id,
        display_name=name,
        element_types=element_types or ["DeclarativeAgent"],
        agent_type="Custom",
        is_blocked=is_blocked,
        publisher=publisher,
        available_to=available_to or [{"type": "Organization"}],
        deployed_to=[],
        supported_hosts=["Copilot"],
        version="1.0",
        last_modified_datetime=datetime(2026, 1, 1, tzinfo=UTC),
        purview_last_interaction=(
            NOW - timedelta(days=days_since_activity)
            if days_since_activity is not None else None
        ),
        purview_top_knowledge_sources=knowledge_sources or [],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_agents() -> list[Agent]:
    """
    5 agents covering different origins and risk situations:
      - a1 : Copilot Studio, named publisher, recently active, internal KS → no HIGH
      - a2 : Orphan (org-scoped, no publisher), inactive 200 days
      - a3 : Agent Builder (individual scope), sensitive KS ('everyone')
      - a4 : Microsoft Prebuilt
      - a5 : SharePoint Agent, blocked
    """
    agents = [
        _agent(
            "a1", "HR Assistant",
            available_to=[{"type": "Organization"}],
            publisher={"displayName": "Contoso IT", "publisherType": "Organization"},
            days_since_activity=10,
            knowledge_sources=["https://contoso.sharepoint.com/sites/hr"],
        ),
        _agent(
            "a2", "Orphan Bot",
            available_to=[{"type": "Organization"}],
            publisher=None,
            days_since_activity=200,
        ),
        _agent(
            "a3", "External Assist",
            available_to=[{"type": "User"}],
            publisher={"displayName": "Some User", "publisherType": "User"},
            days_since_activity=5,
            knowledge_sources=["https://contoso.sharepoint.com/sites/everyone"],
        ),
        _agent(
            "a4", "MS Search",
            publisher={"displayName": "Microsoft", "publisherType": "Microsoft"},
        ),
        _agent(
            "a5", "SP Agent",
            element_types=["SharePointAgent"],
            publisher={"displayName": "Auto", "publisherType": "Organization"},
            is_blocked=True,
        ),
    ]
    return evaluate_all(agents, inactivity_days=90)


@pytest.fixture()
def generator(sample_agents: list[Agent]) -> ReportGenerator:
    return ReportGenerator(
        tenant_name="Contoso",
        scan_date=NOW,
        agents=sample_agents,
        version="0.1.0-test",
    )


# ---------------------------------------------------------------------------
# _compute_stats()
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_total_agents(self, generator: ReportGenerator) -> None:
        stats = generator._compute_stats()
        assert stats["total_agents"] == 5

    def test_high_risk_count(self, generator: ReportGenerator) -> None:
        stats = generator._compute_stats()
        # a2 (ORPHAN) and a3 (SENSITIVE_KNOWLEDGE + AGENT_BUILDER origin) are HIGH
        assert stats["high_risk_count"] >= 2

    def test_orphan_count(self, generator: ReportGenerator) -> None:
        stats = generator._compute_stats()
        # Only a2 triggers ORPHAN rule
        assert stats["orphan_count"] == 1

    def test_shared_vs_private_counts(self, generator: ReportGenerator) -> None:
        stats = generator._compute_stats()
        # a1, a2, a4, a5 are org-scoped; a3 is user-scoped
        assert stats["shared_count"] == 4
        assert stats["private_count"] == 1
        assert stats["shared_count"] + stats["private_count"] == 5

    def test_agents_by_origin_keys(self, generator: ReportGenerator) -> None:
        stats = generator._compute_stats()
        by_origin = stats["agents_by_origin"]
        # Total across all origins must equal total_agents
        assert sum(by_origin.values()) == 5

    def test_agents_by_risk_sums_to_total(self, generator: ReportGenerator) -> None:
        stats = generator._compute_stats()
        assert sum(stats["agents_by_risk"].values()) == 5


# ---------------------------------------------------------------------------
# _serialize_agent()
# ---------------------------------------------------------------------------

class TestSerializeAgent:
    REQUIRED_FIELDS = [
        "id", "display_name", "agent_type", "element_types",
        "is_blocked", "is_org_scoped", "publisher", "version",
        "last_modified", "last_interaction", "origin", "origin_color",
        "worst_level", "worst_color", "flags", "knowledge_sources",
    ]

    def _get_agent(self, generator: ReportGenerator, agent_id: str) -> dict:
        agent = next(a for a in generator.agents if a.id == agent_id)
        return generator._serialize_agent(agent)

    def test_all_required_fields_present(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a1")
        for field in self.REQUIRED_FIELDS:
            assert field in data, f"Missing field: {field}"

    def test_clean_agent_worst_level_is_medium_or_lower(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a1")
        # a1 is a governed Copilot Studio agent — no HIGH flag expected
        assert data["worst_level"] != RiskLevel.HIGH.value

    def test_orphan_worst_level_is_high(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a2")
        assert data["worst_level"] == RiskLevel.HIGH.value

    def test_worst_color_matches_risk_badge_map(self, generator: ReportGenerator) -> None:
        for agent in generator.agents:
            data = generator._serialize_agent(agent)
            expected_color = RISK_BADGE_COLOR.get(data["worst_level"], "#16a34a")
            assert data["worst_color"] == expected_color

    def test_origin_color_matches_origin_badge_map(self, generator: ReportGenerator) -> None:
        for agent in generator.agents:
            data = generator._serialize_agent(agent)
            assert data["origin_color"] == ORIGIN_BADGE_COLOR.get(data["origin"], "#6b7280")

    def test_flags_contain_required_keys(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a2")
        assert len(data["flags"]) > 0
        for flag in data["flags"]:
            for key in ("rule_id", "level", "message_en", "message_fr", "data_source", "color", "ds_color"):
                assert key in flag, f"Flag missing key: {key}"

    def test_flag_color_matches_risk_badge_map(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a2")
        for flag in data["flags"]:
            assert flag["color"] == RISK_BADGE_COLOR.get(flag["level"], "#6b7280")

    def test_flag_ds_color_matches_data_source_map(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a2")
        for flag in data["flags"]:
            assert flag["ds_color"] == DATA_SOURCE_BADGE_COLOR.get(flag["data_source"], "#6b7280")

    def test_blocked_agent_serializes_correctly(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a5")
        assert data["is_blocked"] is True

    def test_knowledge_sources_preserved(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a1")
        assert "https://contoso.sharepoint.com/sites/hr" in data["knowledge_sources"]

    def test_microsoft_origin(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a4")
        assert data["origin"] == AgentOrigin.MICROSOFT_PREBUILT.value

    def test_sharepoint_origin(self, generator: ReportGenerator) -> None:
        data = self._get_agent(generator, "a5")
        assert data["origin"] == AgentOrigin.SHAREPOINT_AGENT.value


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_file_is_created(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        assert out.exists()

    def test_file_is_non_empty(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        assert out.stat().st_size > 1000   # at minimum several KB

    def test_tenant_name_in_output(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        html = out.read_text(encoding="utf-8")
        assert "Contoso" in html

    def test_version_in_output(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        html = out.read_text(encoding="utf-8")
        assert "0.1.0-test" in html

    def test_all_agent_names_in_output(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        html = out.read_text(encoding="utf-8")
        for agent in generator.agents:
            assert agent.display_name in html, f"Agent '{agent.display_name}' not found in HTML"

    def test_no_jinja2_placeholders_remain(self, generator: ReportGenerator, tmp_path: Path) -> None:
        """Verify Jinja2 rendered all variables — no {{ }} left in output."""
        out = tmp_path / "report.html"
        generator.generate(str(out))
        html = out.read_text(encoding="utf-8")
        assert "{{" not in html
        assert "}}" not in html

    def test_creates_parent_directories(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "deep" / "report.html"
        generator.generate(str(out))
        assert out.exists()

    def test_scan_date_in_output(self, generator: ReportGenerator, tmp_path: Path) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        html = out.read_text(encoding="utf-8")
        assert "2026-04-10" in html

    def test_high_risk_banner_present_when_applicable(
        self, generator: ReportGenerator, tmp_path: Path
    ) -> None:
        out = tmp_path / "report.html"
        generator.generate(str(out))
        html = out.read_text(encoding="utf-8")
        # The fixture has at least 2 HIGH agents → banner must appear
        assert "risk-banner" in html

    def test_empty_agent_list_renders_without_error(self, tmp_path: Path) -> None:
        rg = ReportGenerator("Empty Corp", NOW, agents=[])
        out = tmp_path / "empty.html"
        rg.generate(str(out))
        html = out.read_text(encoding="utf-8")
        assert "Empty Corp" in html
        assert "{{" not in html
