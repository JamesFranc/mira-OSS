"""
System Gateway Tool - Sandboxed filesystem and command execution.

Provides secure access to local filesystem operations and shell command
execution within an isolated Docker container. Includes HITL approval
for sensitive operations.
"""

import fnmatch
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from tools.repo import Tool
from tools.registry import registry
from utils import http_client


# --- Configuration ---

class SystemGatewayToolConfig(BaseModel):
    """Configuration for system_gateway_tool."""
    enabled: bool = Field(default=True, description="Whether this tool is enabled")


registry.register("system_gateway_tool", SystemGatewayToolConfig)


# --- Sensitivity Classification ---

class SensitivityLevel:
    AUTO = "auto"  # Execute immediately
    PROMPT = "prompt"  # Queue for approval, short timeout
    HIGH = "high"  # Queue for approval, explicit confirmation
    BLOCKED = "blocked"  # Reject immediately


# Commands that auto-approve
AUTO_APPROVE_COMMANDS = {
    "ls", "cat", "head", "tail", "grep", "find", "wc", "pwd", "echo",
    "tree", "file", "stat", "du", "df", "which", "whoami", "date",
    "env", "printenv", "basename", "dirname", "realpath", "readlink",
}

# Commands that require approval
PROMPT_COMMANDS = {
    "mv", "cp", "touch", "mkdir", "npm", "pip", "yarn", "pnpm",
    "python", "node", "bun", "cargo", "go", "make", "git status",
    "git log", "git diff", "git branch",
}

# High-risk commands requiring explicit confirmation
HIGH_RISK_PATTERNS = [
    r"^rm\s", r"^git\s+push", r"^git\s+checkout", r"^git\s+reset",
    r"^git\s+rebase", r"^git\s+merge", r"^chmod\s", r"^chown\s",
]

# Commands that are always blocked
BLOCKED_COMMANDS = {
    "sudo", "su", "mount", "umount", "reboot", "shutdown", "halt",
    "init", "systemctl", "service", "passwd", "useradd", "userdel",
}

# Dangerous patterns
BLOCKED_PATTERNS = [
    r"\|\s*sh\b", r"\|\s*bash\b", r"\|\s*zsh\b",  # Pipe to shell
    r"`", r"\$\(",  # Command substitution
    r">\s*/dev/", r">>\s*/dev/",  # Writing to devices
    r"/etc/", r"/var/", r"/usr/",  # System directories
    r"~/.ssh", r"~/.gnupg",  # Sensitive user directories
]


def classify_command(command: str) -> str:
    """Classify command sensitivity level."""
    command_lower = command.lower().strip()
    
    # Check blocked patterns first
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command_lower):
            return SensitivityLevel.BLOCKED
    
    # Get base command
    parts = command_lower.split()
    if not parts:
        return SensitivityLevel.BLOCKED
    
    base_cmd = parts[0].split("/")[-1]  # Handle full paths
    
    # Check blocked commands
    if base_cmd in BLOCKED_COMMANDS:
        return SensitivityLevel.BLOCKED
    
    # Check high-risk patterns
    for pattern in HIGH_RISK_PATTERNS:
        if re.match(pattern, command_lower):
            return SensitivityLevel.HIGH
    
    # Check auto-approve
    if base_cmd in AUTO_APPROVE_COMMANDS:
        return SensitivityLevel.AUTO
    
    # Check prompt commands
    if base_cmd in PROMPT_COMMANDS:
        return SensitivityLevel.PROMPT
    
    # Default to prompt for unknown commands
    return SensitivityLevel.PROMPT


def classify_file_operation(operation: str, path: str) -> str:
    """Classify file operation sensitivity."""
    # Read operations are generally safe
    if operation in ("read_structure", "read_file"):
        return SensitivityLevel.AUTO
    
    # Write operations need more scrutiny
    if operation == "edit_file":
        # Check for sensitive file patterns
        sensitive_patterns = [
            "*.env", "*.key", "*.pem", "*.crt", ".git/config",
            "**/secrets/**", "**/.ssh/**", "**/credentials*"
        ]
        for pattern in sensitive_patterns:
            if fnmatch.fnmatch(path, pattern):
                return SensitivityLevel.HIGH
        return SensitivityLevel.PROMPT
    
    return SensitivityLevel.AUTO


# --- Tool Implementation ---

