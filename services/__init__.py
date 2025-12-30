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
    ApprovalDecision,
    get_hitl_approval_service,
)
from .gateway_audit_log import (
    GatewayAuditLogger,
    OperationType,
    AuditEntry,
    get_audit_logger,
)
from .gateway_user_settings import (
    GatewayUserSettingsService,
    GatewayUserSettings,
)
from .approval_interceptor import (
    ApprovalInterceptor,
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
    "ApprovalDecision",
    "get_hitl_approval_service",
    # Audit logging
    "GatewayAuditLogger",
    "OperationType",
    "AuditEntry",
    "get_audit_logger",
    # User settings
    "GatewayUserSettingsService",
    "GatewayUserSettings",
    # Interceptor
    "ApprovalInterceptor",
]

