"""
Anthropic Files API Manager.

Manages file uploads, lifecycle, and cleanup for structured data files
(CSV, XLSX, JSON) sent to code execution tool.
"""

import logging
from typing import Dict, Set, Optional
import anthropic
from anthropic import APIStatusError

from utils.user_context import get_current_user_id
from tools.repo import FILES_API_BETA_FLAG


class FilesManager:
    """
    Manages Anthropic Files API operations with segment-scoped lifecycle.

    Responsibilities:
    - Upload files to Anthropic Files API
    - Track uploaded files per segment for cleanup
    - Delete files when segment collapses
    - Handle API errors with recovery guidance

    Lifecycle:
    - Files persist for the duration of the conversation segment
    - Cleanup occurs when segment collapses (history compression)
    - Enables multi-turn code execution on same file
    """

    def __init__(self, anthropic_client: anthropic.Anthropic):
        """
        Initialize FilesManager with Anthropic client.

        Args:
            anthropic_client: Initialized Anthropic SDK client
        """
        self.client = anthropic_client
        self.logger = logging.getLogger("files_manager")
        # Track uploaded files: {segment_id: Set[file_id]}
        self._uploaded_files: Dict[str, Set[str]] = {}

    def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        media_type: str,
        segment_id: str
    ) -> str:
        """
        Upload file to Anthropic Files API.

        Args:
            file_bytes: File content as bytes
            filename: Original filename for tracking
            media_type: MIME type (e.g., "text/csv")
            segment_id: Segment ID for lifecycle tracking

        Returns:
            file_id for use in container_upload blocks

        Raises:
            ValueError: File too large (>32MB)
            RuntimeError: API errors (403, 404, etc.)
        """
        user_id = get_current_user_id()

        try:
            # Upload file with beta API
            self.logger.info(f"Uploading file {filename} ({media_type}) for segment {segment_id}")

            response = self.client.beta.files.upload(
                file=(filename, file_bytes, media_type),
                betas=[FILES_API_BETA_FLAG]
            )

            file_id = response.id

            # Track for cleanup by segment
            if segment_id not in self._uploaded_files:
                self._uploaded_files[segment_id] = set()
            self._uploaded_files[segment_id].add(file_id)

            self.logger.info(f"Uploaded file {filename} → file_id: {file_id} (segment: {segment_id})")
            return file_id

        except APIStatusError as e:
            if e.status_code == 413:
                self.logger.error(f"File too large: {filename} ({len(file_bytes)} bytes)")
                raise ValueError(
                    f"File too large for Files API. Maximum size: 32MB. "
                    f"Consider splitting the file or using data sampling. "
                    f"Current size: {len(file_bytes) / (1024*1024):.1f}MB"
                )
            elif e.status_code == 403:
                self.logger.error(f"Files API access denied: {e}")
                raise RuntimeError(
                    "Files API access denied. Check API key permissions for Files API beta access. "
                    "Contact Anthropic support if needed."
                )
            elif e.status_code == 404:
                self.logger.error(f"Files API endpoint not found: {e}")
                raise RuntimeError(
                    "Files API endpoint not found. Verify beta flag is set correctly."
                )
            else:
                self.logger.error(f"Files API error ({e.status_code}): {e}")
                raise RuntimeError(f"Files API error ({e.status_code}): {str(e)}")
        except Exception as e:
            self.logger.error(f"Unexpected error uploading file {filename}: {e}")
            raise RuntimeError(f"Failed to upload file: {str(e)}")

    def delete_file(self, file_id: str) -> None:
        """
        Delete single file by ID.

        Args:
            file_id: File ID from upload

        Note:
            Handles 404 gracefully (file may already be deleted)
        """
        try:
            self.logger.debug(f"Deleting file: {file_id}")
            self.client.beta.files.delete(
                file_id=file_id,
                betas=[FILES_API_BETA_FLAG]
            )
            self.logger.debug(f"Deleted file: {file_id}")
        except APIStatusError as e:
            if e.status_code == 404:
                # File already deleted or never existed - not an error
                self.logger.debug(f"File not found (may already be deleted): {file_id}")
            else:
                self.logger.warning(f"Error deleting file {file_id}: {e}")
        except Exception as e:
            # Log but don't fail request on cleanup errors
            self.logger.warning(f"Unexpected error deleting file {file_id}: {e}")

    def cleanup_segment_files(self, segment_id: str) -> None:
        """
        Delete all files uploaded during this segment.

        Called when segment collapses (conversation history compression).

        Args:
            segment_id: Segment ID to cleanup files for

        Note:
            Removes tracking and deletes all uploaded files for segment.
            Gracefully handles deletion failures (logs warnings).
        """
        if segment_id not in self._uploaded_files:
            return

        file_ids = self._uploaded_files.pop(segment_id)

        if not file_ids:
            return

        self.logger.info(f"Cleaning up {len(file_ids)} files for segment {segment_id}")

        for file_id in file_ids:
            self.delete_file(file_id)

        self.logger.info(f"Cleanup complete for segment {segment_id}")
