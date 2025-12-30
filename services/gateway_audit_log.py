"""
Audit logging for System Gateway operations.

Logs all gateway operations with user_id, operation type, path/command,
result, and timestamp. Uses append-only logging for security.
"""

import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from config.config_manager import config
from utils.timezone_utils import utc_now, format_utc_iso

logger = logging.getLogger(__name__)


class OperationType(str, Enum):
    """Types of gateway operations."""
    READ_STRUCTURE = "read_structure"
    READ_FILE = "read_file"
    EDIT_FILE = "edit_file"
    EXECUTE = "execute"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_EXPIRED = "approval_expired"


class AuditEntry(BaseModel):
    """Single audit log entry."""
    timestamp: str = Field(..., description="ISO timestamp of operation")
    user_id: str = Field(..., description="User who performed operation")
    operation: OperationType = Field(..., description="Type of operation")
    target: str = Field(..., description="Path or command that was targeted")
    result: str = Field(..., description="Result: success, failure, blocked, pending")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional details")
    sensitivity: Optional[str] = Field(default=None, description="Sensitivity level if applicable")
    approval_id: Optional[str] = Field(default=None, description="Approval ID if HITL was involved")


class GatewayAuditLogger:
    """
    Append-only audit logger for gateway operations.
    
    Writes to a dedicated log file in JSON Lines format for easy parsing.
    """
    
    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize audit logger.
        
        Args:
            log_dir: Directory for audit logs. Defaults to data/audit/
        """
        if log_dir is None:
            log_dir = Path(config.paths.data_dir) / "audit"
        
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "gateway_audit.jsonl"
    
    def log(
        self,
        user_id: str,
        operation: OperationType,
        target: str,
        result: str,
        details: Optional[Dict[str, Any]] = None,
        sensitivity: Optional[str] = None,
        approval_id: Optional[str] = None
    ) -> None:
        """
        Log an audit entry.
        
        Args:
            user_id: User who performed the operation
            operation: Type of operation
            target: Path or command targeted
            result: Result of operation (success, failure, blocked, pending)
            details: Additional details about the operation
            sensitivity: Sensitivity level if applicable
            approval_id: Approval ID if HITL was involved
        """
        entry = AuditEntry(
            timestamp=format_utc_iso(utc_now()),
            user_id=user_id,
            operation=operation,
            target=target,
            result=result,
            details=details or {},
            sensitivity=sensitivity,
            approval_id=approval_id
        )
        
        # Append to log file (atomic write with newline)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")
        except Exception as e:
            # Log to standard logger if file write fails
            logger.error(f"Failed to write audit log: {e}")
            logger.info(f"AUDIT: {entry.model_dump_json()}")
    
    def log_read_structure(self, user_id: str, path: str, success: bool) -> None:
        """Log a read_structure operation."""
        self.log(
            user_id=user_id,
            operation=OperationType.READ_STRUCTURE,
            target=path or "/",
            result="success" if success else "failure"
        )
    
    def log_read_file(self, user_id: str, path: str, success: bool, lines: int = 0) -> None:
        """Log a read_file operation."""
        self.log(
            user_id=user_id,
            operation=OperationType.READ_FILE,
            target=path,
            result="success" if success else "failure",
            details={"lines_read": lines}
        )
    
    def log_edit_file(self, user_id: str, path: str, success: bool, edits: int = 0) -> None:
        """Log an edit_file operation."""
        self.log(
            user_id=user_id,
            operation=OperationType.EDIT_FILE,
            target=path,
            result="success" if success else "failure",
            details={"edits_applied": edits}
        )
    
    def log_execute(
        self,
        user_id: str,
        command: str,
        success: bool,
        exit_code: int = 0,
        duration_ms: int = 0
    ) -> None:
        """Log an execute operation."""
        self.log(
            user_id=user_id,
            operation=OperationType.EXECUTE,
            target=command,
            result="success" if success else "failure",
            details={"exit_code": exit_code, "duration_ms": duration_ms}
        )
    
    def log_blocked(self, user_id: str, operation: str, target: str, reason: str) -> None:
        """Log a blocked operation."""
        self.log(
            user_id=user_id,
            operation=OperationType(operation) if operation in OperationType.__members__.values() else OperationType.EXECUTE,
            target=target,
            result="blocked",
            details={"reason": reason}
        )


# Singleton instance
_audit_logger: Optional[GatewayAuditLogger] = None


def get_audit_logger() -> GatewayAuditLogger:
    """Get the singleton audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = GatewayAuditLogger()
    return _audit_logger

