"""
exceptions.py — CopilotScan custom exceptions
"""

from __future__ import annotations


class FeatureFlagError(Exception):
    """Raised when the tenant has not enabled the Copilot admin catalog feature (HTTP 403)."""


class DelegatedAuthRequired(Exception):
    """Raised when the endpoint requires delegated (user) auth, not app-only (HTTP 424)."""


class AuditQueryTimeout(Exception):
    """Raised when the Purview audit query does not complete within the configured timeout."""
