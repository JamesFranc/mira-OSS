"""
Single-user authentication.

Provides get_current_user dependency that validates Bearer token against
the API key stored in Vault.
"""

from typing import Optional, Dict, Any

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from utils.user_context import set_current_user_id, set_current_user_data


# Security scheme for bearer tokens
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Dict[str, Any]:
    """Verify Bearer token and inject single user context."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    if credentials.credentials != request.app.state.api_key:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    user_id = request.app.state.single_user_id
    user_email = request.app.state.user_email

    set_current_user_id(user_id)
    set_current_user_data({
        "user_id": user_id,
        "email": user_email
    })

    return {
        "user_id": user_id,
        "email": user_email
    }
