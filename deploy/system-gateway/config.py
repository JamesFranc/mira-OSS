"""
Configuration for System Gateway service.

Reads from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Gateway configuration from environment variables."""
    
    # Server settings
    gateway_port: int = Field(default=9500, alias="GATEWAY_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    
    # Workspace settings
    workspace_root: str = Field(default="/workspace", alias="WORKSPACE_ROOT")
    
    # Security settings - patterns to block (comma-separated in env)
    blocked_patterns_str: str = Field(
        default="*.env,*.key,*.pem,.git/config,**/secrets/**",
        alias="BLOCKED_PATTERNS"
    )
    
    # Command execution settings
    default_timeout: int = Field(default=30, alias="DEFAULT_TIMEOUT")
    max_timeout: int = Field(default=300, alias="MAX_TIMEOUT")
    
    # File operation limits
    max_file_size_bytes: int = Field(default=10_485_760, alias="MAX_FILE_SIZE")  # 10MB
    max_output_lines: int = Field(default=10000, alias="MAX_OUTPUT_LINES")
    
    # Tree indexer settings
    index_db_path: str = Field(default="/tmp/gateway/tree_index.db", alias="INDEX_DB_PATH")
    index_update_debounce_ms: int = Field(default=500, alias="INDEX_DEBOUNCE_MS")
    
    @property
    def blocked_patterns(self) -> List[str]:
        """Parse blocked patterns from comma-separated string."""
        return [p.strip() for p in self.blocked_patterns_str.split(",") if p.strip()]
    
    @property
    def workspace_path(self) -> Path:
        """Get workspace as Path object."""
        return Path(self.workspace_root)
    
    class Config:
        env_file = ".env"
        extra = "ignore"


# Global settings instance
settings = Settings()

