"""
System Gateway - Sandboxed Execution Environment for Mira

Provides secure filesystem and command execution capabilities within
an isolated Docker container. All operations are constrained to the
mounted /workspace directory.
"""

import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import settings
from routers import structure, files, execute
from services.tree_indexer import TreeIndexer

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Global tree indexer instance
tree_indexer: TreeIndexer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown."""
    global tree_indexer
    
    logger.info("Starting System Gateway...")
    logger.info(f"Workspace root: {settings.workspace_root}")
    logger.info(f"Blocked patterns: {settings.blocked_patterns}")
    
    # Initialize tree indexer
    tree_indexer = TreeIndexer(settings.workspace_root)
    tree_indexer.start()
    logger.info("Tree indexer started")
    
    # Store in app state for access in routes
    app.state.tree_indexer = tree_indexer
    
    yield
    
    # Shutdown
    logger.info("Shutting down System Gateway...")
    if tree_indexer:
        tree_indexer.stop()
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Mira System Gateway",
    description="Sandboxed filesystem and command execution service",
    version="1.0.0",
    lifespan=lifespan
)


# Include routers
app.include_router(structure.router, tags=["structure"])
app.include_router(files.router, tags=["files"])
app.include_router(execute.router, tags=["execute"])


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "workspace_root": settings.workspace_root,
        "workspace_exists": os.path.isdir(settings.workspace_root)
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "internal_error",
            "message": str(exc)
        }
    )


def handle_signal(signum: int, frame: Any) -> None:
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.gateway_port,
        log_level=settings.log_level.lower()
    )

