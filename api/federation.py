"""
Federation webhook endpoint for receiving messages from Lattice.

This endpoint receives validated, de-duplicated messages from the Lattice
discovery daemon and delivers them to local users via the pager tool.
"""

import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class FederationDeliveryPayload(BaseModel):
    """Payload from Lattice for federated message delivery."""
    from_address: str = Field(..., description="Sender's federated address (user@domain)")
    to_user_id: str = Field(..., description="Resolved recipient user_id (UUID)")
    content: str = Field(..., description="Message content")
    priority: int = Field(default=0, description="Priority: 0=normal, 1=high, 2=urgent")
    message_id: str = Field(..., description="Unique message ID for idempotency")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata")
    sender_verified: bool = Field(default=False, description="Whether sender signature was verified")
    sender_server_id: str = Field(..., description="Sending server's domain")


class FederationDeliveryResponse(BaseModel):
    """Response to Lattice after delivery attempt."""
    status: str = Field(..., description="delivered or failed")
    message_id: str = Field(..., description="Echo back message_id")
    error: Optional[str] = Field(default=None, description="Error message if failed")


@router.post("/federation/deliver", response_model=FederationDeliveryResponse)
def receive_federation_delivery(payload: FederationDeliveryPayload) -> FederationDeliveryResponse:
    """
    Receive a federated message from Lattice and deliver to local user.

    Lattice has already:
    - Verified the sender's signature
    - Checked rate limits
    - De-duplicated the message
    - Resolved the username to user_id

    This endpoint just needs to write to the user's pager.

    Returns:
        200 + status=delivered: Success
        4xx: Permanent failure (Lattice won't retry)
        5xx: Temporary failure (Lattice will retry)
    """
    try:
        logger.info(
            f"Receiving federated message {payload.message_id} "
            f"from {payload.from_address} to user {payload.to_user_id}"
        )

        # Import here to avoid circular imports
        from tools.implementations.pager_tool import PagerTool

        # Create pager tool instance for the target user
        pager = PagerTool(user_id=payload.to_user_id)

        # Deliver the message
        result = pager.deliver_federated_message(
            from_address=payload.from_address,
            content=payload.content,
            priority=payload.priority,
            metadata=payload.metadata
        )

        if result.get("success"):
            logger.info(
                f"Delivered federated message {payload.message_id} "
                f"to user {payload.to_user_id} pager {result.get('delivered_to')}"
            )
            return FederationDeliveryResponse(
                status="delivered",
                message_id=payload.message_id
            )
        else:
            error_msg = result.get("error", "Unknown delivery error")
            logger.warning(f"Failed to deliver {payload.message_id}: {error_msg}")

            # Return 400 for user-related issues (permanent)
            raise HTTPException(status_code=400, detail=error_msg)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error delivering federated message {payload.message_id}: {e}", exc_info=True)
        # Return 500 for server errors (Lattice will retry)
        raise HTTPException(status_code=500, detail="Internal delivery error")
