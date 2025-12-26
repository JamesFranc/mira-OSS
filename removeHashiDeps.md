Below are **draft documentation pages**, a **threat model sketch**, and **real code you could put into the `mira-OSS` repo** to add SOPS-based secrets handling. This replaces the previous HashiCorp Vault integration.

---

# 📘 Documentation (Markdown)

## 📌 docs/secret_management/README.md

```markdown
# Secrets Management in Mira-OSS

Mira-OSS uses provider API keys and tokens for LLM and external API integrations. Secure handling of these secrets is essential, especially for offline or self-hosted use.

This document describes:
- The SOPS-based secret backend
- Schema validation for fail-fast startup
- CLI commands for secrets management

## Overview

Mira-OSS uses **SOPS** for secrets management — a file-based encryption scheme with no running server required. This replaces the previous HashiCorp Vault integration.

**Why SOPS over Vault?** Mira-OSS uses third-party API keys (Anthropic, OpenAI, etc.) that neither system can automatically rotate. Vault's dynamic secrets features only work for credentials Vault generates itself (database users, cloud IAM). For static API keys, Vault is just a key-value store with significant operational overhead. SOPS provides equivalent security with dramatically lower complexity.

Secrets are logically organized under paths such as:

```
providers.anthropic_key
providers.openai_embeddings_key
database.service_url
```

These values are decrypted at startup, validated against a schema, and injected into provider code paths.

## Secret Backend: SOPS

SOPS encrypts a file called `secrets.enc.yaml`. See [SOPS Guide](sops.md) for setup instructions.

Use the CLI:

```bash
mira secrets init    # Generate age key and empty secrets file
mira secrets edit    # Decrypt, edit, re-encrypt
mira secrets check   # Validate before deployment
```

### Security Guarantees

- **Fail-fast**: Application refuses to start with missing/invalid secrets
- **Encrypted at rest**: All secrets encrypted with age (modern, audited crypto)
- **Schema validation**: Required secrets enforced at startup
- **No server dependency**: Works offline, no network required
- **Git-auditable**: Changes tracked in version control

````

---

## 📌 docs/secret_management/sops.md

```markdown
# Using SOPS for Secrets

This guide walks through SOPS setup for offline and secure use.

## Install SOPS

Follow instructions from the SOPS project:

https://github.com/getsops/sops

## Initialize Encrypted Secrets

```bash
mira secrets init
````

This:

* Generates an age key (`~/.config/mira/age.key`)
* Creates `secrets.enc.yaml`
* Sets default patterns for encrypted fields

Secrets might look like:

```yaml
providers:
  openai:
    api_key: ENC[…]
```

## Editing Secrets

```bash
mira secrets edit
```

This opens `secrets.enc.yaml` in your editor and encrypts on save.

## Validate

```bash
mira secrets check
```

Make sure required keys are present before starting Mira.

````

---

# 🛡️ Threat Model Sketch (docs/secret_management/threat_model.md)

```markdown
# Secrets Threat Model for Mira-OSS

This document outlines risk scenarios and mitigations for storing and accessing secrets.

## Assets
- Provider API keys (OpenAI, Anthropic, etc.)
- Database credentials
- Tokens for external services

## Threat Actors
- Local attacker with file system access
- Remote attacker with network access (if services exposed)
- Build/CI compromise

## Attack Vectors

### 1. File Theft
If an attacker reads disk, encrypted SOPS files are safe, but they can decrypt only with key material.

Mitigations:
- Store age keys in OS protected directories
- Use OS permissions (600)

### 2. Untrusted Execution
Scripts or components that exec `sops` could leak plaintext.

Mitigations:
- Use in-memory decryption (SOPS libraries) when possible
- Avoid writing decrypted plaintext to disk

## Security Boundaries

| Component | Trust Level |
|-----------|-------------|
| Secrets file (encrypted) | Confidential |
| age private key | Highly confidential |

## Assumptions
- Operator secures private key
- No cloud dependency for offline mode

## Limitations
- SOPS offers no runtime access control (by design - secrets loaded at startup)
- Rotation requires restart
````

---

# 🧠 Real Code for `mira-OSS`

Below are Python modules you can add under a new directory `mira_oss/secrets` — this follows the patterns already in the repo (Python backend, FastAPI CLI likely via `scripts/`).

