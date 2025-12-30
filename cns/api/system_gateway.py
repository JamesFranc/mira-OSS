"""
System Gateway API endpoints.

Provides endpoints for managing HITL approval requests for system gateway operations.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from auth.dependencies import get_current_user_id
from services.hitl_approval_service import (
    get_hitl_service,
    ApprovalStatus,
    ApprovalRequest,
)
from cns.api.base import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system-gateway", tags=["system-gateway"])


# --- Request/Response Models ---

class ApprovalListResponse(BaseModel):
    """Response model for listing approvals."""
    success: bool
    approvals: List[Dict[str, Any]]
    count: int


class ApprovalActionRequest(BaseModel):
    """Request model for approve/reject actions."""
    action: str = Field(..., pattern="^(approve|reject)$", description="Action to take")
    reason: Optional[str] = Field(default=None, description="Reason for rejection")


class ApprovalActionResponse(BaseModel):
    """Response model for approval actions."""
    success: bool
    approval_id: str
    status: str
    message: str


# --- Endpoints ---

@router.get("/approvals", response_model=ApprovalListResponse)
async def list_pending_approvals(
    user_id: str = Depends(get_current_user_id)
) -> ApprovalListResponse:
    """
    List all pending approval requests for the current user.
    
    Returns pending system gateway operations awaiting user approval.
    """
    service = get_hitl_service()
    pending = await service.get_pending_for_user(user_id)
    
    return ApprovalListResponse(
        success=True,
        approvals=[req.to_dict() for req in pending],
        count=len(pending)
    )


@router.get("/approvals/{approval_id}")
async def get_approval_status(
    approval_id: str,
    user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    """
    Get the status of a specific approval request.
    
    Returns the current state of the approval (pending, approved, rejected, expired).
    """
    service = get_hitl_service()
    request = await service.get_status(approval_id)
    
    if not request:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    
    # Verify user owns this request
    if request.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this approval")
    
    return APIResponse(
        success=True,
        data=request.to_dict()
    ).to_dict()


@router.patch("/approvals/{approval_id}", response_model=ApprovalActionResponse)
async def update_approval(
    approval_id: str,
    body: ApprovalActionRequest,
    user_id: str = Depends(get_current_user_id)
) -> ApprovalActionResponse:
    """
    Approve or reject a pending approval request.
    
    Only the user who initiated the request can approve/reject it.
    """
    service = get_hitl_service()
    request = await service.get_status(approval_id)
    
    if not request:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    
    # Verify user owns this request
    if request.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this approval")
    
    if request.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Approval already processed: {request.status.value}"
        )
    
    if body.action == "approve":
        success = await service.approve(approval_id, approved_by=user_id)
        message = "Operation approved"
        status = ApprovalStatus.APPROVED.value
    else:
        success = await service.reject(approval_id, rejected_by=user_id, reason=body.reason)
        message = f"Operation rejected: {body.reason}" if body.reason else "Operation rejected"
        status = ApprovalStatus.REJECTED.value
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update approval status")
    
    logger.info(f"User {user_id} {body.action}d approval {approval_id}")
    
    return ApprovalActionResponse(
        success=True,
        approval_id=approval_id,
        status=status,
        message=message
    )

