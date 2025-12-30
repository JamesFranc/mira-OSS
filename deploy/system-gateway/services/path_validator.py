"""
Path validation and security checks for System Gateway.

Prevents path traversal attacks, symlink escapes, and access to blocked patterns.
"""

import fnmatch
import os
from pathlib import Path
from typing import List

from config import settings


class PathValidationError(Exception):
    """Raised when path validation fails."""
    pass


class PathValidator:
    """Validates and secures filesystem paths within workspace."""
    
    def __init__(self, workspace_root: str | None = None, blocked_patterns: List[str] | None = None):
        self.workspace_root = Path(workspace_root or settings.workspace_root).resolve()
        self.blocked_patterns = blocked_patterns or settings.blocked_patterns
    
    def validate(self, path: str) -> Path:
        """
        Validate a path is safe and within workspace.
        
        Args:
            path: Relative or absolute path to validate
            
        Returns:
            Resolved absolute Path within workspace
            
        Raises:
            PathValidationError: If path is invalid or blocked
        """
        # Handle empty path as workspace root
        if not path or path in (".", "./", "/"):
            return self.workspace_root
        
        # Remove leading slash for relative path handling
        clean_path = path.lstrip("/")
        
        # Construct absolute path
        if Path(path).is_absolute():
            # Absolute paths must be within workspace
            target = Path(path)
        else:
            target = self.workspace_root / clean_path
        
        # Resolve to catch .. traversal
        try:
            resolved = target.resolve()
        except (OSError, RuntimeError) as e:
            raise PathValidationError(f"Cannot resolve path: {e}")
        
        # Check if within workspace (after resolving symlinks)
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise PathValidationError(
                f"Path escapes workspace: {path} resolves to {resolved}"
            )
        
        # Check blocked patterns
        relative_path = str(resolved.relative_to(self.workspace_root))
        for pattern in self.blocked_patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                raise PathValidationError(f"Access blocked by pattern: {pattern}")
            if fnmatch.fnmatch(resolved.name, pattern):
                raise PathValidationError(f"Access blocked by pattern: {pattern}")
        
        return resolved
    
    def validate_for_write(self, path: str) -> Path:
        """
        Validate path for write operations.
        
        Additional checks for write safety.
        """
        resolved = self.validate(path)
        
        # Check if parent directory exists
        parent = resolved.parent
        if not parent.exists():
            raise PathValidationError(f"Parent directory does not exist: {parent}")
        
        # Check if parent is writable
        if not os.access(parent, os.W_OK):
            raise PathValidationError(f"Parent directory not writable: {parent}")
        
        return resolved
    
    def is_binary(self, path: Path, sample_size: int = 8192) -> bool:
        """Check if a file appears to be binary."""
        try:
            with open(path, "rb") as f:
                chunk = f.read(sample_size)
                # Look for null bytes (common in binary files)
                if b"\x00" in chunk:
                    return True
                # Check for high ratio of non-text bytes
                text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
                non_text = sum(1 for b in chunk if b not in text_chars)
                return non_text / len(chunk) > 0.3 if chunk else False
        except (OSError, IOError):
            return False

