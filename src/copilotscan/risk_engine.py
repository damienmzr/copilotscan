"""
risk_engine.py — CopilotScan risk evaluation engine
=====================================================
Evaluates 6 risk rules on each Agent object.

Input  : list[Agent] — purview enrichment fields may be None if Purview was skipped
Output : same list, with Agent.risk_flags populated in place

Public API
----------
evaluate(agent, inactivity_days=90) -> list[RiskFlag]
    Evaluate all rules for a single agent and return the flags.

evaluate_all(agents, inactivity_days=90) -> list[Agent]
    Evaluate all rules for every agent, writing flags to Agent.risk_flags.
    Returns the same list for convenience.

classify_origin(agent) -> AgentOrigin
    Classify how the agent was created from its Graph metadata.

Rules
-----
Rule 1 — INACTIVE          MEDIUM  purview
Rule 2 — ORPHAN            HIGH    graph
Rule 3 — SENSITIVE_KNOWLEDGE HIGH  purview-inferred
Rule 4 — KNOWLEDGE_UNKNOWN LOW     unavailable
Rule 5 — AGENT_NOT_AUDITED INFO    graph
Rule 6 — ORIGIN_RISK       varies  graph
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from copilotscan.models import (
    Agent,
    AgentOrigin,
    RiskFlag,
    RiskLevel,
)

logger = logging.getLogger(__name__)

# Strings that indicate broad / external knowledge-source access
_SENSITIVE_KS_KEYWORDS = ("everyone", "external")


# ---------------------------------------------------------------------------
# Origin classification
# ---------------------------------------------------------------------------


def classify_origin(agent: Agent) -> AgentOrigin:
    """
    Classify an agent's creation origin from its Graph metadata.

    Decision order (first match wins):
      1. 'SharePointAgent' in element_types            → SHAREPOINT_AGENT
      2. publisher.displayName == 'Microsoft'          → MICROSOFT_PREBUILT
      3. 'CustomEngineAgent' in element_types          → PRO_CODE
      4. 'DeclarativeAgent' + individual scope         → AGENT_BUILDER
      5. 'DeclarativeAgent' + org/team scope           → COPILOT_STUDIO
      6. fallback                                      → UNKNOWN
    """
    elem = [e.lower() for e in agent.element_types]
    publisher_name: str = ((agent.publisher or {}).get("displayName") or "").lower()

    if "sharepointagent" in elem:
        return AgentOrigin.SHAREPOINT_AGENT

    if publisher_name == "microsoft":
        return AgentOrigin.MICROSOFT_PREBUILT

    if "customenginagent" in elem or "customengineeragent" in elem:
        return AgentOrigin.PRO_CODE

    if "declarativeagent" in elem:
        # Individual scope → built by a user in Agent Builder (Copilot Studio personal)
        if not agent.is_org_scoped:
            return AgentOrigin.AGENT_BUILDER
        # Org / team scope → deployed via Copilot Studio
        return AgentOrigin.COPILOT_STUDIO

    return AgentOrigin.UNKNOWN


# ---------------------------------------------------------------------------
# Individual rule implementations
# ---------------------------------------------------------------------------


def _rule_inactive(agent: Agent, inactivity_days: int) -> RiskFlag | None:
    """Rule 1 — INACTIVE: no Purview data or last activity older than threshold."""
    if agent.purview_last_interaction is None:
        return RiskFlag(
            rule_id="INACTIVE",
            level=RiskLevel.MEDIUM,
            message_en=(
                f"No Purview activity recorded for this agent (threshold: {inactivity_days} days)."
            ),
            message_fr=(
                f"Aucune activité Purview enregistrée pour cet agent "
                f"(seuil : {inactivity_days} jours)."
            ),
            data_source="purview",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=inactivity_days)
    if agent.purview_last_interaction < cutoff:
        days_ago = (datetime.now(timezone.utc) - agent.purview_last_interaction).days
        return RiskFlag(
            rule_id="INACTIVE",
            level=RiskLevel.MEDIUM,
            message_en=(
                f"Agent last active {days_ago} days ago (threshold: {inactivity_days} days)."
            ),
            message_fr=(
                f"Agent actif pour la dernière fois il y a {days_ago} jours "
                f"(seuil : {inactivity_days} jours)."
            ),
            data_source="purview",
        )
    return None


def _rule_orphan(agent: Agent) -> RiskFlag | None:
    """Rule 2 — ORPHAN: org-scoped agent with no publisher/owner."""
    if agent.is_org_scoped and agent.publisher is None:
        return RiskFlag(
            rule_id="ORPHAN",
            level=RiskLevel.HIGH,
            message_en=("Agent is shared org-wide but has no registered publisher / owner."),
            message_fr=(
                "L'agent est partagé à l'échelle de l'organisation "
                "mais ne possède pas de propriétaire enregistré."
            ),
            data_source="graph",
        )
    return None


def _rule_sensitive_knowledge(agent: Agent) -> RiskFlag | None:
    """Rule 3 — SENSITIVE_KNOWLEDGE: knowledge source URL suggests broad access."""
    if not agent.purview_top_knowledge_sources:
        return None

    flagged = [
        ks
        for ks in agent.purview_top_knowledge_sources
        if any(kw in ks.lower() for kw in _SENSITIVE_KS_KEYWORDS)
    ]
    if flagged:
        sources_str = ", ".join(flagged[:3])
        return RiskFlag(
            rule_id="SENSITIVE_KNOWLEDGE",
            level=RiskLevel.HIGH,
            message_en=(f"Agent accesses potentially over-broad knowledge sources: {sources_str}"),
            message_fr=(
                f"L'agent accède à des sources de connaissances potentiellement "
                f"trop larges : {sources_str}"
            ),
            data_source="purview-inferred",
        )
    return None


def _rule_knowledge_unknown(agent: Agent) -> RiskFlag | None:
    """Rule 4 — KNOWLEDGE_UNKNOWN: no Purview data available at all."""
    if agent.purview_last_interaction is None and not agent.purview_top_knowledge_sources:
        return RiskFlag(
            rule_id="KNOWLEDGE_UNKNOWN",
            level=RiskLevel.LOW,
            message_en=(
                "Knowledge sources could not be determined "
                "(no Purview audit data available for this agent)."
            ),
            message_fr=(
                "Les sources de connaissances n'ont pas pu être déterminées "
                "(aucune donnée d'audit Purview disponible pour cet agent)."
            ),
            data_source="unavailable",
        )
    return None


def _rule_agent_not_audited(agent: Agent, origin: AgentOrigin) -> RiskFlag | None:
    """Rule 5 — AGENT_NOT_AUDITED: origin types that do not emit audit records."""
    if origin in (AgentOrigin.MICROSOFT_PREBUILT, AgentOrigin.SHAREPOINT_AGENT):
        return RiskFlag(
            rule_id="AGENT_NOT_AUDITED",
            level=RiskLevel.INFO,
            message_en=(
                f"Agent origin ({origin.value}) does not emit Purview audit records. "
                "Activity cannot be tracked via AuditLog."
            ),
            message_fr=(
                f"L'origine de l'agent ({origin.value}) ne génère pas d'enregistrements "
                "d'audit Purview. L'activité ne peut pas être suivie via AuditLog."
            ),
            data_source="graph",
        )
    return None


_ORIGIN_RISK_LEVEL: dict[AgentOrigin, RiskLevel] = {
    AgentOrigin.AGENT_BUILDER: RiskLevel.HIGH,
    AgentOrigin.SHAREPOINT_AGENT: RiskLevel.HIGH,
    AgentOrigin.COPILOT_STUDIO: RiskLevel.MEDIUM,
    AgentOrigin.PRO_CODE: RiskLevel.MEDIUM,
    AgentOrigin.MICROSOFT_PREBUILT: RiskLevel.INFO,
    AgentOrigin.UNKNOWN: RiskLevel.LOW,
}

_ORIGIN_RISK_MSG_EN: dict[AgentOrigin, str] = {
    AgentOrigin.AGENT_BUILDER: "Agent was created by an individual user via Agent Builder and shared org-wide.",
    AgentOrigin.SHAREPOINT_AGENT: "Agent is a SharePoint-native agent — audit coverage is limited.",
    AgentOrigin.COPILOT_STUDIO: "Agent was deployed via Copilot Studio — verify governance approval.",
    AgentOrigin.PRO_CODE: "Agent is a custom-engine (pro-code) agent — code review recommended.",
    AgentOrigin.MICROSOFT_PREBUILT: "Agent is a Microsoft prebuilt agent — low risk.",
    AgentOrigin.UNKNOWN: "Agent origin could not be determined from available metadata.",
}

_ORIGIN_RISK_MSG_FR: dict[AgentOrigin, str] = {
    AgentOrigin.AGENT_BUILDER: "L'agent a été créé par un utilisateur individuel via Agent Builder et partagé à l'organisation.",
    AgentOrigin.SHAREPOINT_AGENT: "L'agent est natif SharePoint — la couverture d'audit est limitée.",
    AgentOrigin.COPILOT_STUDIO: "L'agent a été déployé via Copilot Studio — vérifiez l'approbation de gouvernance.",
    AgentOrigin.PRO_CODE: "L'agent est un agent custom-engine (pro-code) — une revue de code est recommandée.",
    AgentOrigin.MICROSOFT_PREBUILT: "L'agent est un agent Microsoft préintégré — risque faible.",
    AgentOrigin.UNKNOWN: "L'origine de l'agent n'a pas pu être déterminée à partir des métadonnées disponibles.",
}


def _rule_origin_risk(agent: Agent, origin: AgentOrigin) -> RiskFlag:
    """Rule 6 — ORIGIN_RISK: always fires; level depends on origin."""
    return RiskFlag(
        rule_id="ORIGIN_RISK",
        level=_ORIGIN_RISK_LEVEL[origin],
        message_en=_ORIGIN_RISK_MSG_EN[origin],
        message_fr=_ORIGIN_RISK_MSG_FR[origin],
        data_source="graph",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate(agent: Agent, inactivity_days: int = 90) -> list[RiskFlag]:
    """
    Evaluate all 6 risk rules for a single agent.

    Args:
        agent:           The Agent to evaluate.
        inactivity_days: Days without Purview activity before INACTIVE fires.

    Returns:
        List of RiskFlag objects (may be empty for a clean agent).
    """
    flags: list[RiskFlag] = []
    origin = classify_origin(agent)

    for flag in (
        _rule_inactive(agent, inactivity_days),
        _rule_orphan(agent),
        _rule_sensitive_knowledge(agent),
        _rule_knowledge_unknown(agent),
        _rule_agent_not_audited(agent, origin),
        _rule_origin_risk(agent, origin),
    ):
        if flag is not None:
            flags.append(flag)

    logger.debug(
        "evaluate: agent=%s origin=%s flags=%s",
        agent.id,
        origin.value,
        [f.rule_id for f in flags],
    )
    return flags


def evaluate_all(
    agents: list[Agent],
    inactivity_days: int = 90,
) -> list[Agent]:
    """
    Evaluate all rules for every agent in the list.
    Writes results to Agent.risk_flags in place and returns the list.
    """
    for agent in agents:
        agent.risk_flags = evaluate(agent, inactivity_days=inactivity_days)
    logger.info(
        "evaluate_all: evaluated %d agents; total flags=%d",
        len(agents),
        sum(len(a.risk_flags) for a in agents),
    )
    return agents
