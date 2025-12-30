"""
Root-level services for MIRA.

Contains services that operate at the application level rather than
within specific domains like CNS.
"""

from .sensitivity_classifier import (
    SensitivityLevel,
    classify_command,
    classify_file_operation,
)
from .hitl_approval_service import (
    HITLApprovalService,
    ApprovalRequest,
    ApprovalStatus,
    get_hitl_service,
)
from .gateway_audit_log import (
    GatewayAuditLogger,
    OperationType,
    AuditEntry,
    get_audit_logger,
)
from .gateway_user_settings import (
    GatewayUserSettings,
    get_user_gateway_settings,
    save_user_gateway_settings,
)
from .approval_interceptor import (
    check_for_approval_response,
    format_pending_approval_prompt,
)

__all__ = [
    # Sensitivity classification
    "SensitivityLevel",
    "classify_command",
    "classify_file_operation",
    # HITL Approval
    "HITLApprovalService",
    "ApprovalRequest",
    "ApprovalStatus",
    "get_hitl_service",
    # Audit logging
    "GatewayAuditLogger",
    "OperationType",
    "AuditEntry",
    "get_audit_logger",
    # User settings
    "GatewayUserSettings",
    "get_user_gateway_settings",
    "save_user_gateway_settings",
    # Interceptor
    "check_for_approval_response",
    "format_pending_approval_prompt",
]

