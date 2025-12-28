"""
Tests for clients/secrets/compat.py - Vault compatibility layer.

Tests the API compatibility functions that wrap the SOPS backend.
Uses mocking to test the compat layer logic without requiring actual secrets.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestGetDatabaseUrl:
    """Test get_database_url compatibility function."""

    def test_returns_service_url_by_default(self):
        """Returns service URL when admin=False."""
        from clients.secrets import compat

        # Clear cache for clean test
        compat._secret_cache.clear()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "postgresql://user:pass@localhost/db"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result = compat.get_database_url("mira_service")

        assert result == "postgresql://user:pass@localhost/db"
        mock_backend.get.assert_called_with("database.service_url")

    def test_returns_admin_url_when_admin_true(self):
        """Returns admin URL when admin=True."""
        from clients.secrets import compat

        compat._secret_cache.clear()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "postgresql://admin:pass@localhost/db"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result = compat.get_database_url("mira_service", admin=True)

        assert result == "postgresql://admin:pass@localhost/db"
        mock_backend.get.assert_called_with("database.admin_url")

    def test_raises_on_unknown_service(self):
        """Raises ValueError for unknown database service."""
        from clients.secrets.compat import get_database_url

        with pytest.raises(ValueError, match="Unknown database service"):
            get_database_url("unknown_service")

    def test_caches_result_on_subsequent_calls(self):
        """Cached value is returned on subsequent calls."""
        from clients.secrets import compat

        compat._secret_cache.clear()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "postgresql://user:pass@localhost/db"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result1 = compat.get_database_url("mira_service")
            result2 = compat.get_database_url("mira_service")

        assert result1 == result2
        # Should only call backend once due to caching
        assert mock_backend.get.call_count == 1


class TestGetApiKey:
    """Test get_api_key compatibility function."""

    def test_returns_provider_key(self):
        """Returns API key from providers section."""
        from clients.secrets import compat

        compat._secret_cache.clear()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "sk-ant-123456"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result = compat.get_api_key("anthropic_key")

        assert result == "sk-ant-123456"
        mock_backend.get.assert_called_with("providers.anthropic_key")

    def test_returns_auth_key_for_mira_api(self):
        """Returns mira_api from auth section, not providers."""
        from clients.secrets import compat

        compat._secret_cache.clear()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "mira_abc123"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result = compat.get_api_key("mira_api")

        assert result == "mira_abc123"
        mock_backend.get.assert_called_with("auth.mira_api")

    def test_returns_auth_key_for_jwt_secret(self):
        """Returns jwt_secret from auth section."""
        from clients.secrets import compat

        compat._secret_cache.clear()

        mock_backend = MagicMock()
        mock_backend.get.return_value = "jwt-secret-value"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result = compat.get_api_key("jwt_secret")

        assert result == "jwt-secret-value"
        mock_backend.get.assert_called_with("auth.jwt_secret")


class TestTestVaultConnection:
    """Test test_vault_connection compatibility function."""

    def test_returns_success_when_backend_works(self):
        """Returns success status when backend is functional."""
        from clients.secrets import compat

        mock_backend = MagicMock()
        mock_backend.get.return_value = "test_user"

        with patch.object(compat, 'get_secrets_backend', return_value=mock_backend):
            result = compat.test_vault_connection()

        assert result["status"] == "success"
        assert result["authenticated"] is True

    def test_returns_error_when_backend_fails(self):
        """Returns error status when backend raises."""
        from clients.secrets import compat

        with patch.object(compat, 'get_secrets_backend', side_effect=RuntimeError("Failed")):
            result = compat.test_vault_connection()

        assert result["status"] == "error"
        assert result["authenticated"] is False
        assert "Failed" in result["message"]

