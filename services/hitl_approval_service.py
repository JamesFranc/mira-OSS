"""
Human-in-the-Loop (HITL) Approval Service.

Manages approval queue for sensitive system gateway operations via Valkey.
Provides queue, poll, approve, and reject functionality with TTL-based expiration.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from clients.valkey_client import get_valkey_client
from utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    """Represents a pending approval request."""
    id: str
    user_id: str
    operation: str
    details: Dict[str, Any]
    sensitivity: str
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "operation": self.operation,
            "details": self.details,
            "sensitivity": self.sensitivity,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalRequest":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            operation=data["operation"],
            details=data["details"],
            sensitivity=data["sensitivity"],
            status=ApprovalStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )


class HITLApprovalService:
    """
    Service for managing HITL approval requests.
    
    Uses Valkey for storage with automatic TTL-based expiration.
    """
    
    # Key prefixes for Valkey storage
    KEY_PREFIX = "hitl:approval:"
    USER_INDEX_PREFIX = "hitl:user:"
    
    def __init__(self, default_ttl_seconds: int = 120):
        """
        Initialize the approval service.
        
        Args:
            default_ttl_seconds: Default TTL for approval requests (2 minutes)
        """
        self.default_ttl = default_ttl_seconds
        self._valkey = get_valkey_client()
    
    async def queue_approval(
        self,
        user_id: str,
        operation: str,
        details: Dict[str, Any],
        sensitivity: str,
        ttl_seconds: Optional[int] = None
    ) -> ApprovalRequest:
        """
        Queue a new approval request.
        
        Args:
            user_id: User who initiated the operation
            operation: Description of the operation (e.g., "execute: rm -rf temp/")
            details: Additional details about the operation
            sensitivity: Sensitivity level (PROMPT, HIGH)
            ttl_seconds: Custom TTL, defaults to service default
            
        Returns:
            The created ApprovalRequest
        """
        ttl = ttl_seconds or self.default_ttl
        now = utc_now()
        
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            user_id=user_id,
            operation=operation,
            details=details,
            sensitivity=sensitivity,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=now.replace(microsecond=0) + __import__("datetime").timedelta(seconds=ttl),
        )
        
        # Store in Valkey with TTL
        key = f"{self.KEY_PREFIX}{request.id}"
        self._valkey.setex(key, ttl, json.dumps(request.to_dict()))

        # Add to user's pending list
        user_key = f"{self.USER_INDEX_PREFIX}{user_id}"
        self._valkey.sadd(user_key, request.id)
        self._valkey.expire(user_key, ttl + 60)  # Slightly longer TTL for index

        logger.info(f"Queued approval request {request.id} for user {user_id}: {operation}")
        return request

    async def get_status(self, approval_id: str) -> Optional[ApprovalRequest]:
        """
        Get the current status of an approval request.

        Args:
            approval_id: The approval request ID

        Returns:
            ApprovalRequest if found, None if expired or not found
        """
        key = f"{self.KEY_PREFIX}{approval_id}"
        data = self._valkey.get(key)

        if not data:
            return None

        return ApprovalRequest.from_dict(json.loads(data))

    async def get_pending_for_user(self, user_id: str) -> List[ApprovalRequest]:
        """
        Get all pending approval requests for a user.

        Args:
            user_id: The user ID

        Returns:
            List of pending ApprovalRequests
        """
        user_key = f"{self.USER_INDEX_PREFIX}{user_id}"
        approval_ids = self._valkey.smembers(user_key)

        requests = []
        expired_ids = []

        for approval_id in approval_ids:
            request = await self.get_status(approval_id)
            if request and request.status == ApprovalStatus.PENDING:
                requests.append(request)
            elif not request:
                expired_ids.append(approval_id)

        # Clean up expired IDs from user index
        if expired_ids:
            self._valkey.srem(user_key, *expired_ids)

        return requests

    async def approve(self, approval_id: str, approved_by: Optional[str] = None) -> bool:
        """
        Approve a pending request.

        Args:
            approval_id: The approval request ID
            approved_by: Optional identifier of who approved

        Returns:
            True if approved, False if not found or already processed
        """
        request = await self.get_status(approval_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return False

        request.status = ApprovalStatus.APPROVED
        request.details["approved_by"] = approved_by
        request.details["approved_at"] = utc_now().isoformat()

        # Update in Valkey (keep short TTL for result retrieval)
        key = f"{self.KEY_PREFIX}{approval_id}"
        self._valkey.setex(key, 60, json.dumps(request.to_dict()))

        logger.info(f"Approved request {approval_id}")
        return True

    async def reject(
        self,
        approval_id: str,
        rejected_by: Optional[str] = None,
        reason: Optional[str] = None
    ) -> bool:
        """
        Reject a pending request.

        Args:
            approval_id: The approval request ID
            rejected_by: Optional identifier of who rejected
            reason: Optional rejection reason

        Returns:
            True if rejected, False if not found or already processed
        """
        request = await self.get_status(approval_id)
        if not request or request.status != ApprovalStatus.PENDING:
            return False

        request.status = ApprovalStatus.REJECTED
        request.details["rejected_by"] = rejected_by
        request.details["rejected_at"] = utc_now().isoformat()
        if reason:
            request.details["rejection_reason"] = reason

        # Update in Valkey (keep short TTL for result retrieval)
        key = f"{self.KEY_PREFIX}{approval_id}"
        self._valkey.setex(key, 60, json.dumps(request.to_dict()))

        logger.info(f"Rejected request {approval_id}: {reason}")
        return True

    async def wait_for_decision(
        self,
        approval_id: str,
        poll_interval: float = 0.5,
        max_wait: Optional[float] = None
    ) -> Optional[ApprovalRequest]:
        """
        Wait for an approval decision with polling.

        Args:
            approval_id: The approval request ID
            poll_interval: Seconds between polls
            max_wait: Maximum seconds to wait (None = wait until expiry)

        Returns:
            Final ApprovalRequest state, or None if expired
        """
        import asyncio

        start = utc_now()

        while True:
            request = await self.get_status(approval_id)

            if not request:
                return None  # Expired

            if request.status != ApprovalStatus.PENDING:
                return request  # Decision made

            # Check timeout
            if max_wait:
                elapsed = (utc_now() - start).total_seconds()
                if elapsed >= max_wait:
                    return request  # Still pending, but we timed out

            await asyncio.sleep(poll_interval)


# Singleton instance
_service: Optional[HITLApprovalService] = None


def get_hitl_service() -> HITLApprovalService:
    """Get the singleton HITL approval service instance."""
    global _service
    if _service is None:
        from config.config_manager import config
        ttl = config.system_gateway.hitl_timeout
        _service = HITLApprovalService(default_ttl_seconds=ttl)
    return _service
