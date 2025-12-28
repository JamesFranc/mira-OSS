"""
Client modules for external service integrations.
"""

from .hybrid_embeddings_provider import get_hybrid_embeddings_provider, HybridEmbeddingsProvider
from .llm_provider import LLMProvider
from .postgres_client import PostgresClient
from .sqlite_client import SQLiteClient
from .valkey_client import ValkeyClient, get_valkey, get_valkey_client

# Secrets management - SOPS-based (replaces vault_client)
from .secrets.compat import (
    get_api_key,
    get_auth_secret,
    get_database_credentials,
    get_database_url,
    get_service_config,
    preload_secrets,
    test_vault_connection,
)

__all__ = [
    'HybridEmbeddingsProvider',
    'get_hybrid_embeddings_provider',
    'LLMProvider',
    'PostgresClient',
    'SQLiteClient',
    'ValkeyClient',
    'get_valkey',
    'get_valkey_client',
    # Secrets management (SOPS-based)
    'get_api_key',
    'get_auth_secret',
    'get_database_credentials',
    'get_database_url',
    'get_service_config',
    'preload_secrets',
    'test_vault_connection',
]