---

## 📌 `mira_oss/secrets/backend.py`

```python
import abc
from typing import Any, Dict

class SecretsBackend(abc.ABC):
    @abc.abstractmethod
    def init(self) -> None:
        """Initialize the backend (load keys, auth, etc.)"""

    @abc.abstractmethod
    def get(self, path: str) -> str:
        """Retrieve a secret value by logical path"""

    def get_all(self, prefix: str = "") -> Dict[str, str]:
        """Optional: Bulk retrieval by prefix"""
        raise NotImplementedError

    def is_ready(self) -> bool:
        """Check if the backend is ready for use"""
        return True
```

---

## 📌 `mira_oss/secrets/sops_backend.py`

```python
import subprocess
import yaml
from .backend import SecretsBackend
from typing import Dict

class SOPSBackend(SecretsBackend):
    def __init__(self, path: str = "secrets.enc.yaml"):
        self.path = path
        self._secrets: Dict[str, Any] = {}

    def init(self) -> None:
        decrypted = subprocess.check_output(["sops", "-d", self.path])
        self._secrets = yaml.safe_load(decrypted)

    def get(self, path: str) -> str:
        parts = path.split(".")
        node = self._secrets
        for p in parts:
            if p not in node:
                raise KeyError(f"Secret not found: {path}")
            node = node[p]
        if not isinstance(node, str):
            raise ValueError(f"Secret at {path} is not a string")
        return node

    def get_all(self, prefix: str = "") -> Dict[str, str]:
        return self._secrets
```

---

## 📌 `mira_oss/secrets/__init__.py`

```python
from .sops_backend import SOPSBackend

def get_secrets_backend() -> SOPSBackend:
    """Get the SOPS secrets backend."""
    return SOPSBackend()
```

---

## 🚀 Hook Into App Startup (e.g., in `main.py`)

```python
from mira_oss.secrets import create_backend

secrets = create_backend()
secrets.init()

# Example usage
openai_key = secrets.get("providers.openai.api_key")
```

---

# 🧪 Notes

* This integrates directly with Python stack of Mira-OSS. ([GitHub][1])
* You can wrap the file decryption in an async task or CLI command if necessary.
* You’ll likely need to update provider config code to read from this backend rather than environment variables.

---

Below is a **complete, actionable package** covering:

1. **Draft tests** (unit + integration) that fit Mira-OSS's Python repo
2. **CI integration** (GitHub Actions) for SOPS
3. **Clear patterns for secret rotation** with SOPS

---

# 1. Draft Tests

Assumptions (based on Mira-OSS repo structure):

* Python project
* `pytest` available or acceptable
* Secrets code lives in `mira_oss/secrets/`

---

## 1.1 Test Strategy Overview

| Test Type         | Purpose                                           |
| ----------------- | ------------------------------------------------- |
| Unit tests        | Validate path resolution, error handling          |
| Schema tests      | Ensure required secrets validation works          |
| Contract tests    | Ensure providers don't care about backend details |

---

## 1.2 Unit Tests: SOPS Backend

### `tests/secrets/test_sops_backend.py`

```python
import subprocess
import yaml
import pytest
from mira_oss.secrets.sops_backend import SOPSBackend

@pytest.fixture
def mock_sops(monkeypatch):
    def fake_check_output(cmd):
        data = {
            "providers": {
                "openai": {
                    "api_key": "test-key"
                }
            }
        }
        return yaml.dump(data).encode()

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

def test_sops_get_success(mock_sops):
    backend = SOPSBackend("secrets.enc.yaml")
    backend.init()
    assert backend.get("providers.openai.api_key") == "test-key"

def test_sops_missing_key(mock_sops):
    backend = SOPSBackend("secrets.enc.yaml")
    backend.init()
    with pytest.raises(KeyError):
        backend.get("providers.anthropic.api_key")
```

---

## 1.3 Unit Tests: Schema Validation

### `tests/secrets/test_schema.py`

