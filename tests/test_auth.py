"""
tests/test_auth.py — Unit tests for copilotscan.auth

All tests mock MSAL and filesystem I/O — no live Microsoft Graph calls.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from copilotscan.auth import (
    AuthConfig,
    AuthConfigError,
    AuthMode,
    CopilotScanAuthenticator,
    TokenResult,
    load_config,
)

# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------


class TestAuthConfig:
    def test_stores_client_and_tenant_id(self) -> None:
        config = AuthConfig(client_id="client-123", tenant_id="tenant-abc")
        assert config.client_id == "client-123"
        assert config.tenant_id == "tenant-abc"

    def test_default_auth_mode_is_device_code(self) -> None:
        config = AuthConfig(client_id="c", tenant_id="t")
        assert config.auth_mode == AuthMode.DEVICE_CODE

    def test_authority_format(self) -> None:
        config = AuthConfig(client_id="c", tenant_id="t-abc")
        assert config.authority == "https://login.microsoftonline.com/t-abc"

    def test_device_code_scopes_are_delegated(self) -> None:
        config = AuthConfig(client_id="c", tenant_id="t", auth_mode=AuthMode.DEVICE_CODE)
        assert any("Reports.Read.All" in s for s in config.scopes)

    def test_app_secret_scopes_use_default(self) -> None:
        config = AuthConfig(
            client_id="c",
            tenant_id="t",
            client_secret="s",
            auth_mode=AuthMode.APP_SECRET,
        )
        assert any(".default" in s for s in config.scopes)

    def test_extra_scopes_appended(self) -> None:
        config = AuthConfig(
            client_id="c",
            tenant_id="t",
            extra_scopes=["https://graph.microsoft.com/CustomScope"],
        )
        assert "https://graph.microsoft.com/CustomScope" in config.scopes


# ---------------------------------------------------------------------------
# TokenResult
# ---------------------------------------------------------------------------


class TestTokenResult:
    def _make_result(self, expires_in: int = 3600) -> TokenResult:
        return TokenResult(access_token="tok", expires_in=expires_in)

    def test_not_expired_for_fresh_token(self) -> None:
        assert not self._make_result(3600).is_expired

    def test_expired_when_within_60s_buffer(self) -> None:
        # expires_at is 30 seconds from now — within the 60s buffer
        result = TokenResult(
            access_token="tok",
            expires_in=30,
            expires_at=time.time() + 30,
        )
        assert result.is_expired

    def test_authorization_header_format(self) -> None:
        result = self._make_result()
        assert result.authorization_header == "Bearer tok"

    def test_custom_token_type(self) -> None:
        result = TokenResult(access_token="tok", expires_in=3600, token_type="Bearer")
        assert result.authorization_header.startswith("Bearer ")


# ---------------------------------------------------------------------------
# load_config — validation and env-var overrides
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_raises_when_client_id_missing(self) -> None:
        with pytest.raises(AuthConfigError, match="client_id"):
            load_config()  # no env vars, no file → both IDs missing

    def test_raises_when_tenant_id_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COPILOT_SCAN_CLIENT_ID", "client-123")
        with pytest.raises(AuthConfigError, match="tenant_id"):
            load_config()

    def test_env_vars_populate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COPILOT_SCAN_CLIENT_ID", "env-client")
        monkeypatch.setenv("COPILOT_SCAN_TENANT_ID", "env-tenant")
        config = load_config()
        assert config.client_id == "env-client"
        assert config.tenant_id == "env-tenant"

    def test_app_secret_without_secret_raises_at_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # load_config already calls _validate_config_for_mode internally
        monkeypatch.setenv("COPILOT_SCAN_CLIENT_ID", "c")
        monkeypatch.setenv("COPILOT_SCAN_TENANT_ID", "t")
        with pytest.raises(AuthConfigError, match="client_secret"):
            load_config(auth_mode=AuthMode.APP_SECRET)

    def test_yaml_file_loads_client_and_tenant(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "client_id: yaml-client\ntenant_id: yaml-tenant\n",
            encoding="utf-8",
        )
        config = load_config(config_path=cfg_file)
        assert config.client_id == "yaml-client"
        assert config.tenant_id == "yaml-tenant"

    def test_env_var_overrides_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "client_id: yaml-client\ntenant_id: yaml-tenant\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("COPILOT_SCAN_CLIENT_ID", "env-wins")
        config = load_config(config_path=cfg_file)
        assert config.client_id == "env-wins"
        assert config.tenant_id == "yaml-tenant"

    def test_raises_for_invalid_yaml(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text("{invalid: [yaml: content", encoding="utf-8")
        with pytest.raises(AuthConfigError, match="YAML"):
            load_config(config_path=cfg_file)

    def test_app_secret_mode_requires_secret(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "client_id: c\ntenant_id: t\n",
            encoding="utf-8",
        )
        with pytest.raises(AuthConfigError, match="client_secret"):
            load_config(config_path=cfg_file, auth_mode=AuthMode.APP_SECRET)


# ---------------------------------------------------------------------------
# CopilotScanAuthenticator — construction and token caching
# ---------------------------------------------------------------------------


class TestCopilotScanAuthenticator:
    def _make_config(self, **kwargs) -> AuthConfig:
        return AuthConfig(client_id="client-xyz", tenant_id="tenant-xyz", **kwargs)

    def test_instantiation_does_not_call_msal(self) -> None:
        """Constructor must not make network calls."""
        with patch("msal.PublicClientApplication") as mock_app:
            config = self._make_config()
            auth = CopilotScanAuthenticator(config)
            mock_app.assert_not_called()
            assert auth._current_token is None  # type: ignore[attr-defined]

    def test_cached_token_returned_without_acquiring(self) -> None:
        config = self._make_config()
        auth = CopilotScanAuthenticator(config)
        fresh = TokenResult(access_token="cached-tok", expires_in=3600)
        auth._current_token = fresh  # type: ignore[attr-defined]

        with patch.object(auth, "_acquire_device_code") as mock_acquire:
            result = auth.acquire_token()
            mock_acquire.assert_not_called()
        assert result.access_token == "cached-tok"

    def test_expired_token_triggers_reacquisition(self) -> None:
        config = self._make_config()
        auth = CopilotScanAuthenticator(config)
        expired = TokenResult(
            access_token="old-tok",
            expires_in=1,
            expires_at=time.time() - 100,  # already expired
        )
        auth._current_token = expired  # type: ignore[attr-defined]

        new_token = TokenResult(access_token="new-tok", expires_in=3600)
        with patch.object(auth, "_acquire_device_code", return_value=new_token):
            result = auth.acquire_token()
        assert result.access_token == "new-tok"

    def test_cache_file_path_stored_on_config(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "token_cache.bin"
        config = self._make_config(cache_file=cache_path)
        assert config.cache_file == cache_path

    def test_get_auth_header_returns_dict(self) -> None:
        config = self._make_config()
        auth = CopilotScanAuthenticator(config)
        fresh = TokenResult(access_token="hdr-tok", expires_in=3600)
        auth._current_token = fresh  # type: ignore[attr-defined]

        with patch.object(auth, "acquire_token", return_value=fresh):
            header = auth.get_auth_header()
        assert header == {"Authorization": "Bearer hdr-tok"}
