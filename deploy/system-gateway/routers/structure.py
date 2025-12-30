"""
Directory structure endpoint for System Gateway.

Provides hierarchical filesystem views from the tree index.
"""

from typing import Any, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

from services.path_validator import PathValidator, PathValidationError

router = APIRouter()


class StructureRequest(BaseModel):
    """Request model for directory structure."""
    path: str = Field(default="", description="Relative path within workspace")
    depth: int = Field(default=2, ge=1, le=5, description="Directory traversal depth")
    include_hidden: bool = Field(default=False, description="Include hidden files")
    pattern: Optional[str] = Field(default=None, description="Glob pattern filter (e.g., '*.py')")


class StructureResponse(BaseModel):
    """Response model for directory structure."""
    success: bool
    root: str
    tree: list[dict[str, Any]]
    stats: dict[str, int]


@router.post("/structure", response_model=StructureResponse)
async def get_structure(request: Request, body: StructureRequest) -> StructureResponse:
    """
    Get directory structure from workspace.
    
    Returns hierarchical tree with file/directory information,
    limited by depth parameter to manage response size.
    """
    # Validate path
    validator = PathValidator()
    try:
        validator.validate(body.path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get tree indexer from app state
    tree_indexer = request.app.state.tree_indexer
    
    result = tree_indexer.get_structure(
        path=body.path,
        depth=body.depth,
        include_hidden=body.include_hidden,
        pattern=body.pattern
    )
    
    return StructureResponse(
        success=True,
        root=result["root"],
        tree=result["tree"],
        stats=result["stats"]
    )


@router.post("/index/refresh")
async def refresh_index(request: Request) -> dict[str, Any]:
    """
    Trigger a full reindex of the workspace.
    
    Useful after large batch operations or external changes.
    """
    tree_indexer = request.app.state.tree_indexer
    tree_indexer._full_reindex()
    
    return {
        "success": True,
        "message": "Index refresh completed"
    }

