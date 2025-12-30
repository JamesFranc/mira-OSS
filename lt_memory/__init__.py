"""
LT_Memory Module - Long-term memory system for MIRA.

Factory-based initialization with explicit dependency management.
"""
import logging
from typing import Dict, Any, Optional

from config.config import LTMemoryConfig
from lt_memory.factory import LTMemoryFactory, get_lt_memory_factory
from lt_memory.db_access import LTMemoryDB
from lt_memory.vector_ops import VectorOps
from lt_memory.extraction import ExtractionService
from lt_memory.linking import LinkingService
from lt_memory.refinement import RefinementService
from lt_memory.batching import BatchingService
from lt_memory.proactive import ProactiveService
from lt_memory.models import (
    Memory,
    ExtractedMemory,
    MemoryLink,
    Entity,
    ProcessingChunk,
    ExtractionBatch,
    PostProcessingBatch,
    RefinementCandidate,
    ConsolidationCluster
)

logger = logging.getLogger(__name__)


# Cached extraction LLM routing config
_extraction_llm_config: Optional[Dict[str, Any]] = None


def get_extraction_llm_kwargs() -> Dict[str, Any]:
    """
    Get LLM routing kwargs for LT Memory extraction operations.

    Loads extraction config from internal_llm table and returns kwargs
    suitable for passing to LLMProvider.generate_response().

    Returns:
        Dict with endpoint_url, model_override, api_key_override if configured,
        or empty dict if extraction config not available.
    """
    global _extraction_llm_config

    if _extraction_llm_config is not None:
        return _extraction_llm_config.copy()

    try:
        from utils.user_context import get_internal_llm
        from clients.secrets.compat import get_api_key

        extraction_config = get_internal_llm('extraction')
        api_key = (
            get_api_key(extraction_config.api_key_name)
            if extraction_config.api_key_name else None
        )

        _extraction_llm_config = {
            "endpoint_url": extraction_config.endpoint_url,
            "model_override": extraction_config.model,
        }
        if api_key:
            _extraction_llm_config["api_key_override"] = api_key

        logger.info(
            f"Extraction LLM routing: {extraction_config.model} via {extraction_config.endpoint_url}"
        )
    except KeyError:
        logger.warning(
            "No 'extraction' entry in internal_llm table - LT Memory will use default LLMProvider"
        )
        _extraction_llm_config = {}

    return _extraction_llm_config.copy()

__all__ = [
    # Factory
    'LTMemoryFactory',
    'get_lt_memory_factory',

    # Helpers
    'get_extraction_llm_kwargs',

    # Classes (for type hints)
    'LTMemoryDB',
    'VectorOps',
    'ExtractionService',
    'LinkingService',
    'RefinementService',
    'BatchingService',
    'ProactiveService',
    'LTMemoryConfig',

    # Models
    'Memory',
    'ExtractedMemory',
    'MemoryLink',
    'Entity',
    'ProcessingChunk',
    'ExtractionBatch',
    'PostProcessingBatch',
    'RefinementCandidate',
    'ConsolidationCluster',
]
