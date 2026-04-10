"""
collectors/graph.py — CopilotScan GraphCollector
=================================================
Fetches all Copilot agents from Microsoft Graph.

Endpoint : GET /beta/copilot/admin/catalog/packages
Auth     : delegated token (authorization_header from auth.py)
Features :
  - OData pagination  (@odata.nextLink)
  - Exponential backoff retry (max 3 attempts, base 2 s)
  - Rate-limit handling (HTTP 429 → honour Retry-After header)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import requests

from copilotscan.exceptions import DelegatedAuthRequired, FeatureFlagError
from copilotscan.models import Agent

logger = logging.getLogger(__name__)


def _sleep(seconds: float, reason: str = "") -> None:
    msg = f"Sleeping {seconds:.1f}s" + (f" – {reason}" if reason else "")
    logger.debug(msg)
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# GraphCollector
# ---------------------------------------------------------------------------


class GraphCollector:
    """
    Fetches all Copilot agents from Microsoft Graph.

    Endpoint : GET /beta/copilot/admin/catalog/packages
    Auth     : delegated token (TokenResult.authorization_header from auth.py)
    Features :
      - OData pagination  (@odata.nextLink)
      - Exponential backoff retry (max 3 attempts, base 2 s)
      - Rate-limit handling (HTTP 429 → honour Retry-After header)

    Returns  : list[Agent]
    Errors   :
      - HTTP 403 → FeatureFlagError
      - HTTP 424 → DelegatedAuthRequired
    """

    BASE_URL = "https://graph.microsoft.com/beta/copilot/admin/catalog/packages"
    MAX_RETRY = 3
    BACKOFF_BASE = 2.0  # seconds

    def __init__(
        self,
        authorization_header: str,
        session: requests.Session | None = None,
        page_size: int = 100,
    ) -> None:
        """
        Args:
            authorization_header: Value of the Authorization header
                                  (e.g. "Bearer eyJ0...").
            session:              Optional pre-configured requests.Session.
            page_size:            OData $top value per page (max 100).
        """
        self._auth_header = authorization_header
        self._session = session or requests.Session()
        self._page_size = page_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> list[Agent]:
        """Fetch *all* agents, handling pagination automatically."""
        agents: list[Agent] = []
        for page in self._paginate():
            for item in page:
                try:
                    agents.append(Agent.from_graph_payload(item))
                except Exception as exc:
                    logger.warning("Skipping malformed agent payload: %s", exc)
        logger.info("GraphCollector: collected %d agents", len(agents))
        return agents

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _paginate(self) -> Iterator[list[dict]]:
        """Yield successive pages (lists of raw dicts) from the Graph endpoint."""
        url: str | None = f"{self.BASE_URL}?$top={self._page_size}"
        page_num = 0

        while url:
            page_num += 1
            logger.debug("GraphCollector: fetching page %d – %s", page_num, url)
            data = self._get_with_retry(url)
            yield data.get("value", [])
            url = data.get("@odata.nextLink")

    def _get_with_retry(self, url: str) -> dict:
        """
        Perform a GET with:
          - 429 rate-limit back-off (Retry-After header or 60 s default)
          - Exponential backoff on transient errors (5xx / network)
          - Up to MAX_RETRY attempts
        """
        last_exc: Exception | None = None

        for attempt in range(1, self.MAX_RETRY + 1):
            try:
                resp = self._session.get(
                    url,
                    headers={
                        "Authorization": self._auth_header,
                        "Accept": "application/json",
                        "ConsistencyLevel": "eventual",
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                last_exc = exc
                wait = self.BACKOFF_BASE**attempt
                logger.warning(
                    "GraphCollector: network error on attempt %d/%d – %s. Retrying in %.1f s",
                    attempt,
                    self.MAX_RETRY,
                    exc,
                    wait,
                )
                _sleep(wait, "network error")
                continue

            # ── Rate limiting ─────────────────────────────────────────
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 60))
                logger.warning(
                    "GraphCollector: rate limited (429). Waiting %.0f s (Retry-After).",
                    retry_after,
                )
                _sleep(retry_after, "rate limit 429")
                # Do NOT count this against the retry budget
                continue

            # ── Auth / feature errors ─────────────────────────────────
            if resp.status_code == 403:
                raise FeatureFlagError(
                    "HTTP 403 – the tenant may not have the Copilot admin catalog "
                    "feature enabled, or the token lacks required scopes. "
                    f"URL: {url}"
                )
            if resp.status_code == 424:
                raise DelegatedAuthRequired(
                    "HTTP 424 – this endpoint requires delegated (user) authentication. "
                    "Re-run auth with interactive flow. "
                    f"URL: {url}"
                )

            # ── Server-side transient errors ──────────────────────────
            if resp.status_code >= 500:
                wait = self.BACKOFF_BASE**attempt
                logger.warning(
                    "GraphCollector: server error %d on attempt %d/%d. Retrying in %.1f s",
                    resp.status_code,
                    attempt,
                    self.MAX_RETRY,
                    wait,
                )
                _sleep(wait, f"HTTP {resp.status_code}")
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
                continue

            # ── Other client errors ───────────────────────────────────
            resp.raise_for_status()

            return resp.json()

        raise RuntimeError(
            f"GraphCollector: gave up after {self.MAX_RETRY} attempts. Last error: {last_exc}"
        )
