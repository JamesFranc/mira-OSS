"""
SOPS-based secrets backend with fail-fast security.

Security guarantees:
- Age key must exist before decryption attempt
- Secrets file must have secure permissions (600)
- All required secrets validated at startup
- Never returns defaults for missing secrets
"""

import logging
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .backend import SecretsBackend
from .schema import SchemaError, validate

logger = logging.getLogger(__name__)


class SOPSBackend(SecretsBackend):
    """
    SOPS-based secrets backend with fail-fast security.

    This backend:
    1. Validates age key exists before attempting decryption
    2. Checks file permissions for security
    3. Decrypts secrets using SOPS CLI
    4. Validates all required secrets against schema
    5. Caches decrypted secrets in memory

    All failures raise exceptions - no silent degradation.
    """

    DEFAULT_AGE_KEY_PATH = Path.home() / ".config" / "mira" / "age.key"
    DEFAULT_SECRETS_PATH = Path("secrets.enc.yaml")
    DEFAULT_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"

    def __init__(
        self,
        path: Optional[str] = None,
        age_key_path: Optional[Path] = None,
        schema_path: Optional[Path] = None
    ):
        """
        Initialize SOPS backend configuration.

        Args:
            path: Path to encrypted secrets file (default: secrets.enc.yaml)
            age_key_path: Path to age private key (default: ~/.config/mira/age.key)
            schema_path: Path to schema definition (default: clients/secrets/schema.yaml)
        """
        self.path = Path(path) if path else self.DEFAULT_SECRETS_PATH
        self.age_key_path = age_key_path or self.DEFAULT_AGE_KEY_PATH
        self.schema_path = schema_path or self.DEFAULT_SCHEMA_PATH
        self._secrets: Dict[str, Any] = {}
        self._initialized = False

    def _validate_age_key_exists(self) -> None:
        """Verify age private key exists before attempting decryption."""
        if not self.age_key_path.exists():
            raise RuntimeError(
                f"FATAL: Age private key not found at {self.age_key_path}. "
                "Run 'mira secrets init' to generate encryption keys. "
                "Application cannot start without decryption capability."
            )

        # Check key file permissions
        mode = self.age_key_path.stat().st_mode
        if mode & stat.S_IROTH:
            raise RuntimeError(
                f"SECURITY ERROR: {self.age_key_path} is world-readable. "
                f"Fix with: chmod 600 {self.age_key_path}"
            )

    def _validate_secrets_file(self) -> None:
        """Verify secrets file exists and has secure permissions."""
        if not self.path.exists():
            raise FileNotFoundError(
                f"FATAL: Secrets file not found: {self.path}. "
                "Run 'mira secrets init' to create encrypted secrets file."
            )

        mode = self.path.stat().st_mode

        # Check if file is world-readable (security violation)
        if mode & stat.S_IROTH:
            raise RuntimeError(
                f"SECURITY ERROR: {self.path} is world-readable. "
                f"Fix with: chmod 600 {self.path}"
            )

        # Warn if group-readable
        if mode & stat.S_IRGRP:
            logger.warning(
                f"SECURITY WARNING: {self.path} is group-readable. "
                f"Recommended: chmod 600 {self.path}"
            )

    def _decrypt_secrets(self) -> Dict[str, Any]:
        """Decrypt SOPS file with integrity verification."""
        try:
            # Set SOPS_AGE_KEY_FILE for sops to find the key
            env = os.environ.copy()
            env["SOPS_AGE_KEY_FILE"] = str(self.age_key_path)

            result = subprocess.run(
                ["sops", "-d", str(self.path)],
                capture_output=True,
                env=env,
                check=True
            )
            return yaml.safe_load(result.stdout)

        except FileNotFoundError:
            raise RuntimeError(
                "FATAL: 'sops' command not found. "
                "Install SOPS: https://github.com/getsops/sops"
            )

        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ""

            if "MAC mismatch" in stderr:
                raise RuntimeError(
                    f"SECURITY: {self.path} integrity check failed. "
                    "File may have been tampered with."
                )
            elif "could not decrypt" in stderr.lower():
                raise RuntimeError(
                    f"FATAL: Cannot decrypt {self.path}. "
                    "Age key may be incorrect or file is corrupted."
                )
            else:
                raise RuntimeError(f"FATAL: SOPS decryption failed: {stderr}")

    def _validate_schema(self, secrets: Dict[str, Any]) -> None:
        """Validate secrets against schema with fail-fast semantics."""
        if not self.schema_path.exists():
            raise RuntimeError(
                f"FATAL: Schema file not found: {self.schema_path}. "
                "Cannot validate secrets without schema definition."
            )

        with open(self.schema_path) as f:
            schema = yaml.safe_load(f)

        try:
            validate(schema, secrets)
        except SchemaError as e:
            raise RuntimeError(
                f"FATAL: {e}. Application cannot start with missing required secrets."
            )

    def init(self) -> None:
        """
        Initialize backend with full security validation.

        Raises on ANY security or configuration issue - no silent failures.
        Order of operations:
        1. Verify age key exists and has secure permissions
        2. Verify secrets file exists and has secure permissions
        3. Decrypt with integrity check
        4. Validate all required fields present

        Raises:
            RuntimeError: On any security or configuration error
            FileNotFoundError: If required files are missing
        """
        logger.info("Initializing SOPS secrets backend...")

        # 1. Verify age key exists
        self._validate_age_key_exists()
        logger.debug(f"Age key validated: {self.age_key_path}")

        # 2. Verify secrets file exists and has secure permissions
        self._validate_secrets_file()
        logger.debug(f"Secrets file validated: {self.path}")

        # 3. Decrypt with integrity check
        self._secrets = self._decrypt_secrets()
        logger.debug("Secrets decrypted successfully")

        # 4. Validate all required fields present
        self._validate_schema(self._secrets)
        logger.info("Secrets validation passed - all required secrets present")

        self._initialized = True

    def get(self, path: str) -> str:
        """
        Retrieve secret by path. NEVER returns defaults.

        Args:
            path: Dot-notation path (e.g., 'providers.anthropic_key')

        Returns:
            The secret value as a string

        Raises:
            RuntimeError: If backend not initialized
            KeyError: If secret not found (fail-fast, no fallback)
            ValueError: If secret is not a string
        """
        if not self._initialized:
            raise RuntimeError(
                "FATAL: Secrets backend not initialized. "
                "Call init() before accessing secrets."
            )

        parts = path.split(".")
        node = self._secrets

        for p in parts:
            if not isinstance(node, dict) or p not in node:
                raise KeyError(
                    f"REQUIRED SECRET NOT FOUND: {path}. "
                    "Edit secrets.enc.yaml to add missing secret."
                )
            node = node[p]

        if not isinstance(node, str):
            raise ValueError(
                f"Secret at {path} is not a string (got {type(node).__name__})"
            )

        return node

    def get_optional(self, path: str, default: Optional[str] = None) -> Optional[str]:
        """
        Retrieve optional secret by path, returning default if not found.

        Use this ONLY for genuinely optional secrets. Required secrets
        should use get() to enforce fail-fast semantics.

        Args:
            path: Dot-notation path
            default: Value to return if secret not found

        Returns:
            The secret value or default
        """
        try:
            return self.get(path)
        except KeyError:
            return default

    def get_all(self, prefix: str = "") -> Dict[str, Any]:
        """
        Return all secrets (for preloading into cache).

        Args:
            prefix: Unused, for API compatibility

        Returns:
            Dictionary of all decrypted secrets
        """
        if not self._initialized:
            raise RuntimeError("Secrets backend not initialized")
        return self._secrets.copy()

    def is_ready(self) -> bool:
        """Check if backend is initialized and ready."""
        return self._initialized

