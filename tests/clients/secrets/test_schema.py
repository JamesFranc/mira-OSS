"""
Tests for clients/secrets/schema.py - Schema validation.

Tests the schema validation logic independently of SOPS decryption.
"""
import pytest
from clients.secrets.schema import validate, get_required_fields, SchemaError


class TestSchemaValidation:
    """Test schema validation logic."""

    def test_validate_passes_with_all_required_fields(self):
        """Validation passes when all required fields are present."""
        schema = {
            "providers": {
                "anthropic_key": {"type": "string", "required": True},
                "optional_key": {"type": "string", "required": False},
            }
        }
        secrets = {
            "providers": {
                "anthropic_key": "sk-ant-123456",
            }
        }

        # Should not raise
        validate(schema, secrets)

    def test_validate_fails_on_missing_required_field(self):
        """Validation fails when required field is missing."""
        schema = {
            "providers": {
                "anthropic_key": {"type": "string", "required": True},
            }
        }
        secrets = {
            "providers": {}
        }

        with pytest.raises(SchemaError, match="MISSING REQUIRED"):
            validate(schema, secrets)

    def test_validate_fails_on_missing_required_section(self):
        """Validation fails when entire required section is missing."""
        schema = {
            "database": {
                "service_url": {"type": "string", "required": True},
            }
        }
        secrets = {}

        with pytest.raises(SchemaError, match="MISSING SECTION"):
            validate(schema, secrets)

    def test_validate_fails_on_type_mismatch(self):
        """Validation fails when field type doesn't match schema."""
        schema = {
            "providers": {
                "anthropic_key": {"type": "string", "required": True},
            }
        }
        secrets = {
            "providers": {
                "anthropic_key": 12345,  # Should be string, not int
            }
        }

        with pytest.raises(SchemaError, match="TYPE ERROR"):
            validate(schema, secrets)

    def test_validate_collects_all_errors(self):
        """Validation collects all errors before raising."""
        schema = {
            "providers": {
                "key1": {"type": "string", "required": True},
                "key2": {"type": "string", "required": True},
            },
            "database": {
                "url": {"type": "string", "required": True},
            }
        }
        secrets = {
            "providers": {}
            # database section entirely missing
        }

        with pytest.raises(SchemaError) as exc_info:
            validate(schema, secrets)

        error_msg = str(exc_info.value)
        # Should mention multiple errors
        assert "2 error" in error_msg or "3 error" in error_msg

    def test_validate_accepts_optional_missing_fields(self):
        """Validation passes when optional fields are missing."""
        schema = {
            "providers": {
                "required_key": {"type": "string", "required": True},
                "optional_key": {"type": "string", "required": False},
            }
        }
        secrets = {
            "providers": {
                "required_key": "value",
                # optional_key missing - should be fine
            }
        }

        # Should not raise
        validate(schema, secrets)


class TestGetRequiredFields:
    """Test required field extraction."""

    def test_extracts_required_fields_from_flat_schema(self):
        """Extracts required fields from simple schema."""
        schema = {
            "providers": {
                "anthropic_key": {"type": "string", "required": True},
                "optional_key": {"type": "string", "required": False},
            }
        }

        required = get_required_fields(schema)

        assert "providers.anthropic_key" in required
        assert "providers.optional_key" not in required

    def test_extracts_required_fields_from_nested_schema(self):
        """Extracts required fields from nested schema."""
        schema = {
            "database": {
                "service_url": {"type": "string", "required": True},
                "admin_url": {"type": "string", "required": True},
            },
            "auth": {
                "jwt_secret": {"type": "string", "required": True},
            }
        }

        required = get_required_fields(schema)

        assert "database.service_url" in required
        assert "database.admin_url" in required
        assert "auth.jwt_secret" in required
        assert len(required) == 3

