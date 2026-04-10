"""
tests/test_risk_engine.py — Unit tests for the CopilotScan risk engine.

Tests cover each of the 6 rules and each AgentOrigin classification.
No live Microsoft Graph / Purview calls — all Agent objects are built in-process.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from copilotscan.models import (
    Agent,
    AgentOrigin,
    KnowledgeSource,
    RiskFlag,
    RiskLevel,
)
from copilotscan.risk_engine import classify_origin, evaluate

# ---------------------------------------------------------------------------
# Shared factory
# ---------------------------------------------------------------------------


def _make_agent(
    element_types: list[str] | None = None,
    available_to: list[dict] | None = None,
    publisher: dict | None = None,
    purview_last_interaction: datetime | None = None,
    purview_top_knowledge_sources: list[KnowledgeSource] | None = None,
    **kwargs,
) -> Agent:
    """Build an Agent with sensible defaults; override per-test as needed."""
    return Agent(
        id=kwargs.get("id", "agent-test-001"),
        display_name=kwargs.get("display_name", "Test Agent"),
        element_types=element_types if element_types is not None else ["DeclarativeAgent"],
        agent_type=kwargs.get("agent_type", "Custom"),
        is_blocked=kwargs.get("is_blocked", False),
        publisher=publisher
        if publisher is not ...
        else {"displayName": "Contoso IT", "publisherType": "Organization"},
        available_to=available_to if available_to is not None else [{"type": "Organization"}],
        deployed_to=kwargs.get("deployed_to", []),
        supported_hosts=kwargs.get("supported_hosts", ["Copilot"]),
        version=kwargs.get("version", "1.0.0"),
        last_modified_datetime=kwargs.get(
            "last_modified_datetime",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        purview_last_interaction=purview_last_interaction,
        purview_top_knowledge_sources=purview_top_knowledge_sources or [],
    )


def _flags_by_rule(flags: list[RiskFlag]) -> dict[str, RiskFlag]:
    return {f.rule_id: f for f in flags}


# ---------------------------------------------------------------------------
# Rule 1 — INACTIVE
# ---------------------------------------------------------------------------


class TestInactiveRule:
    def test_fires_when_last_activity_older_than_threshold(self) -> None:
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=91),
        )
        flags = evaluate(agent, inactivity_days=90)
        by_rule = _flags_by_rule(flags)
        assert "INACTIVE" in by_rule
        assert by_rule["INACTIVE"].level == RiskLevel.MEDIUM

    def test_fires_when_no_purview_interaction_at_all(self) -> None:
        agent = _make_agent(purview_last_interaction=None)
        flags = evaluate(agent, inactivity_days=90)
        assert any(f.rule_id == "INACTIVE" for f in flags)

    def test_does_not_fire_when_recently_active(self) -> None:
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=10),
        )
        flags = evaluate(agent, inactivity_days=90)
        assert all(f.rule_id != "INACTIVE" for f in flags)

    def test_boundary_exactly_at_threshold_does_not_fire(self) -> None:
        # 89 days ago is within the window
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=89),
        )
        flags = evaluate(agent, inactivity_days=90)
        assert all(f.rule_id != "INACTIVE" for f in flags)


# ---------------------------------------------------------------------------
# Rule 2 — ORPHAN
# ---------------------------------------------------------------------------


class TestOrphanRule:
    def test_fires_when_org_scoped_and_no_publisher(self) -> None:
        agent = _make_agent(
            available_to=[{"type": "Organization"}],
            publisher=None,
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "ORPHAN" in by_rule
        assert by_rule["ORPHAN"].level == RiskLevel.HIGH

    def test_does_not_fire_when_individual_scope(self) -> None:
        agent = _make_agent(
            available_to=[{"type": "User"}],
            publisher=None,
        )
        flags = evaluate(agent)
        assert all(f.rule_id != "ORPHAN" for f in flags)

    def test_does_not_fire_when_publisher_present(self) -> None:
        agent = _make_agent(
            available_to=[{"type": "Organization"}],
            publisher={"displayName": "Jane Doe", "publisherType": "User"},
        )
        flags = evaluate(agent)
        assert all(f.rule_id != "ORPHAN" for f in flags)


# ---------------------------------------------------------------------------
# Rule 3 — SENSITIVE_KNOWLEDGE
# ---------------------------------------------------------------------------


class TestSensitiveKnowledgeRule:
    def test_fires_when_source_contains_everyone(self) -> None:
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=5),
            purview_top_knowledge_sources=[
                "https://contoso.sharepoint.com/sites/everyone/documents"
            ],
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "SENSITIVE_KNOWLEDGE" in by_rule
        assert by_rule["SENSITIVE_KNOWLEDGE"].level == RiskLevel.HIGH

    def test_fires_when_source_contains_external(self) -> None:
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=5),
            purview_top_knowledge_sources=[
                "https://contoso.sharepoint.com/sites/external-partners"
            ],
        )
        flags = evaluate(agent)
        assert any(f.rule_id == "SENSITIVE_KNOWLEDGE" for f in flags)

    def test_does_not_fire_for_internal_source(self) -> None:
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=5),
            purview_top_knowledge_sources=["https://contoso.sharepoint.com/sites/hr/documents"],
        )
        flags = evaluate(agent)
        assert all(f.rule_id != "SENSITIVE_KNOWLEDGE" for f in flags)


# ---------------------------------------------------------------------------
# Rule 4 — KNOWLEDGE_UNKNOWN
# ---------------------------------------------------------------------------


class TestKnowledgeUnknownRule:
    def test_fires_when_no_purview_data(self) -> None:
        agent = _make_agent(
            purview_last_interaction=None,
            purview_top_knowledge_sources=[],
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "KNOWLEDGE_UNKNOWN" in by_rule
        assert by_rule["KNOWLEDGE_UNKNOWN"].level == RiskLevel.LOW

    def test_does_not_fire_when_purview_data_present(self) -> None:
        agent = _make_agent(
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=5),
            purview_top_knowledge_sources=["https://contoso.sharepoint.com/sites/hr"],
        )
        flags = evaluate(agent)
        assert all(f.rule_id != "KNOWLEDGE_UNKNOWN" for f in flags)


# ---------------------------------------------------------------------------
# Rule 5 — AGENT_NOT_AUDITED
# ---------------------------------------------------------------------------


class TestAgentNotAuditedRule:
    def test_fires_for_microsoft_prebuilt(self) -> None:
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            publisher={"displayName": "Microsoft", "publisherType": "Microsoft"},
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "AGENT_NOT_AUDITED" in by_rule
        assert by_rule["AGENT_NOT_AUDITED"].level == RiskLevel.INFO

    def test_fires_for_sharepoint_agent(self) -> None:
        agent = _make_agent(element_types=["SharePointAgent"])
        flags = evaluate(agent)
        assert any(f.rule_id == "AGENT_NOT_AUDITED" for f in flags)

    def test_does_not_fire_for_copilot_studio(self) -> None:
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            available_to=[{"type": "Organization"}],
            publisher={"displayName": "Studio Team", "publisherType": "Organization"},
        )
        flags = evaluate(agent)
        assert all(f.rule_id != "AGENT_NOT_AUDITED" for f in flags)


# ---------------------------------------------------------------------------
# Rule 6 — ORIGIN_RISK  (one test per AgentOrigin)
# ---------------------------------------------------------------------------


class TestOriginRiskRule:
    def test_agent_builder_is_high(self) -> None:
        # DeclarativeAgent + individual scope + non-Microsoft publisher → AGENT_BUILDER
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            available_to=[{"type": "User"}],
            publisher={"displayName": "John User", "publisherType": "User"},
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "ORIGIN_RISK" in by_rule
        assert by_rule["ORIGIN_RISK"].level == RiskLevel.HIGH

    def test_sharepoint_agent_is_high(self) -> None:
        agent = _make_agent(element_types=["SharePointAgent"])
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "ORIGIN_RISK" in by_rule
        assert by_rule["ORIGIN_RISK"].level == RiskLevel.HIGH

    def test_copilot_studio_is_medium(self) -> None:
        # DeclarativeAgent + org scope + non-Microsoft publisher → COPILOT_STUDIO
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            available_to=[{"type": "Organization"}],
            publisher={"displayName": "Studio Team", "publisherType": "Organization"},
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "ORIGIN_RISK" in by_rule
        assert by_rule["ORIGIN_RISK"].level == RiskLevel.MEDIUM

    def test_microsoft_prebuilt_is_info(self) -> None:
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            publisher={"displayName": "Microsoft", "publisherType": "Microsoft"},
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "ORIGIN_RISK" in by_rule
        assert by_rule["ORIGIN_RISK"].level == RiskLevel.INFO

    def test_unknown_origin_is_low(self) -> None:
        agent = _make_agent(
            element_types=["SomeUnknownType"],
            publisher={"displayName": "Unknown Corp", "publisherType": "Unknown"},
        )
        flags = evaluate(agent)
        by_rule = _flags_by_rule(flags)
        assert "ORIGIN_RISK" in by_rule
        assert by_rule["ORIGIN_RISK"].level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# classify_origin — one test per AgentOrigin
# ---------------------------------------------------------------------------


class TestClassifyOrigin:
    def test_sharepoint_agent(self) -> None:
        agent = _make_agent(element_types=["SharePointAgent"])
        assert classify_origin(agent) == AgentOrigin.SHAREPOINT_AGENT

    def test_microsoft_prebuilt(self) -> None:
        agent = _make_agent(
            publisher={"displayName": "Microsoft", "publisherType": "Microsoft"},
        )
        assert classify_origin(agent) == AgentOrigin.MICROSOFT_PREBUILT

    def test_agent_builder(self) -> None:
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            available_to=[{"type": "User"}],
            publisher={"displayName": "Some User", "publisherType": "User"},
        )
        assert classify_origin(agent) == AgentOrigin.AGENT_BUILDER

    def test_copilot_studio(self) -> None:
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            available_to=[{"type": "Organization"}],
            publisher={"displayName": "Maker", "publisherType": "Organization"},
        )
        assert classify_origin(agent) == AgentOrigin.COPILOT_STUDIO

    def test_unknown(self) -> None:
        agent = _make_agent(
            element_types=["SomeOtherType"],
            publisher={"displayName": "Corp", "publisherType": "Organization"},
        )
        assert classify_origin(agent) == AgentOrigin.UNKNOWN


# ---------------------------------------------------------------------------
# No false positives — clean agent should generate zero HIGH flags
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    def test_clean_copilot_studio_agent_has_no_high_flags(self) -> None:
        """
        A well-governed Copilot Studio agent:
          - org-scoped with a named publisher
          - recently active in Purview (within 30 days)
          - only internal knowledge sources

        Expected: no HIGH flags (ORIGIN_RISK fires at MEDIUM, nothing higher).
        """
        agent = _make_agent(
            element_types=["DeclarativeAgent"],
            available_to=[{"type": "Organization"}],
            publisher={"displayName": "Contoso IT", "publisherType": "Organization"},
            purview_last_interaction=datetime.now(timezone.utc) - timedelta(days=30),
            purview_top_knowledge_sources=[
                "https://contoso.sharepoint.com/sites/hr/Shared%20Documents"
            ],
        )
        flags = evaluate(agent, inactivity_days=90)
        high_flags = [f for f in flags if f.level == RiskLevel.HIGH]
        assert high_flags == [], f"Unexpected HIGH flags: {[f.rule_id for f in high_flags]}"

    def test_evaluate_all_populates_risk_flags(self) -> None:
        """evaluate_all() must write flags back to Agent.risk_flags."""
        from copilotscan.risk_engine import evaluate_all

        agents = [
            _make_agent(id="a1"),
            _make_agent(id="a2"),
        ]
        result = evaluate_all(agents)
        assert result is agents
        for agent in agents:
            assert isinstance(agent.risk_flags, list)
            assert len(agent.risk_flags) > 0
