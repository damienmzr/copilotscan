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

    def preflight(self) -> None:
        """
        Run lightweight diagnostic calls before the main collection.

        Checks:
          1. Token works at all  (GET /v1.0/me)
          2. Signed-in user's directory roles  (GET /v1.0/me/transitiveMemberOf)
          3. Tenant service plan includes Copilot  (GET /v1.0/subscribedSkus)

        Raises a descriptive RuntimeError if a clear problem is found;
        logs warnings for non-blocking issues.
        """
        # ── 1. Basic token check ──────────────────────────────────────
        me_resp = self._session.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": self._auth_header, "Accept": "application/json"},
            timeout=15,
        )
        if me_resp.status_code == 401:
            raise RuntimeError("Preflight: token is invalid or expired (HTTP 401 on /v1.0/me).")
        if not me_resp.ok:
            logger.warning("Preflight: /v1.0/me returned %d – continuing anyway.", me_resp.status_code)
        else:
            me = me_resp.json()
            logger.info("Preflight: signed in as %s (%s)", me.get("displayName"), me.get("userPrincipalName"))

        # ── 2. Role check ─────────────────────────────────────────────
        roles_resp = self._session.get(
            "https://graph.microsoft.com/v1.0/me/transitiveMemberOf?$select=displayName,roleTemplateId",
            headers={"Authorization": self._auth_header, "Accept": "application/json"},
            timeout=15,
        )
        REQUIRED_ROLE_IDS = {
            "62e90394-69f5-4237-9190-012177145e10",  # Global Administrator
            "892c5842-a9a6-463a-8041-72aa08ca3cf6",  # Copilot Administrator (preview)
        }
        has_required_role = False
        if roles_resp.ok:
            memberships = roles_resp.json().get("value", [])
            role_names = [m.get("displayName", "") for m in memberships]
            role_ids = {m.get("roleTemplateId", "") for m in memberships}
            logger.info("Preflight: user roles/groups: %s", role_names)
            has_required_role = bool(REQUIRED_ROLE_IDS & role_ids)
            if not has_required_role:
                logger.warning(
                    "Preflight: signed-in account does not appear to hold "
                    "Global Administrator or Copilot Administrator role. "
                    "Roles found: %s", role_names or ["(none)"]
                )
        else:
            logger.warning("Preflight: could not retrieve roles (HTTP %d).", roles_resp.status_code)

        # ── 3. Licence check ──────────────────────────────────────────
        # SKU part numbers that include the Copilot admin catalog feature
        COPILOT_SKU_PARTS = {
            "microsoft_365_copilot",
            "m365_copilot",
            "copilot_for_microsoft_365",
            "copilot",
        }
        skus_resp = self._session.get(
            "https://graph.microsoft.com/v1.0/subscribedSkus?$select=skuPartNumber,capabilityStatus",
            headers={"Authorization": self._auth_header, "Accept": "application/json"},
            timeout=15,
        )
        has_copilot_sku = False
        if skus_resp.ok:
            skus = skus_resp.json().get("value", [])
            enabled_skus = [s["skuPartNumber"] for s in skus if s.get("capabilityStatus") == "Enabled"]
            logger.info("Preflight: enabled tenant SKUs: %s", enabled_skus)
            has_copilot_sku = any(
                any(kw in sku.lower() for kw in COPILOT_SKU_PARTS)
                for sku in enabled_skus
            )
            if not has_copilot_sku:
                logger.warning(
                    "Preflight: no Microsoft 365 Copilot SKU found among enabled licences. "
                    "The /beta/copilot/admin/catalog/packages endpoint requires a "
                    "Microsoft 365 Copilot licence on the tenant."
                )
        else:
            logger.warning("Preflight: could not retrieve SKUs (HTTP %d).", skus_resp.status_code)

        # ── Summary ───────────────────────────────────────────────────
        if not has_required_role or not has_copilot_sku:
            problems = []
            if not has_copilot_sku:
                problems.append(
                    "  • No Microsoft 365 Copilot licence detected on this tenant.\n"
                    "    The catalog API is only available to tenants with an M365 Copilot subscription."
                )
            if not has_required_role:
                problems.append(
                    "  • The signed-in account lacks the Global Administrator or\n"
                    "    Copilot Administrator role required to read the agent catalog."
                )
            raise RuntimeError(
                "Preflight checks failed — the Graph catalog endpoint will return 403.\n"
                + "\n".join(problems)
            )

        logger.info("Preflight: all checks passed.")

    def collect(self) -> list[Agent]:
        """Run preflight checks then fetch *all* agents, handling pagination automatically."""
        self.preflight()
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
                try:
                    err_body = resp.json()
                    err_detail = err_body.get("error", {})
                    err_code = err_detail.get("code", "")
                    err_msg = err_detail.get("message", "")
                    detail = f"code={err_code!r} message={err_msg!r}"
                except Exception:
                    detail = resp.text[:500]
                raise FeatureFlagError(
                    f"HTTP 403 from Microsoft Graph.\n"
                    f"  URL    : {url}\n"
                    f"  Detail : {detail}\n"
                    f"  Possible causes:\n"
                    f"    • Admin consent not yet granted for CopilotPackages.Read.All\n"
                    f"    • Tenant does not have a Microsoft 365 Copilot licence\n"
                    f"    • The /beta/copilot/admin/catalog/packages endpoint is not\n"
                    f"      enabled for this tenant (requires M365 Copilot or Copilot+)\n"
                    f"    • The signed-in user is not a Global Admin / Copilot Admin"
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
