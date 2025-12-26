"""
Schema validation engine for SOPS secrets.

Validates decrypted secrets against a YAML schema with fail-fast semantics.
Collects ALL errors before raising, so operators can fix everything at once.
"""

from typing import Any, Dict, List, Optional


class SchemaError(Exception):
    """Raised when secrets fail schema validation."""
    pass


def validate(
    schema: Dict[str, Any],
    secrets: Dict[str, Any],
    path: str = "",
    errors: Optional[List[str]] = None
) -> None:
    """
    Validate secrets against schema with comprehensive error collection.

    Collects ALL errors before raising, so operator can fix everything at once.

    Args:
        schema: The schema definition (from schema.yaml)
        secrets: The decrypted secrets to validate
        path: Current path in the schema (for nested validation)
        errors: Accumulated errors (internal use)

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
                has_required_children = _has_required_children(rules)
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


def _has_required_children(rules: Dict[str, Any]) -> bool:
    """Check if a schema section has any required children (recursively)."""
    for value in rules.values():
        if isinstance(value, dict):
            if value.get("required", False):
                return True
            if _has_required_children(value):
                return True
    return False


def get_required_fields(schema: Dict[str, Any], path: str = "") -> List[str]:
    """
    Extract list of all required field paths from schema.

    Useful for generating template secrets files or documentation.

    Args:
        schema: The schema definition
        path: Current path prefix

    Returns:
        List of dot-notation paths to required fields
    """
    required: List[str] = []

    for key, rules in schema.items():
        full_path = f"{path}.{key}" if path else key

        if isinstance(rules, dict) and "type" in rules:
            if rules.get("required", False):
                required.append(full_path)
        elif isinstance(rules, dict):
            required.extend(get_required_fields(rules, full_path))

    return required