class SystemGatewayTool(Tool):
    """
    System Gateway Tool for sandboxed filesystem and command execution.
    
    Operations:
    - read_structure: Get directory tree with depth limiting
    - read_file: Read file contents with line range support
    - edit_file: Atomic line-based file edits
    - execute: Run shell commands in sandbox
    """
    
    name = "system_gateway_tool"
    description = "Sandboxed filesystem and command execution"
    simple_description = "reads files and runs commands"

    def __init__(self, user_id: str):
        super().__init__(user_id)
        self.logger = logging.getLogger(__name__)
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load gateway configuration."""
        from config.config_manager import config_manager
        cfg = config_manager.config.system_gateway
        return {
            "endpoint": cfg.endpoint,
            "default_timeout": cfg.default_timeout,
            "max_timeout": cfg.max_timeout,
            "hitl_timeout": cfg.hitl_timeout,
            "auto_approve_patterns": cfg.auto_approve_patterns,
            "blocked_patterns": cfg.blocked_patterns,
        }

    @property
    def input_schema(self) -> Dict[str, Any]:
        """Define tool input schema."""
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["read_structure", "read_file", "edit_file", "execute"],
                    "description": "Operation to perform"
                },
                "path": {
                    "type": "string",
                    "description": "File or directory path (relative to workspace)"
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 2,
                    "description": "Directory depth for read_structure"
                },
                "line_start": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Start line for read_file"
                },
                "line_end": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "End line for read_file"
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["replace", "insert", "delete"]},
                            "line_start": {"type": "integer", "minimum": 1},
                            "line_end": {"type": "integer", "minimum": 1},
                            "content": {"type": "string"}
                        },
                        "required": ["action", "line_start"]
                    },
                    "description": "Edit operations for edit_file"
                },
                "command": {
                    "type": "string",
                    "description": "Shell command for execute"
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Command timeout in seconds"
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for execute"
                }
            },
            "required": ["operation"]
        }

    async def run(self, **kwargs) -> str:
        """Execute the requested operation."""
        operation = kwargs.get("operation")

        if operation == "read_structure":
            return await self._read_structure(kwargs)
        elif operation == "read_file":
            return await self._read_file(kwargs)
        elif operation == "edit_file":
            return await self._edit_file(kwargs)
        elif operation == "execute":
            return await self._execute(kwargs)
        else:
            return f"Unknown operation: {operation}"

    async def _read_structure(self, params: Dict[str, Any]) -> str:
        """Get directory structure."""
        from services.gateway_audit_log import get_audit_logger

        path = params.get("path", "")
        depth = params.get("depth", 2)

        response = await self._gateway_request("POST", "/structure", {
            "path": path,
            "depth": depth,
            "include_hidden": False
        })

        success = response.get("success", False)
        get_audit_logger().log_read_structure(self.user_id, path, success)

        if not success:
            return f"Error: {response.get('detail', 'Unknown error')}"

        # Format tree output
        tree = response.get("tree", [])
        stats = response.get("stats", {})

        lines = [f"Directory: {response.get('root', path)}"]
        lines.append(f"({stats.get('total_files', 0)} files, {stats.get('total_dirs', 0)} directories)")
        lines.append("")

        for entry in tree[:100]:  # Limit output
            prefix = "📁 " if entry.get("type") == "dir" else "📄 "
            size = entry.get("size", "")
            size_str = f" ({self._format_size(size)})" if size else ""
            lines.append(f"{prefix}{entry.get('path', '')}{size_str}")

        if len(tree) > 100:
            lines.append(f"... and {len(tree) - 100} more entries")

        return "\n".join(lines)

    async def _read_file(self, params: Dict[str, Any]) -> str:
        """Read file contents."""
        from services.gateway_audit_log import get_audit_logger

        path = params.get("path")
        if not path:
            return "Error: path is required for read_file"

        response = await self._gateway_request("POST", "/read", {
            "path": path,
            "line_start": params.get("line_start"),
            "line_end": params.get("line_end")
        })

        success = response.get("success", False)
        lines_returned = response.get("lines_returned", 0)
        get_audit_logger().log_read_file(self.user_id, path, success, lines_returned)

        if not success:
            return f"Error: {response.get('detail', 'Unknown error')}"

        if response.get("is_binary"):
            return f"[Binary file: {path}]"

        content = response.get("content", "")
        total = response.get("total_lines", 0)
        truncated = response.get("truncated", False)

        header = f"File: {path} ({lines_returned}/{total} lines)"
        if truncated:
            header += " [truncated]"

        return f"{header}\n{'=' * 40}\n{content}"

    async def _edit_file(self, params: Dict[str, Any]) -> str:
        """Edit file with atomic operations."""
        from services.gateway_audit_log import get_audit_logger

        path = params.get("path")
        edits = params.get("edits")

        if not path:
            return "Error: path is required for edit_file"
        if not edits:
            return "Error: edits array is required for edit_file"

        # Check sensitivity and get approval if needed
        sensitivity = classify_file_operation("edit_file", path)
        if sensitivity == SensitivityLevel.BLOCKED:
            get_audit_logger().log_blocked(self.user_id, "edit_file", path, "blocked pattern")
            return f"Error: Editing this file is blocked: {path}"

        if sensitivity in (SensitivityLevel.PROMPT, SensitivityLevel.HIGH):
            approved = await self._request_approval(
                f"Edit file: {path}",
                f"Edits: {len(edits)} operations",
                sensitivity
            )
            if not approved:
                return "Operation cancelled: User did not approve file edit"

        response = await self._gateway_request("POST", "/edit", {
            "path": path,
            "edits": edits,
            "create_if_missing": False
        })

        success = response.get("success", False)
        applied = response.get("edits_applied", 0)
        get_audit_logger().log_edit_file(self.user_id, path, success, applied)

        if not success:
            return f"Error: {response.get('detail', 'Unknown error')}"

        diff = response.get("diff_preview", "")
        new_lines = response.get("new_line_count", 0)

        return f"Applied {applied} edits to {path} ({new_lines} lines)\n\n{diff}"

    async def _execute(self, params: Dict[str, Any]) -> str:
        """Execute shell command."""
        from services.gateway_audit_log import get_audit_logger

        command = params.get("command")
        if not command:
            return "Error: command is required for execute"

        # Classify command sensitivity
        sensitivity = classify_command(command)

        if sensitivity == SensitivityLevel.BLOCKED:
            get_audit_logger().log_blocked(self.user_id, "execute", command, "blocked command")
            return f"Error: Command blocked for security: {command}"

        if sensitivity in (SensitivityLevel.PROMPT, SensitivityLevel.HIGH):
            approved = await self._request_approval(
                f"Execute command: {command}",
                f"Working directory: {params.get('cwd', 'workspace root')}",
                sensitivity
            )
            if not approved:
                return "Operation cancelled: User did not approve command"

        timeout = min(
            params.get("timeout", self._config["default_timeout"]),
            self._config["max_timeout"]
        )

        response = await self._gateway_request("POST", "/execute", {
            "command": command,
            "timeout": timeout,
            "cwd": params.get("cwd"),
            "env": params.get("env")
        })

        if "detail" in response and not response.get("success", True):
            get_audit_logger().log_execute(self.user_id, command, False, -1, 0)
            return f"Error: {response.get('detail')}"

        exit_code = response.get("exit_code", -1)
        stdout = response.get("stdout", "")
        stderr = response.get("stderr", "")
        duration = response.get("duration_ms", 0)
        truncated = response.get("truncated", False)

        success = exit_code == 0
        get_audit_logger().log_execute(self.user_id, command, success, exit_code, duration)

        lines = [f"Command: {command}"]
        lines.append(f"Exit code: {exit_code} ({duration}ms)")

        if truncated:
            lines.append("[Output truncated]")

        if stdout:
            lines.append("\n--- stdout ---")
            lines.append(stdout)

        if stderr:
            lines.append("\n--- stderr ---")
            lines.append(stderr)

        return "\n".join(lines)

    async def _gateway_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to gateway container."""
        url = f"{self._config['endpoint']}{path}"

        try:
            if method == "POST":
                response = await http_client.post(url, json=data)
            else:
                response = await http_client.get(url)

            return response.json()
        except Exception as e:
            self.logger.error(f"Gateway request failed: {e}")
            return {"success": False, "detail": str(e)}

    async def _request_approval(
        self,
        action: str,
        details: str,
        sensitivity: str
    ) -> bool:
        """Request HITL approval for sensitive operation."""
        from services.hitl_approval_service import get_hitl_service, ApprovalStatus

        service = get_hitl_service()

        # Queue the approval request
        request = await service.queue_approval(
            user_id=self.user_id,
            operation=action,
            details={"description": details, "sensitivity": sensitivity},
            sensitivity=sensitivity
        )

        self.logger.info(f"Queued approval {request.id} for: {action}")

        # Wait for decision (with timeout from config)
        result = await service.wait_for_decision(
            request.id,
            max_wait=self._config["hitl_timeout"]
        )

        if not result:
            self.logger.warning(f"Approval {request.id} expired")
            return False

        if result.status == ApprovalStatus.APPROVED:
            self.logger.info(f"Approval {request.id} granted")
            return True
        elif result.status == ApprovalStatus.REJECTED:
            self.logger.info(f"Approval {request.id} rejected")
            return False
        else:
            # Still pending after timeout
            self.logger.warning(f"Approval {request.id} timed out (still pending)")
            return False

    def _format_size(self, size: int) -> str:
        """Format file size for display."""
        if not size:
            return ""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

