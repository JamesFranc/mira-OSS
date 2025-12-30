"""
File read/edit endpoints for System Gateway.

Provides line-based file reading and atomic editing operations.
"""

import difflib
import os
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import settings
from services.path_validator import PathValidator, PathValidationError

router = APIRouter()


class ReadRequest(BaseModel):
    """Request model for file reading."""
    path: str = Field(..., description="Relative path to file")
    line_start: Optional[int] = Field(default=None, ge=1, description="Start line (1-indexed)")
    line_end: Optional[int] = Field(default=None, ge=1, description="End line (inclusive)")


class ReadResponse(BaseModel):
    """Response model for file reading."""
    success: bool
    path: str
    content: str
    total_lines: int
    lines_returned: int
    truncated: bool
    is_binary: bool = False


@router.post("/read", response_model=ReadResponse)
async def read_file(body: ReadRequest) -> ReadResponse:
    """
    Read file contents with optional line range.
    
    Supports partial file reading to manage response size.
    Binary files are detected and flagged.
    """
    validator = PathValidator()
    
    try:
        resolved = validator.validate(body.path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")
    
    if resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")
    
    # Check file size
    file_size = resolved.stat().st_size
    if file_size > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {file_size} bytes (max: {settings.max_file_size_bytes})"
        )
    
    # Check for binary
    if validator.is_binary(resolved):
        return ReadResponse(
            success=True,
            path=body.path,
            content="[Binary file - content not displayed]",
            total_lines=0,
            lines_returned=0,
            truncated=False,
            is_binary=True
        )
    
    # Read file
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")
    
    total_lines = len(lines)
    
    # Apply line range
    start = (body.line_start or 1) - 1  # Convert to 0-indexed
    end = body.line_end or total_lines
    
    # Clamp to valid range
    start = max(0, min(start, total_lines))
    end = max(start, min(end, total_lines))
    
    selected_lines = lines[start:end]
    truncated = len(selected_lines) >= settings.max_output_lines
    
    if truncated:
        selected_lines = selected_lines[:settings.max_output_lines]
    
    return ReadResponse(
        success=True,
        path=body.path,
        content="".join(selected_lines),
        total_lines=total_lines,
        lines_returned=len(selected_lines),
        truncated=truncated
    )


class EditOperation(BaseModel):
    """Single edit operation."""
    action: Literal["replace", "insert", "delete"] = Field(..., description="Edit action")
    line_start: int = Field(..., ge=1, description="Start line (1-indexed)")
    line_end: Optional[int] = Field(default=None, ge=1, description="End line for replace/delete")
    content: Optional[str] = Field(default=None, description="New content for replace/insert")


class EditRequest(BaseModel):
    """Request model for file editing."""
    path: str = Field(..., description="Relative path to file")
    edits: List[EditOperation] = Field(..., min_length=1, description="List of edit operations")
    create_if_missing: bool = Field(default=False, description="Create file if it doesn't exist")


class EditResponse(BaseModel):
    """Response model for file editing."""
    success: bool
    path: str
    edits_applied: int
    new_line_count: int
    diff_preview: str


@router.post("/edit", response_model=EditResponse)
async def edit_file(body: EditRequest) -> EditResponse:
    """
    Apply atomic line-based edits to a file.
    
    All edits succeed together or the file is unchanged.
    Returns a unified diff preview of changes.
    """
    validator = PathValidator()
    
    try:
        resolved = validator.validate_for_write(body.path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Handle missing file
    if not resolved.exists():
        if body.create_if_missing:
            original_lines: List[str] = []
        else:
            raise HTTPException(status_code=404, detail=f"File not found: {body.path}")
    else:
        if resolved.is_dir():
            raise HTTPException(status_code=400, detail="Path is a directory")
        
        with open(resolved, "r", encoding="utf-8") as f:
            original_lines = f.readlines()
    
    # Apply edits (sort by line number descending to avoid offset issues)
    new_lines = original_lines.copy()
    sorted_edits = sorted(body.edits, key=lambda e: e.line_start, reverse=True)
    
    for edit in sorted_edits:
        idx = edit.line_start - 1  # Convert to 0-indexed
        
        if edit.action == "delete":
            end_idx = (edit.line_end or edit.line_start)
            del new_lines[idx:end_idx]
        
        elif edit.action == "replace":
            end_idx = (edit.line_end or edit.line_start)
            content_lines = (edit.content or "").splitlines(keepends=True)
            if content_lines and not content_lines[-1].endswith("\n"):
                content_lines[-1] += "\n"
            new_lines[idx:end_idx] = content_lines
        
        elif edit.action == "insert":
            content_lines = (edit.content or "").splitlines(keepends=True)
            if content_lines and not content_lines[-1].endswith("\n"):
                content_lines[-1] += "\n"
            new_lines[idx:idx] = content_lines
    
    # Generate diff
    diff = difflib.unified_diff(
        original_lines, new_lines,
        fromfile=f"a/{body.path}",
        tofile=f"b/{body.path}",
        lineterm=""
    )
    diff_preview = "\n".join(list(diff)[:50])  # Limit diff preview
    
    # Write atomically
    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing file: {e}")
    
    return EditResponse(
        success=True,
        path=body.path,
        edits_applied=len(body.edits),
        new_line_count=len(new_lines),
        diff_preview=diff_preview
    )

