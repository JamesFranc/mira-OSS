"""
Per-user System Gateway settings.

Stores user-specific gateway configuration like workspace paths,
auto-approve patterns, and blocked paths in encrypted user storage.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from utils.user_credentials import UserCredentialService
from config.config_manager import config

logger = logging.getLogger(__name__)


class GatewayUserSettings(BaseModel):
    """Per-user gateway settings stored in encrypted user database."""
    
    # Workspace configuration
    workspace_paths: List[str] = Field(
        default_factory=list,
        description="Additional workspace paths the user can access"
    )
    default_workspace: Optional[str] = Field(
        default=None,
        description="User's default workspace path (overrides global config)"
    )
    
    # Auto-approve configuration
    auto_approve_commands: List[str] = Field(
        default_factory=list,
        description="Additional commands to auto-approve for this user"
    )
    auto_approve_dirs: List[str] = Field(
        default_factory=list,
        description="Directories where all operations auto-approve"
    )
    
    # Blocked patterns
    blocked_paths: List[str] = Field(
        default_factory=list,
        description="Additional paths blocked for this user"
    )
    
    # Feature flags
    network_enabled: bool = Field(
        default=False,
        description="Whether network access is enabled in gateway (requires container restart)"
    )
    max_timeout: int = Field(
        default=300,
        description="Maximum command timeout for this user"
    )


CREDENTIAL_TYPE = "gateway_settings"
SERVICE_NAME = "system_gateway"


def get_user_gateway_settings(user_id: str) -> GatewayUserSettings:
    """
    Get gateway settings for a user.
    
    Returns user-specific settings merged with global defaults.
    """
    cred_service = UserCredentialService(user_id)
    
    try:
        settings_json = cred_service.get_credential(CREDENTIAL_TYPE, SERVICE_NAME)
        if settings_json:
            return GatewayUserSettings(**json.loads(settings_json))
    except Exception as e:
        logger.warning(f"Failed to load gateway settings for user {user_id}: {e}")
    
    return GatewayUserSettings()


def save_user_gateway_settings(user_id: str, settings: GatewayUserSettings) -> None:
    """Save gateway settings for a user."""
    cred_service = UserCredentialService(user_id)
    cred_service.store_credential(
        CREDENTIAL_TYPE,
        SERVICE_NAME,
        settings.model_dump_json()
    )
    logger.info(f"Saved gateway settings for user {user_id}")


def get_effective_auto_approve_commands(user_id: str) -> List[str]:
    """
    Get combined auto-approve commands from global config and user settings.
    """
    global_patterns = config.system_gateway.auto_approve_patterns
    user_settings = get_user_gateway_settings(user_id)
    
    # Combine and deduplicate
    return list(set(global_patterns + user_settings.auto_approve_commands))


def get_effective_blocked_patterns(user_id: str) -> List[str]:
    """
    Get combined blocked patterns from global config and user settings.
    """
    global_patterns = config.system_gateway.blocked_patterns
    user_settings = get_user_gateway_settings(user_id)
    
    # Combine and deduplicate
    return list(set(global_patterns + user_settings.blocked_paths))


def is_path_in_auto_approve_dir(user_id: str, path: str) -> bool:
    """
    Check if a path is within a user's auto-approve directories.
    """
    user_settings = get_user_gateway_settings(user_id)
    
    for auto_dir in user_settings.auto_approve_dirs:
        if path.startswith(auto_dir) or path == auto_dir:
            return True
    
    return False

