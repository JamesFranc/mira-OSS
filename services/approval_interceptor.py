"""
Approval Interceptor for conversational HITL approval.

Intercepts user messages to detect approval/rejection responses for pending
system gateway operations. Allows users to approve operations via natural
language in the chat flow.
"""

import logging
import re
from typing import Optional, Tuple

from services.hitl_approval_service import get_hitl_service, ApprovalStatus

logger = logging.getLogger(__name__)

# Patterns for approval responses
APPROVE_PATTERNS = [
    r"^yes$", r"^y$", r"^approve$", r"^approved$", r"^ok$", r"^okay$",
    r"^go ahead$", r"^do it$", r"^proceed$", r"^confirm$", r"^confirmed$",
    r"^allow$", r"^allowed$", r"^accept$", r"^accepted$",
    r"^yes,?\s*please$", r"^yes,?\s*go ahead$",
]

REJECT_PATTERNS = [
    r"^no$", r"^n$", r"^reject$", r"^rejected$", r"^deny$", r"^denied$",
    r"^cancel$", r"^cancelled$", r"^stop$", r"^abort$", r"^don'?t$",
    r"^no,?\s*thanks$", r"^no,?\s*don'?t$", r"^nevermind$", r"^never\s*mind$",
]


def _matches_patterns(text: str, patterns: list[str]) -> bool:
    """Check if text matches any of the given patterns."""
    text_lower = text.lower().strip()
    for pattern in patterns:
        if re.match(pattern, text_lower, re.IGNORECASE):
            return True
    return False


async def check_for_approval_response(
    user_id: str,
    message: str
) -> Optional[Tuple[bool, str]]:
    """
    Check if a user message is an approval/rejection response.
    
    Args:
        user_id: The user ID
        message: The user's message text
        
    Returns:
        None if not an approval response, or tuple of (approved: bool, response_text: str)
    """
    # Get pending approvals for user
    service = get_hitl_service()
    pending = await service.get_pending_for_user(user_id)
    
    if not pending:
        return None
    
    # Check if message matches approval/rejection patterns
    is_approve = _matches_patterns(message, APPROVE_PATTERNS)
    is_reject = _matches_patterns(message, REJECT_PATTERNS)
    
    if not is_approve and not is_reject:
        return None
    
    # Process the most recent pending approval
    # (In practice, there should usually only be one pending at a time)
    latest = pending[0]
    
    if is_approve:
        success = await service.approve(latest.id, approved_by=user_id)
        if success:
            logger.info(f"User {user_id} approved operation {latest.id} via chat")
            return (True, f"✓ Approved: {latest.operation}\n\nExecuting operation...")
        else:
            return (False, "Failed to process approval. The request may have expired.")
    
    elif is_reject:
        success = await service.reject(latest.id, rejected_by=user_id, reason="User rejected via chat")
        if success:
            logger.info(f"User {user_id} rejected operation {latest.id} via chat")
            return (False, f"✗ Rejected: {latest.operation}\n\nOperation cancelled.")
        else:
            return (False, "Failed to process rejection. The request may have expired.")
    
    return None


async def format_pending_approval_prompt(user_id: str) -> Optional[str]:
    """
    Format a prompt for pending approvals to include in system context.
    
    Args:
        user_id: The user ID
        
    Returns:
        Formatted prompt string if there are pending approvals, None otherwise
    """
    service = get_hitl_service()
    pending = await service.get_pending_for_user(user_id)
    
    if not pending:
        return None
    
    lines = ["⚠️ PENDING APPROVAL REQUIRED:"]
    for req in pending:
        lines.append(f"\n• {req.operation}")
        if req.details.get("description"):
            lines.append(f"  Details: {req.details['description']}")
        lines.append(f"  Sensitivity: {req.sensitivity}")
        lines.append(f"  Expires: {req.expires_at.strftime('%H:%M:%S')}")
    
    lines.append("\nRespond with 'yes' to approve or 'no' to reject.")
    
    return "\n".join(lines)

