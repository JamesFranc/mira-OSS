"""
Abstract base class for secrets backends.

This module defines the interface that all secrets backends must implement.
The interface is designed with fail-fast semantics - backends must raise
exceptions rather than returning defaults on missing secrets.
"""

import abc
from typing import Any, Dict


class SecretsBackend(abc.ABC):
    """
    Abstract base class for secrets backends.

    All implementations must follow fail-fast semantics:
    - init() must raise on configuration errors
    - get() must raise KeyError on missing secrets (never return defaults)
    - is_ready() indicates whether init() completed successfully
    """

    @abc.abstractmethod
    def init(self) -> None:
        """
        Initialize the backend (load keys, authenticate, decrypt, validate).

        Must perform all validation and raise exceptions on any failure.
        After successful init(), all secrets are guaranteed available.

        Raises:
            RuntimeError: On configuration or security errors
            FileNotFoundError: On missing required files
        """

    @abc.abstractmethod
    def get(self, path: str) -> str:
        """
        Retrieve a secret value by logical path (e.g., 'providers.anthropic_key').

        Args:
            path: Dot-notation path to the secret

        Returns:
            The secret value as a string

        Raises:
            RuntimeError: If backend not initialized
            KeyError: If secret not found (fail-fast, no fallback)
            ValueError: If secret is not a string
        """

    def get_all(self, prefix: str = "") -> Dict[str, Any]:
        """
        Bulk retrieval of all secrets.

        Args:
            prefix: Optional prefix to filter secrets

        Returns:
            Dictionary of all secrets

        Raises:
            RuntimeError: If backend not initialized
            NotImplementedError: If bulk retrieval not supported
        """
        raise NotImplementedError("Bulk retrieval not supported by this backend")

    def is_ready(self) -> bool:
        """
        Check if the backend is initialized and ready for use.

        Returns:
            True if init() completed successfully, False otherwise
        """
        return False

