"""
models.py — CopilotScan domain models
======================================
AuditQueryStatus : Purview audit query lifecycle states
AgentOrigin      : Classification of how/where an agent was created
RiskLevel        : Severity of a risk flag (INFO → HIGH)
RiskFlag         : A single rule evaluation result attached to an Agent
Agent            : A Copilot agent as returned by Graph /beta/copilot/admin/catalog/packages
PurviewData      : Per-agent activity data aggregated from the Purview audit log
KnowledgeSource  : Type alias for a knowledge-source URL string
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: A knowledge-source reference — typically a SharePoint / OneDrive URL.
KnowledgeSource = str


# ---------------------------------------------------------------------------
# AuditQueryStatus
# ---------------------------------------------------------------------------


class AuditQueryStatus(str, Enum):
    """Lifecycle states of a Purview /auditLog/queries job."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# AgentOrigin
# ---------------------------------------------------------------------------


class AgentOrigin(str, Enum):
    """How / where the agent was created — used by classify_origin() in risk_engine."""

    SHAREPOINT_AGENT = "sharepoint_agent"
    MICROSOFT_PREBUILT = "microsoft_prebuilt"
    PRO_CODE = "pro_code"
    AGENT_BUILDER = "agent_builder"
    COPILOT_STUDIO = "copilot_studio"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# RiskLevel / RiskFlag
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    """Severity of a risk flag, ordered from lowest to highest."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class RiskFlag:
    """A single rule evaluation result produced by risk_engine.evaluate()."""

    rule_id: str  # e.g. "INACTIVE", "ORPHAN"
    level: RiskLevel
    message_en: str
    message_fr: str
    data_source: str  # 'graph' | 'purview' | 'purview-inferred' | 'unavailable'


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    """
    Represents a single Copilot agent from the Graph catalog.

    The raw payload is a dict matching the /beta/copilot/admin/catalog/packages
    response schema.  Purview-enriched fields are prefixed with `_purview_`.
    """

    id: str
    display_name: str
    element_types: list[str]
    agent_type: str
    is_blocked: bool
    publisher: dict[str, Any] | None
    available_to: list[dict[str, Any]]
    deployed_to: list[dict[str, Any]]
    supported_hosts: list[str]
    version: str | None
    last_modified_datetime: datetime | None

    # Optional fields — populated when available from Graph detail endpoint
    creator_upn: str | None = field(default=None)
    platform: str | None = field(default=None)
    short_description: str | None = field(default=None)
    long_description: str | None = field(default=None)
    # Parsed from elementDetails.elements[].definition
    graph_capabilities: list[dict[str, Any]] = field(default_factory=list)
    graph_instructions: str | None = field(default=None)
    graph_actions: list[dict[str, Any]] = field(default_factory=list)
    graph_conversation_starters: list[dict[str, Any]] = field(default_factory=list)

    # Populated after Purview enrichment
    purview_last_interaction: datetime | None = field(default=None)
    purview_top_knowledge_sources: list[KnowledgeSource] = field(default_factory=list)

    # Populated by risk_engine.evaluate()
    risk_flags: list[RiskFlag] = field(default_factory=list)

    # Raw payload retained for downstream consumers (risk engine, reports)
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------------

    @classmethod
    def from_graph_payload(cls, payload: dict[str, Any]) -> Agent:
        """
        Construct an Agent from a raw Graph API response item.

        Fields not present in the payload default to safe empty values so
        callers don't need to handle KeyError / None everywhere.
        """
        raw_dt = payload.get("lastModifiedDateTime")
        last_modified: datetime | None = None
        if raw_dt:
            try:
                last_modified = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            except ValueError:
                pass

        return cls(
            id=payload.get("id", ""),
            display_name=payload.get("displayName", ""),
            element_types=payload.get("elementTypes") or [],
            agent_type=payload.get("type", ""),
            is_blocked=bool(payload.get("isBlocked", False)),
            publisher=payload.get("publisher"),
            available_to=payload.get("availableTo") or [],
            deployed_to=payload.get("deployedTo") or [],
            supported_hosts=payload.get("supportedHosts") or [],
            version=payload.get("version"),
            last_modified_datetime=last_modified,
            platform=payload.get("platform"),
            short_description=payload.get("shortDescription"),
            long_description=payload.get("longDescription"),
            _raw=payload,
        )

    # Convenience helpers used by the risk engine
    # ------------------------------------------------------------------

    @property
    def is_org_scoped(self) -> bool:
        """True when the agent is available tenant-wide (not just to one user)."""
        return any(
            isinstance(a, dict) and (a.get("type") or "").lower() == "organization"
            for a in self.available_to
        )

    @property
    def days_since_modified(self) -> int | None:
        """Number of full days since lastModifiedDateTime, or None if unknown."""
        if self.last_modified_datetime is None:
            return None
        delta = datetime.now(timezone.utc) - self.last_modified_datetime
        return delta.days


# ---------------------------------------------------------------------------
# PurviewData
# ---------------------------------------------------------------------------


@dataclass
class PurviewData:
    """
    Aggregated Purview audit data for a single agent.

    Built incrementally by calling `merge_record()` for each audit record,
    then finalised with `compute_top_sources()`.
    """

    agent_id: str

    # Timestamp of the most recent recorded interaction
    last_interaction: datetime | None = field(default=None)

    # Running count of knowledge-source references
    _source_counter: Counter = field(default_factory=Counter, repr=False)

    # Populated by compute_top_sources()
    top_knowledge_sources: list[str] = field(default_factory=list)

    # Raw record count (useful for activity-volume checks)
    record_count: int = field(default=0)

    # ------------------------------------------------------------------

    def merge_record(self, record: dict[str, Any]) -> None:
        """
        Incorporate a single audit record into the aggregate.

        Expected fields (all optional — missing keys are silently skipped):
          - createdDateTime / activityDateTime : ISO-8601 timestamp
          - auditData.KnowledgeSources         : list[str]
        """
        self.record_count += 1

        # Track last interaction time
        raw_ts = record.get("createdDateTime") or record.get("activityDateTime")
        if raw_ts:
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if self.last_interaction is None or ts > self.last_interaction:
                    self.last_interaction = ts
            except ValueError:
                pass

        # Accumulate knowledge-source hits
        audit_data = record.get("auditData") or {}
        sources = audit_data.get("KnowledgeSources") or []
        if isinstance(sources, list):
            self._source_counter.update(sources)

    def compute_top_sources(self, top_n: int = 5) -> None:
        """Populate `top_knowledge_sources` with the N most-referenced sources."""
        self.top_knowledge_sources = [src for src, _ in self._source_counter.most_common(top_n)]