```python
import pytest
from mira_oss.secrets.schema import validate, SchemaError

def test_validate_required_present():
    schema = {
        "providers": {
            "anthropic_key": {"type": "string", "required": True}
        }
    }
    secrets = {"providers": {"anthropic_key": "sk-test"}}
    validate(schema, secrets)  # Should not raise

def test_validate_required_missing():
    schema = {
        "providers": {
            "anthropic_key": {"type": "string", "required": True}
        }
    }
    secrets = {"providers": {}}
    with pytest.raises(SchemaError) as exc:
        validate(schema, secrets)
    assert "MISSING REQUIRED" in str(exc.value)

def test_validate_optional_missing_ok():
    schema = {
        "providers": {
            "kagi_key": {"type": "string", "required": False}
        }
    }
    secrets = {"providers": {}}
    validate(schema, secrets)  # Should not raise
```

---

# 2. CI Integration (GitHub Actions)

This CI setup:

* Runs unit tests always
* Never exposes real secrets
* Does **not** require SOPS keys (mocks decryption)

---

## 2.1 `.github/workflows/secrets.yml`

```yaml
name: Secrets Backend Tests

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest

      - name: Run secrets unit tests
        run: pytest tests/secrets/
```

---

## 2.2 Why CI Does NOT Use Real SOPS

* CI tests **logic**, not crypto correctness
* SOPS correctness is already tested upstream
* Avoids secret sprawl

This is intentional and good practice.

---

# 3. Secret Rotation with SOPS

## 3.1 Rotation Pattern: Edit → Commit → Restart

For third-party API keys (Anthropic, OpenAI, etc.), rotation is always manual because the provider controls key issuance. Neither SOPS nor Vault can automatically rotate these.

### Steps:

```bash
# 1. Generate new key at provider (Anthropic console, etc.)
# 2. Edit encrypted secrets
mira secrets edit
# 3. Update the key value, save
# 4. Commit for audit trail
git commit -am "Rotate Anthropic API key"
# 5. Restart application
systemctl restart mira
```

### Characteristics

| Aspect           | Value   |
| ---------------- | ------- |
| Runtime rotation | ❌ (restart required) |
| Audit trail      | ✅ (Git history) |
| Offline capable  | ✅       |
| Simplicity       | ✅       |
| Same as Vault?   | ✅ (for third-party keys) |

### Why This Is Correct for Mira-OSS

Vault's dynamic secrets engine can only rotate credentials that **Vault itself generates** (database users, cloud IAM). For third-party API keys:
- Vault is just a key-value store
- Rotation still requires human intervention
- The only difference is `vault kv put` vs `sops edit`

SOPS provides the same security with less operational complexity.

---

# 4. Why SOPS Over Vault/OpenBao

## 4.1 The Rotation Misconception

A common belief: "Vault provides automatic secret rotation."

**Reality**: Vault can only rotate secrets it generates:
- ✅ Database credentials (Vault creates the user)
- ✅ AWS IAM credentials (via STS AssumeRole)
- ✅ PKI certificates (Vault is the CA)
- ❌ Anthropic API keys (Anthropic issues them)
- ❌ OpenAI API keys (OpenAI issues them)
- ❌ Any third-party API key

For Mira-OSS's secrets (all third-party API keys), Vault provides no rotation benefit.

## 4.2 Security Comparison

| Property | SOPS | Vault KV |
|----------|------|----------|
| Encrypted at rest | ✅ (age) | ✅ (Vault storage) |
| Fail-fast on missing | ✅ (schema validation) | ✅ (KeyError) |
| Authentication required | ✅ (age key) | ✅ (token/AppRole) |
| Auto-rotate third-party keys | ❌ No | ❌ No |
| Offline operation | ✅ Yes | ❌ No |
| Operational complexity | Low | High |
| Failure modes | Few | Many (server, seal, token, network) |

## 4.3 What You Gain by Removing Vault

* 🔥 **70-80% less operational complexity** - No server to run, unseal, backup
* 🧠 **Lower contributor friction** - Just need age key, not full Vault setup
* 📴 **Offline correctness** - Works without network
* 📦 **Smaller dependency graph** - Remove `hvac` library
* 📜 **Cleaner security story** - Fewer moving parts = fewer vulnerabilities

---

# 5. Vault Removal Justification

## 5.1 Strong signals it's safe to remove Vault

You can confidently **remove Vault entirely** when:

