"""
tests/test_auth.py — Unit tests for copilotscan.auth

All tests mock MSAL and filesystem I/O — no live Microsoft Graph calls.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from copilotscan.auth import (
    AuthConfig,
    AuthFlow,
    AuthResult,
    AuthenticationError,
    CopilotScanAuth,
    TokenCacheManager,
    get_auth_headers,
)


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------


class TestAuthConfig:
    def test_raises_when_client_id_missing(self) -> None:
        with pytest.raises(ValueError, match="client_id"):
            AuthConfig(client_id="", tenant_id="tenant-123")

    def test_raises_when_tenant_id_missing(self) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            AuthConfig(client_id="client-123", tenant_id="")

    def test_raises_client_credentials_without_secret(self) -> None:
        with pytest.raises(ValueError, match="client_secret"):
            AuthConfig(
                client_id="client-123",
                tenant_id="tenant-123",
                flow=AuthFlow.CLIENT_CREDENTIALS,
                client_secret=None,
            )

    def test_env_vars_override_constructor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COPILOTSCAN_CLIENT_ID", "env-client")
        monkeypatch.setenv("COPILOTSCAN_TENANT_ID", "env-tenant")
        config = AuthConfig(client_id="ignored", tenant_id="ignored")
        assert config.client_id == "env-client"
        assert config.tenant_id == "env-tenant"

    def test_authority_format(self) -> None:
        config = AuthConfig(client_id="c", tenant_id="t-abc")
        assert config.authority == "https://login.microsoftonline.com/t-abc"

    def test_device_code_is_default_flow(self) -> None:
        config = AuthConfig(client_id="c", tenant_id="t")
        assert config.flow == AuthFlow.DEVICE_CODE


# ---------------------------------------------------------------------------
# AuthResult
# ---------------------------------------------------------------------------


class TestAuthResult:
    def _make_result(self, expires_in: int = 3600) -> AuthResult:
        return AuthResult(
            access_token="tok",
            flow_used=AuthFlow.DEVICE_CODE,
            expires_at=time.time() + expires_in,
        )

    def test_not_expired_for_fresh_token(self) -> None:
        assert not self._make_result(3600).is_expired

    def test_expired_when_within_buffer(self) -> None:
        # expires_at is 60 seconds from now — within the 300s buffer
        assert self._make_result(60).is_expired

    def test_bearer_header_format(self) -> None:
        result = self._make_result()
        assert result.bearer_header == {"Authorization": "Bearer tok"}


# ---------------------------------------------------------------------------
# TokenCacheManager
# ---------------------------------------------------------------------------


class TestTokenCacheManager:
    def test_creates_cache_dir_on_save(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "sub" / "token_cache.bin"
        mgr = TokenCacheManager(cache_path)
        # Simulate a state change
        mgr._cache._cache_changed = True  # type: ignore[attr-defined]
        # save() should create the parent dir
        with patch.object(mgr._cache, "has_state_changed", True):
            with patch.object(mgr._cache, "serialize", return_value="serialized"):
                mgr.save()
        assert (tmp_path / "sub").exists()

    def test_corrupt_cache_is_removed(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "token_cache.bin"
        cache_path.write_text("not-valid-json", encoding="utf-8")
        # Should not raise; corrupt cache is silently removed
        mgr = TokenCacheManager(cache_path)
        assert not cache_path.exists()


# ---------------------------------------------------------------------------
# CopilotScanAuth — process_token_response
# ---------------------------------------------------------------------------


class TestCopilotScanAuth:
    def _make_auth(self) -> CopilotScanAuth:
        config = AuthConfig(client_id="c", tenant_id="t")
        return CopilotScanAuth(config)

    def test_raises_authentication_error_on_missing_token(self) -> None:
        auth = self._make_auth()
        with pytest.raises(AuthenticationError, match="device_code"):
            auth._process_token_response(  # type: ignore[attr-defined]
                {"error": "invalid_grant", "error_description": "Token expired"},
                AuthFlow.DEVICE_CODE,
            )

    def test_build_result_sets_fields_correctly(self) -> None:
        auth = self._make_auth()
        response = {
            "access_token": "test-token-abc",
            "expires_in": 3600,
            "scope": "CopilotPackages.Read.All Reports.Read.All",
            "account": {"username": "admin@contoso.com"},
        }
        result = auth._build_result(response, AuthFlow.DEVICE_CODE)  # type: ignore[attr-defined]
        assert result.access_token == "test-token-abc"
        assert result.flow_used == AuthFlow.DEVICE_CODE
        assert result.account_upn == "admin@contoso.com"
        assert "CopilotPackages.Read.All" in result.scopes_granted
