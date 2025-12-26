"""
SOPS-based secrets management for MIRA-OSS.

This module provides encrypted secrets storage using SOPS with age encryption.
It replaces the previous HashiCorp Vault integration with a simpler, file-based
approach that provides equivalent security for static API keys.

Usage:
    from clients.secrets import get_secrets_backend, create_backend

    # Option 1: Use singleton (recommended for application code)
    secrets = get_secrets_backend()
    api_key = secrets.get("providers.anthropic_key")

    # Option 2: Create new instance (for testing or custom paths)
    backend = create_backend(path="custom/secrets.enc.yaml")
    backend.init()
    api_key = backend.get("providers.anthropic_key")

Security guarantees:
    - Fail-fast: Application refuses to start without valid secrets
    - Encrypted at rest: All secrets encrypted with age
    - Schema validation: Required secrets enforced at startup
    - No server dependency: Works offline, no network required
"""

import logging
from pathlib import Path
from typing import Optional

from .backend import SecretsBackend
from .schema import SchemaError, get_required_fields, validate
from .sops_backend import SOPSBackend

logger = logging.getLogger(__name__)

# Global singleton instance
_secrets_backend: Optional[SOPSBackend] = None


def create_backend(
    path: Optional[str] = None,
    age_key_path: Optional[Path] = None,
    schema_path: Optional[Path] = None
) -> SOPSBackend:
    """
    Create a new SOPS backend instance.

    Use this for testing or when you need a custom configuration.
    For normal application code, use get_secrets_backend() instead.

    Args:
        path: Path to encrypted secrets file
        age_key_path: Path to age private key
        schema_path: Path to schema definition

    Returns:
        Uninitialized SOPSBackend instance (call init() before use)
    """
    return SOPSBackend(
        path=path,
        age_key_path=age_key_path,
        schema_path=schema_path
    )


def get_secrets_backend() -> SOPSBackend:
    """
    Get the global secrets backend singleton.

    Initializes on first call. Thread-safe for reads after initialization.
    Raises on any configuration or security error.

    Returns:
        Initialized SOPSBackend instance

    Raises:
        RuntimeError: If initialization fails
    """
    global _secrets_backend

    if _secrets_backend is None:
        _secrets_backend = create_backend()
        _secrets_backend.init()
        logger.info("Secrets backend initialized successfully")

    return _secrets_backend


def initialize_secrets() -> SOPSBackend:
    """
    Initialize the secrets backend with fail-fast semantics.

    Call this at application startup. If this returns successfully,
    all required secrets are guaranteed available.

    Returns:
        Initialized secrets backend

    Raises:
        RuntimeError: With descriptive error if secrets are misconfigured
    """
    return get_secrets_backend()


# Re-export commonly used items
__all__ = [
    # Classes
    "SecretsBackend",
    "SOPSBackend",
    "SchemaError",
    # Factory functions
    "create_backend",
    "get_secrets_backend",
    "initialize_secrets",
    # Utilities
    "validate",
    "get_required_fields",
]