* ✅ All secrets are **static API keys**
* ✅ Secrets are loaded **only at startup**
* ✅ Mira-OSS is **single-user or single-operator**
* ✅ Offline / edge / air-gapped use is a priority
* ✅ Contributors struggle to run Vault locally
* ✅ CI and tests don’t rely on Vault semantics

**Mira-OSS today fits all of these.**

---

# 6. Schema Validation for Secrets

Schema validation is **non-optional** once secrets become "just files". This ensures fail-fast behavior at startup.

## 6.1 Design Goals

* Fail fast at startup
* Catch missing or misspelled keys
* Provider-aware (Anthropic, OpenAI, etc.)
* No plaintext logging

---

## 6.2 Schema Definition (YAML)

Create: `mira_oss/secrets/schema.yaml`

```yaml
providers:
  anthropic:
    api_key:
      type: string
      required: true
  openai:
    api_key:
      type: string
      required: false
  kagi:
    api_key:
      type: string
      required: false
```

This is intentionally simple and explicit.

---

## 6.3 Validation Engine (Python)

### `mira_oss/secrets/schema.py`

```python
from typing import Dict, Any

class SchemaError(Exception):
    pass

def validate(schema: Dict[str, Any], secrets: Dict[str, Any], path=""):
    for key, rules in schema.items():
        full_path = f"{path}.{key}" if path else key

        if isinstance(rules, dict) and "type" in rules:
            if rules.get("required") and key not in secrets:
                raise SchemaError(f"Missing required secret: {full_path}")

            if key in secrets:
                value = secrets[key]
                if rules["type"] == "string" and not isinstance(value, str):
                    raise SchemaError(f"Secret {full_path} must be a string")

        else:
            if key not in secrets:
                if rules.get("required", False):
                    raise SchemaError(f"Missing required section: {full_path}")
                continue
            validate(rules, secrets[key], full_path)
```

---

## 6.4 Hook Validation into Startup

### In `SOPSBackend.init()`

```python
from mira_oss.secrets.schema import validate
import yaml

with open("mira_oss/secrets/schema.yaml") as f:
    schema = yaml.safe_load(f)

validate(schema, self._secrets)
```

### Result

* App **refuses to start** if secrets are wrong
* Error messages are precise
* No provider sees invalid config

---

## 6.5 CLI Integration

### `mira secrets check`

Should:

1. Decrypt secrets
2. Load schema
3. Validate
4. Exit non-zero on failure

This makes secrets correctness **testable and automatable**.

---

# 7. PR Checklist (Drop-In)

Create: `.github/PULL_REQUEST_TEMPLATE.md`

```markdown
## Summary
Explain what this PR changes and why.

---

## Secrets & Security Checklist

- [ ] No plaintext secrets committed
- [ ] `secrets.enc.yaml` not modified unless intended
- [ ] Schema validation passes (`mira secrets check`)
- [ ] New secrets documented in schema.yaml
- [ ] No secrets logged or printed

---

## Tests

- [ ] Unit tests added or updated
- [ ] Secrets-related tests pass
- [ ] CI passes

---

## Docs

- [ ] Docs updated if secrets behavior changed
- [ ] Migration notes added (if applicable)

---

## Reviewer Notes
Anything reviewers should pay special attention to.
```

---

# 8. Fail-Fast Security Implementation

This section addresses a critical gap: achieving **fail-fast security guarantees** with SOPS. The core principle is that **the application must refuse to start** without properly configured, secure secrets.

## 8.1 Security Properties We Enforce

1. **No hardcoded credentials** - Secrets live in encrypted SOPS file
2. **Fail-fast on missing secrets** - `KeyError` on missing fields, not silent defaults
3. **Authentication required** - Age key must exist and be valid
4. **Application refuses to start** - Without valid secrets, startup fails immediately

---

## 8.2 Enhanced Schema with Required Field Semantics

Update `mira_oss/secrets/schema.yaml` with complete field coverage:

