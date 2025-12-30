"""
Sensitivity classification for System Gateway operations.

Classifies commands and file operations by sensitivity level:
- AUTO: Safe operations that can proceed without approval
- PROMPT: Operations requiring user confirmation
- HIGH: High-risk operations requiring explicit approval
- BLOCKED: Operations that are never allowed
"""

import re
from enum import Enum
from typing import List, Optional

from config.config_manager import config


class SensitivityLevel(str, Enum):
    """Sensitivity levels for gateway operations."""
    AUTO = "auto"
    PROMPT = "prompt"
    HIGH = "high"
    BLOCKED = "blocked"


# Command patterns for classification
BLOCKED_COMMAND_PATTERNS = [
    r"^\s*sudo\b",
    r"\bsudo\s+",
    r"\brm\s+-rf\s+/\s*$",
    r"\brm\s+-rf\s+/[^/]",  # rm -rf /something at root
    r"\bcurl\s+.*\|\s*(ba)?sh",
    r"\bwget\s+.*\|\s*(ba)?sh",
    r"\bnc\s+-[el]",  # netcat listen/execute
    r"\bncat\s+-[el]",
    r"\bnetcat\s+-[el]",
    r"\bchmod\s+777\s+/",
    r"\bchown\s+.*\s+/",
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/",
    r"\b:(){ :|:& };:",  # Fork bomb
]

HIGH_SENSITIVITY_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\brm\s+-r\b",
    r"\bgit\s+push\b",
    r"\bgit\s+push\s+--force",
    r"\bgit\s+reset\s+--hard",
    r"\bdocker\s+rm\b",
    r"\bdocker\s+rmi\b",
    r"\bkill\s+-9\b",
    r"\bpkill\b",
    r"\bkillall\b",
    r"\btruncate\b",
    r"\bshred\b",
]

PROMPT_PATTERNS = [
    r"\bmv\s+",
    r"\bcp\s+-r",
    r"\bnpm\s+install\b",
    r"\bnpm\s+i\b",
    r"\byarn\s+add\b",
    r"\bpip\s+install\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+checkout\b",
    r"\bgit\s+branch\s+-[dD]",
    r"\bchmod\b",
    r"\bchown\b",
]

AUTO_PATTERNS = [
    r"^\s*ls\b",
    r"^\s*cat\b",
    r"^\s*head\b",
    r"^\s*tail\b",
    r"^\s*grep\b",
    r"^\s*find\b",
    r"^\s*echo\b",
    r"^\s*pwd\b",
    r"^\s*wc\b",
    r"^\s*date\b",
    r"^\s*whoami\b",
    r"^\s*which\b",
    r"^\s*file\b",
    r"^\s*stat\b",
]

# File patterns for classification
BLOCKED_FILE_PATTERNS = [
    r"\.env$",
    r"\.env\.",
    r"\.key$",
    r"\.pem$",
    r"\.p12$",
    r"\.pfx$",
    r"id_rsa",
    r"id_ed25519",
    r"id_ecdsa",
    r"id_dsa",
    r"\.git/config$",
    r"\.git/credentials",
    r"secrets\.yaml$",
    r"secrets\.enc\.yaml$",
    r"\.aws/credentials",
    r"\.ssh/",
]

HIGH_SENSITIVITY_FILE_PATTERNS = [
    r"\.git/",
    r"\.gitignore$",
    r"config\.yaml$",
    r"config\.json$",
    r"settings\.py$",
    r"\.dockerignore$",
    r"Dockerfile$",
    r"docker-compose",
]


def classify_command(command: str) -> SensitivityLevel:
    """
    Classify a shell command by sensitivity level.
    
    Args:
        command: The shell command to classify
        
    Returns:
        SensitivityLevel for the command
    """
    command_lower = command.lower()
    
    # Check blocked patterns first
    for pattern in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE):
            return SensitivityLevel.BLOCKED
    
    # Check high sensitivity
    for pattern in HIGH_SENSITIVITY_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE):
            return SensitivityLevel.HIGH
    
    # Check prompt patterns
    for pattern in PROMPT_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE):
            return SensitivityLevel.PROMPT
    
    # Check auto-approve patterns
    for pattern in AUTO_PATTERNS:
        if re.search(pattern, command_lower, re.IGNORECASE):
            return SensitivityLevel.AUTO
    
    # Default to PROMPT for unknown commands
    return SensitivityLevel.PROMPT


def classify_file_operation(operation: str, path: str) -> SensitivityLevel:
    """
    Classify a file operation by sensitivity level.
    
    Args:
        operation: The operation type (read_file, edit_file, read_structure)
        path: The file path being accessed
        
    Returns:
        SensitivityLevel for the operation
    """
    # Check blocked patterns
    for pattern in BLOCKED_FILE_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            return SensitivityLevel.BLOCKED
    
    # Read operations on high-sensitivity files just need prompt
    if operation in ("read_file", "read_structure"):
        for pattern in HIGH_SENSITIVITY_FILE_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return SensitivityLevel.PROMPT
        return SensitivityLevel.AUTO
    
    # Edit operations on high-sensitivity files are HIGH
    if operation == "edit_file":
        for pattern in HIGH_SENSITIVITY_FILE_PATTERNS:
            if re.search(pattern, path, re.IGNORECASE):
                return SensitivityLevel.HIGH
        return SensitivityLevel.PROMPT
    
    return SensitivityLevel.PROMPT

