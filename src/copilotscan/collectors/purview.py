"""
collectors/purview.py — CopilotScan PurviewCollector
=====================================================
Queries the Microsoft Purview audit log to infer Copilot agent activity
and knowledge-source usage.

Endpoint  : POST /beta/security/auditLog/queries  (async – poll until complete)
Filters   : AgentAdminActivity · AgentSettingsAdminActivity · CopilotInteraction
Returns   : dict[agent_id, PurviewData]
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from copilotscan.exceptions import AuditQueryTimeout
from copilotscan.models import AuditQueryStatus, PurviewData

logger = logging.getLogger(__name__)


def _sleep(seconds: float, reason: str = "") -> None:
    msg = f"Sleeping {seconds:.1f}s" + (f" – {reason}" if reason else "")
    logger.debug(msg)
    time.sleep(seconds)


class PurviewCollector:
    """
    Queries the Microsoft Purview audit log to infer Copilot agent activity
    and knowledge-source usage.

    Endpoint  : POST /beta/security/auditLog/queries  (async – poll until complete)
    Filters   : AgentAdminActivity · AgentSettingsAdminActivity · CopilotInteraction
    Poll      : every POLL_INTERVAL_S seconds, up to poll_timeout_minutes
    Returns   : dict[agent_id, PurviewData]

    Coverage  : Copilot Studio agents only
                (prebuilt & SharePoint agents do not emit audit records of this type)
    """

    AUDIT_QUERY_URL = "https://graph.microsoft.com/beta/security/auditLog/queries"
    POLL_INTERVAL_S = 30

    RECORD_TYPE_FILTERS = [
        "AgentAdminActivity",
        "AgentSettingsAdminActivity",
        "CopilotInteraction",
    ]

    def __init__(
        self,
        authorization_header: str,
        session: Optional[requests.Session] = None,
        poll_timeout_minutes: int = 30,
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
    ) -> None:
        """
        Args:
            authorization_header:  Bearer token header value.
            session:               Optional pre-configured requests.Session.
            poll_timeout_minutes:  Max minutes to wait for query completion (default 30).
            start_datetime:        Audit window start (UTC). Defaults to 7 days ago.
            end_datetime:          Audit window end   (UTC). Defaults to now.
        """
        self._auth_header     = authorization_header
        self._session         = session or requests.Session()
        self._timeout_minutes = poll_timeout_minutes

        now = datetime.now(timezone.utc)
        self._start_dt = start_datetime or datetime(
            now.year, now.month, now.day, tzinfo=timezone.utc
        ).replace(day=max(1, now.day - 7))
        self._end_dt = end_datetime or now

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> dict[str, PurviewData]:
        """
        Submit the audit query, poll until done, aggregate results.
        Returns a mapping of agent_id → PurviewData.
        """
        query_id = self._submit_query()
        logger.info("PurviewCollector: submitted audit query %s", query_id)

        self._poll_until_complete(query_id)
        logger.info("PurviewCollector: query %s completed; fetching records", query_id)

        purview_map = self._aggregate_records(query_id)
        logger.info(
            "PurviewCollector: aggregated data for %d agents", len(purview_map)
        )
        return purview_map

    # ------------------------------------------------------------------
    # Step 1 – Submit
    # ------------------------------------------------------------------

    def _submit_query(self) -> str:
        """POST the audit query and return the resulting query ID."""
        body = {
            "displayName":         f"CopilotScan-{datetime.now(timezone.utc).isoformat()}",
            "filterStartDateTime": self._start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "filterEndDateTime":   self._end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recordTypeFilters":   self.RECORD_TYPE_FILTERS,
        }

        resp = self._session.post(
            self.AUDIT_QUERY_URL,
            json=body,
            headers={
                "Authorization": self._auth_header,
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    # ------------------------------------------------------------------
    # Step 2 – Poll
    # ------------------------------------------------------------------

    def _poll_until_complete(self, query_id: str) -> None:
        """
        Poll the query status every POLL_INTERVAL_S seconds until
        status is 'succeeded' or the timeout is reached.
        """
        deadline   = time.monotonic() + self._timeout_minutes * 60
        status_url = f"{self.AUDIT_QUERY_URL}/{query_id}"

        while True:
            if time.monotonic() > deadline:
                raise AuditQueryTimeout(
                    f"Purview audit query {query_id} did not complete within "
                    f"{self._timeout_minutes} minutes."
                )

            resp = self._session.get(
                status_url,
                headers={"Authorization": self._auth_header, "Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            data   = resp.json()
            status = (data.get("status") or "").lower()

            logger.debug("PurviewCollector: query %s status = %s", query_id, status)

            if status == AuditQueryStatus.SUCCEEDED.value:
                return
            if status in (AuditQueryStatus.FAILED.value, AuditQueryStatus.CANCELLED.value):
                raise RuntimeError(
                    f"Purview audit query {query_id} ended with status '{status}'. "
                    f"Detail: {data.get('error', {})}"
                )

            _sleep(self.POLL_INTERVAL_S, f"waiting for query {query_id} (status={status})")

    # ------------------------------------------------------------------
    # Step 3 – Aggregate
    # ------------------------------------------------------------------

    def _aggregate_records(self, query_id: str) -> dict[str, PurviewData]:
        """
        Page through all audit records for the completed query and
        aggregate per-agent activity + knowledge-source hit counts.
        """
        records_url: Optional[str] = (
            f"{self.AUDIT_QUERY_URL}/{query_id}/records?$top=1000"
        )
        purview_map: dict[str, PurviewData] = {}

        while records_url:
            resp = self._session.get(
                records_url,
                headers={"Authorization": self._auth_header, "Accept": "application/json"},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()

            for record in data.get("value", []):
                agent_id = self._extract_agent_id(record)
                if not agent_id:
                    continue
                if agent_id not in purview_map:
                    purview_map[agent_id] = PurviewData(agent_id=agent_id)
                purview_map[agent_id].merge_record(record)

            records_url = data.get("@odata.nextLink")

        for pd in purview_map.values():
            pd.compute_top_sources(top_n=5)

        return purview_map

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_agent_id(record: dict) -> Optional[str]:
        """
        Extract agent_id from an audit record.
        Microsoft uses different field names depending on record type.
        """
        audit_data = record.get("auditData") or {}
        return (
            audit_data.get("AgentId")
            or audit_data.get("agentId")
            or record.get("objectId")
        )