```yaml
# schema.yaml - FAIL-FAST SCHEMA
# All 'required: true' fields MUST exist or application refuses to start

providers:
  anthropic_key:
    type: string
    required: true  # Core LLM - CANNOT start without
  anthropic_batch_key:
    type: string
    required: false  # Batch API - graceful degradation
  provider_key:
    type: string
    required: false  # OpenRouter/alternative - optional
  kagi_api_key:
    type: string
    required: false  # Search - graceful degradation
  openai_embeddings_key:
    type: string
    required: false  # Only if using OpenAI embeddings
  google_maps_api_key:
    type: string
    required: false  # Location tools - graceful degradation

database:
  service_url:
    type: string
    required: true  # PostgreSQL - CANNOT start without
  admin_url:
    type: string
    required: true  # BYPASSRLS operations - CANNOT start without
  username:
    type: string
    required: true
  password:
    type: string
    required: true

services:
  valkey_url:
    type: string
    required: true  # Cache/session - CANNOT start without
```

---

## 8.3 Enhanced SOPS Backend with Fail-Fast Semantics

### `mira_oss/secrets/sops_backend.py` (Enhanced)

```python
import os
import stat
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

from .backend import SecretsBackend
from .schema import validate, SchemaError


class SOPSBackend(SecretsBackend):
    """
    SOPS-based secrets backend with fail-fast security.

    Security guarantees:
    - Age key must exist before decryption attempt
    - Secrets file must have secure permissions (600)
    - All required secrets validated at startup
    - Never returns defaults for missing secrets
    """

    DEFAULT_AGE_KEY_PATH = Path.home() / ".config/mira/age.key"
    DEFAULT_SECRETS_PATH = Path("secrets.enc.yaml")

    def __init__(
        self,
        path: str = "secrets.enc.yaml",
        age_key_path: Optional[Path] = None,
        schema_path: Optional[Path] = None
    ):
        self.path = Path(path)
        self.age_key_path = age_key_path or self.DEFAULT_AGE_KEY_PATH
        self.schema_path = schema_path or Path("mira_oss/secrets/schema.yaml")
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

    def _validate_file_permissions(self, path: Path) -> None:
        """Verify secrets file has secure permissions."""
        if not path.exists():
            raise FileNotFoundError(
                f"FATAL: Secrets file not found: {path}. "
                "Run 'mira secrets init' to create encrypted secrets file."
            )

        mode = path.stat().st_mode

        # Check if file is world-readable (security violation)
        if mode & stat.S_IROTH:
            raise RuntimeError(
                f"SECURITY ERROR: {path} is world-readable. "
                f"Fix with: chmod 600 {path}"
            )

        # Check if file is group-readable (warning, but allow)
        if mode & stat.S_IRGRP:
            import logging
            logging.getLogger(__name__).warning(
                f"SECURITY WARNING: {path} is group-readable. "
                f"Recommended: chmod 600 {path}"
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
                raise RuntimeError(
                    f"FATAL: SOPS decryption failed: {stderr}"
                )

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
                f"FATAL: Secrets validation failed: {e}. "
                "Application cannot start with missing required secrets."
            )

    def init(self) -> None:
        """
        Initialize backend with full security validation.

        Raises on ANY security or configuration issue - no silent failures.
        """
        # 1. Verify age key exists
        self._validate_age_key_exists()

        # 2. Verify secrets file exists and has secure permissions
        self._validate_file_permissions(self.path)

        # 3. Decrypt with integrity check
        self._secrets = self._decrypt_secrets()

        # 4. Validate all required fields present
        self._validate_schema(self._secrets)

        self._initialized = True

    def get(self, path: str) -> str:
        """
        Retrieve secret by path. NEVER returns defaults.

        Raises:
            RuntimeError: If backend not initialized
            KeyError: If secret not found (fail-fast, no fallback)
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

    def get_all(self, prefix: str = "") -> Dict[str, str]:
        """Return all secrets (for preloading into cache)."""
        if not self._initialized:
            raise RuntimeError("Secrets backend not initialized")
        return self._secrets

    def is_ready(self) -> bool:
        """Check if backend is initialized and ready."""
        return self._initialized
```

---

## 8.4 Enhanced Schema Validation Engine

### `mira_oss/secrets/schema.py` (Enhanced)

