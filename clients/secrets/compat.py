"""
Vault compatibility layer for SOPS backend.

Provides the same API surface as vault_client.py to enable gradual migration.
All functions delegate to the SOPS backend instead of HashiCorp Vault.

Usage:
    # Replace this:
    from clients.vault_client import get_api_key, get_database_url
    
    # With this:
    from clients.secrets.compat import get_api_key, get_database_url

The function signatures and return values are identical.
"""

import logging
from typing import Any, Dict

from . import get_secrets_backend

logger = logging.getLogger(__name__)

# Cache for compatibility with vault_client behavior
_secret_cache: Dict[str, str] = {}


def get_database_url(service: str, admin: bool = False) -> str:
    """
    Get database URL for a service.

    Args:
        service: Database service name (only 'mira_service' supported)
        admin: If True, returns admin connection string (BYPASSRLS role)

    Returns:
        PostgreSQL connection URL
    """
    if service != "mira_service":
        raise ValueError(f"Unknown database service: '{service}'. Only 'mira_service' is supported.")

    field = "admin_url" if admin else "service_url"
    cache_key = f"database/{field}"

    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    backend = get_secrets_backend()
    value = backend.get(f"database.{field}")
    _secret_cache[cache_key] = value
    return value


def get_api_key(key_name: str) -> str:
    """
    Get API key by name.

    Args:
        key_name: Name of the API key (e.g., 'anthropic_key', 'mira_api')

    Returns:
        The API key value
    """
    cache_key = f"providers/{key_name}"

    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    backend = get_secrets_backend()

    # Handle auth keys vs provider keys
    if key_name in ("mira_api", "jwt_secret"):
        value = backend.get(f"auth.{key_name}")
    else:
        value = backend.get(f"providers.{key_name}")

    _secret_cache[cache_key] = value
    return value


def get_auth_secret(secret_name: str) -> str:
    """
    Get authentication secret by name.

    Args:
        secret_name: Name of the auth secret

    Returns:
        The secret value
    """
    cache_key = f"auth/{secret_name}"

    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    backend = get_secrets_backend()
    value = backend.get(f"auth.{secret_name}")
    _secret_cache[cache_key] = value
    return value


def get_service_config(service: str, field: str) -> str:
    """
    Get service configuration value.

    Args:
        service: Service name (ignored, for compatibility)
        field: Configuration field name

    Returns:
        The configuration value
    """
    cache_key = f"services/{field}"

    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    backend = get_secrets_backend()
    value = backend.get(f"services.{field}")
    _secret_cache[cache_key] = value
    return value


def get_database_credentials() -> Dict[str, str]:
    """
    Get database username and password.

    Returns:
        Dictionary with 'username' and 'password' keys
    """
    backend = get_secrets_backend()
    return {
        "username": backend.get("database.username"),
        "password": backend.get("database.password"),
    }


def preload_secrets() -> None:
    """
    Preload all secrets into memory cache.

    With SOPS backend, secrets are already loaded and cached at init().
    This function exists for API compatibility with vault_client.
    """
    backend = get_secrets_backend()
    all_secrets = backend.get_all()

    # Flatten nested secrets into cache format
    def flatten(d: Dict[str, Any], prefix: str = "") -> None:
        for key, value in d.items():
            full_key = f"{prefix}/{key}" if prefix else key
            if isinstance(value, dict):
                flatten(value, full_key)
            else:
                _secret_cache[full_key] = value

    flatten(all_secrets)
    logger.info(f"Preloaded {len(_secret_cache)} secrets into cache")


def test_vault_connection() -> Dict[str, Any]:
    """
    Test secrets backend connection.

    Returns:
        Status dictionary with 'status', 'message', and 'authenticated' keys
    """
    try:
        backend = get_secrets_backend()
        # Try reading a required secret to verify
        backend.get("database.username")

        return {
            "status": "success",
            "message": "SOPS secrets backend ready",
            "authenticated": True,
        }

    except Exception as e:
        logger.error(f"Secrets backend test failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "authenticated": False,
        }

