"""
tests/test_risk_engine.py — Unit tests for the CopilotScan risk engine.

Tests cover each of the 6 rules: both the firing case and the non-firing case.
No live Microsoft Graph calls — all data is synthetic dicts matching the Graph schema.
"""

from __future__ import annotations

import pytest

# NOTE: import paths will resolve once risk_engine.py is implemented.
# from copilotscan.risk_engine import evaluate, RiskFlag, RiskLevel


# ---------------------------------------------------------------------------
# Fixtures — synthetic agent data matching the Graph /catalog/packages schema
# ---------------------------------------------------------------------------


def _make_agent(**overrides: object) -> dict:
    """Return a minimal synthetic agent dict with sane defaults."""
    base: dict = {
        "id": "agent-test-001",
        "displayName": "Test Agent",
        "elementTypes": ["DeclarativeAgent"],
        "type": "Custom",
        "isBlocked": False,
        "publisher": {"displayName": "Contoso IT", "publisherType": "Organization"},
        "availableTo": [{"type": "Organization"}],
        "deployedTo": [],
        "supportedHosts": ["Copilot"],
        "version": "1.0.0",
        "lastModifiedDateTime": "2026-01-01T00:00:00Z",
        # Injected by PurviewCollector after enrichment
        "_purview_last_interaction": None,
        "_purview_top_knowledge_sources": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Rule 2 — ORPHAN
# ---------------------------------------------------------------------------


class TestOrphanRule:
    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_fires_when_shared_and_publisher_missing(self) -> None:
        agent = _make_agent(
            availableTo=[{"type": "Organization"}],
            publisher=None,
        )
        # flags = evaluate(agent)
        # rule_ids = [f.rule_id for f in flags]
        # assert "ORPHAN" in rule_ids

    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_does_not_fire_when_individual_scope(self) -> None:
        agent = _make_agent(
            availableTo=[{"type": "User"}],
            publisher=None,
        )
        # flags = evaluate(agent)
        # assert all(f.rule_id != "ORPHAN" for f in flags)

    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_does_not_fire_when_publisher_present(self) -> None:
        agent = _make_agent(
            availableTo=[{"type": "Organization"}],
            publisher={"displayName": "Jane Doe", "publisherType": "User"},
        )
        # flags = evaluate(agent)
        # assert all(f.rule_id != "ORPHAN" for f in flags)


# ---------------------------------------------------------------------------
# Rule 6 — ORIGIN_RISK
# ---------------------------------------------------------------------------


class TestOriginRiskRule:
    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_agent_builder_is_high(self) -> None:
        agent = _make_agent(
            elementTypes=["DeclarativeAgent"],
            publisher={"displayName": "John User", "publisherType": "User"},
            availableTo=[{"type": "Organization"}],
        )
        # flags = evaluate(agent)
        # origin_flags = [f for f in flags if f.rule_id == "ORIGIN_RISK"]
        # assert origin_flags
        # assert origin_flags[0].level == "HIGH"

    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_microsoft_prebuilt_is_info(self) -> None:
        agent = _make_agent(
            elementTypes=["DeclarativeAgent"],
            publisher={"displayName": "Microsoft", "publisherType": "Microsoft"},
            availableTo=[{"type": "Organization"}],
        )
        # flags = evaluate(agent)
        # origin_flags = [f for f in flags if f.rule_id == "ORIGIN_RISK"]
        # assert origin_flags
        # assert origin_flags[0].level == "INFO"

    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_sharepoint_agent_is_high(self) -> None:
        agent = _make_agent(elementTypes=["SharePointAgent"])
        # flags = evaluate(agent)
        # origin_flags = [f for f in flags if f.rule_id == "ORIGIN_RISK"]
        # assert origin_flags
        # assert origin_flags[0].level == "HIGH"

    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_copilot_studio_is_medium(self) -> None:
        agent = _make_agent(
            elementTypes=["DeclarativeAgent"],
            publisher={"displayName": "Maker Studio", "publisherType": "Organization"},
        )
        # flags = evaluate(agent)
        # origin_flags = [f for f in flags if f.rule_id == "ORIGIN_RISK"]
        # assert origin_flags
        # assert origin_flags[0].level == "MEDIUM"


# ---------------------------------------------------------------------------
# Rule 4 — KNOWLEDGE_SOURCE_UNKNOWN
# ---------------------------------------------------------------------------


class TestKnowledgeSourceUnknownRule:
    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_fires_when_no_purview_data(self) -> None:
        agent = _make_agent(_purview_last_interaction=None, _purview_top_knowledge_sources=[])
        # flags = evaluate(agent)
        # assert any(f.rule_id == "KNOWLEDGE_SOURCE_UNKNOWN" for f in flags)

    @pytest.mark.skip(reason="risk_engine module not yet implemented")
    def test_does_not_fire_when_purview_data_present(self) -> None:
        agent = _make_agent(
            _purview_last_interaction="2026-03-01T10:00:00Z",
            _purview_top_knowledge_sources=["https://contoso.sharepoint.com/sites/hr"],
        )
        # flags = evaluate(agent)
        # assert all(f.rule_id != "KNOWLEDGE_SOURCE_UNKNOWN" for f in flags)