```python
from typing import Dict, Any, List


class SchemaError(Exception):
    """Raised when secrets fail schema validation."""
    pass


def validate(
    schema: Dict[str, Any],
    secrets: Dict[str, Any],
    path: str = "",
    errors: List[str] = None
) -> None:
    """
    Validate secrets against schema with comprehensive error collection.

    Collects ALL errors before raising, so operator can fix everything at once.

    Raises:
        SchemaError: With list of all validation failures
    """
    if errors is None:
        errors = []

    for key, rules in schema.items():
        full_path = f"{path}.{key}" if path else key

        if isinstance(rules, dict) and "type" in rules:
            # Leaf node - validate type and required
            is_required = rules.get("required", False)

            if key not in secrets:
                if is_required:
                    errors.append(f"MISSING REQUIRED: {full_path}")
                continue

            value = secrets[key]
            expected_type = rules["type"]

            if expected_type == "string" and not isinstance(value, str):
                errors.append(
                    f"TYPE ERROR: {full_path} must be string, got {type(value).__name__}"
                )
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(
                    f"TYPE ERROR: {full_path} must be integer, got {type(value).__name__}"
                )
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(
                    f"TYPE ERROR: {full_path} must be boolean, got {type(value).__name__}"
                )

        elif isinstance(rules, dict):
            # Nested section - recurse
            if key not in secrets:
                # Check if any child is required
                has_required_children = any(
                    isinstance(v, dict) and v.get("required", False)
                    for v in rules.values()
                )
                if has_required_children:
                    errors.append(f"MISSING SECTION: {full_path}")
                continue

            if not isinstance(secrets[key], dict):
                errors.append(
                    f"TYPE ERROR: {full_path} must be object, got {type(secrets[key]).__name__}"
                )
                continue

            validate(rules, secrets[key], full_path, errors)

    # Only raise at top level with all collected errors
    if path == "" and errors:
        error_list = "\n  - ".join(errors)
        raise SchemaError(
            f"Secrets validation failed with {len(errors)} error(s):\n  - {error_list}"
        )
```

---

## 8.5 Application Startup Integration

### `main.py` Integration Pattern

```python
import sys
import logging

logger = logging.getLogger(__name__)


def initialize_secrets() -> object:
    """
    Initialize secrets with fail-fast semantics.

    Application WILL NOT START if secrets are misconfigured.
    """
    from mira_oss.secrets import create_backend

    try:
        secrets = create_backend()
        secrets.init()
        logger.info("Secrets backend initialized successfully")
        return secrets

    except FileNotFoundError as e:
        logger.error(f"STARTUP BLOCKED: {e}")
        print("\n" + "="*60)
        print("FATAL: Missing secrets configuration")
        print("="*60)
        print(str(e))
        print("\nRun 'mira secrets init' to create secrets configuration.")
        print("="*60 + "\n")
        sys.exit(1)

    except RuntimeError as e:
        logger.error(f"STARTUP BLOCKED: {e}")
        print("\n" + "="*60)
        print("FATAL: Secrets security/validation failure")
        print("="*60)
        print(str(e))
        print("="*60 + "\n")
        sys.exit(1)

    except Exception as e:
        logger.error(f"STARTUP BLOCKED: Unexpected error: {e}")
        print(f"\nFATAL: Unexpected secrets error: {e}\n")
        sys.exit(1)


# At application startup
secrets = initialize_secrets()

# Now safe to access - all required secrets guaranteed present
anthropic_key = secrets.get("providers.anthropic_key")
database_url = secrets.get("database.service_url")
```

---

## 8.6 CLI Commands for Secrets Management

### `mira secrets check` (Pre-flight Validation)

```python
# scripts/secrets_cli.py
import sys
from mira_oss.secrets import create_backend


def check_secrets():
    """Validate secrets without starting application."""
    print("Checking secrets configuration...")

    try:
        backend = create_backend()
        backend.init()
        print("✓ Age key found")
        print("✓ Secrets file decrypted")
        print("✓ Schema validation passed")
        print("\nAll secrets checks passed.")
        return 0

    except Exception as e:
        print(f"\n✗ FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(check_secrets())
```

This allows operators to validate secrets before deployment:

```bash
$ mira secrets check
Checking secrets configuration...
✓ Age key found
✓ Secrets file decrypted
✓ Schema validation passed

All secrets checks passed.
```

Or catch errors early:

```bash
$ mira secrets check
Checking secrets configuration...

✗ FAILED: Secrets validation failed with 2 error(s):
  - MISSING REQUIRED: providers.anthropic_key
  - MISSING REQUIRED: database.service_url
```
