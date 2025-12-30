"""
Command execution endpoint for System Gateway.

Provides sandboxed shell command execution within the workspace.
"""

import os
import shlex
import subprocess
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import settings
from services.path_validator import PathValidator, PathValidationError

router = APIRouter()

# Commands that are always blocked
BLOCKED_COMMANDS = {
    "sudo", "su", "chmod", "chown", "chgrp",
    "mount", "umount", "mkfs", "fdisk",
    "dd", "reboot", "shutdown", "halt", "init",
    "iptables", "ip6tables", "nft",
    "passwd", "useradd", "userdel", "usermod",
    "nc", "netcat", "ncat",  # Network tools (if network is disabled)
}

# Patterns that indicate dangerous commands
DANGEROUS_PATTERNS = [
    "| sh", "| bash", "| zsh",
    "`", "$(",  # Command substitution
    "> /dev/", ">> /dev/",
    "/etc/", "/var/", "/usr/",
    "~/.ssh", "~/.gnupg",
]


class ExecuteRequest(BaseModel):
    """Request model for command execution."""
    command: str = Field(..., min_length=1, description="Shell command to execute")
    timeout: int = Field(default=30, ge=1, description="Timeout in seconds")
    cwd: Optional[str] = Field(default=None, description="Working directory relative to /workspace")
    env: Optional[Dict[str, str]] = Field(default=None, description="Environment variable overrides")


class ExecuteResponse(BaseModel):
    """Response model for command execution."""
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False


def validate_command(command: str) -> None:
    """
    Validate command is safe to execute.
    
    Raises HTTPException if command is blocked.
    """
    # Check for blocked commands
    try:
        parts = shlex.split(command)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid command syntax")
    
    if not parts:
        raise HTTPException(status_code=400, detail="Empty command")
    
    base_cmd = os.path.basename(parts[0])
    
    if base_cmd in BLOCKED_COMMANDS:
        raise HTTPException(
            status_code=403,
            detail=f"Command blocked for security: {base_cmd}"
        )
    
    # Check for dangerous patterns
    for pattern in DANGEROUS_PATTERNS:
        if pattern in command:
            raise HTTPException(
                status_code=403,
                detail=f"Command contains blocked pattern: {pattern}"
            )


@router.post("/execute", response_model=ExecuteResponse)
async def execute_command(body: ExecuteRequest) -> ExecuteResponse:
    """
    Execute a shell command in the workspace.
    
    Commands are validated against a blocklist before execution.
    Output is captured and returned with timing information.
    """
    # Validate command
    validate_command(body.command)
    
    # Clamp timeout
    timeout = min(body.timeout, settings.max_timeout)
    
    # Validate and resolve working directory
    validator = PathValidator()
    if body.cwd:
        try:
            cwd = validator.validate(body.cwd)
        except PathValidationError as e:
            raise HTTPException(status_code=400, detail=f"Invalid cwd: {e}")
    else:
        cwd = settings.workspace_path
    
    # Build environment
    env = os.environ.copy()
    env["HOME"] = str(settings.workspace_path)
    env["PWD"] = str(cwd)
    if body.env:
        env.update(body.env)
    
    # Execute command
    start_time = time.time()
    try:
        result = subprocess.run(
            body.command,
            shell=True,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            timeout=timeout,
            text=True
        )
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Truncate output if too long
        stdout = result.stdout
        stderr = result.stderr
        truncated = False
        
        max_output = settings.max_output_lines * 100  # Rough char limit
        if len(stdout) > max_output:
            stdout = stdout[:max_output] + "\n... (output truncated)"
            truncated = True
        if len(stderr) > max_output:
            stderr = stderr[:max_output] + "\n... (output truncated)"
            truncated = True
        
        return ExecuteResponse(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=truncated
        )
        
    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecuteResponse(
            success=False,
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout} seconds",
            duration_ms=duration_ms
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution error: {e}")

