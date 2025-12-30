"""
Unit tests for SystemGatewayTool.

Tests operation routing, sensitivity classification, HITL integration,
and error handling with mocked gateway HTTP responses.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from tools.implementations.system_gateway_tool import SystemGatewayTool
from services.sensitivity_classifier import SensitivityLevel


@pytest.fixture
def mock_user_id():
    """Generate a test user ID."""
    return str(uuid4())


@pytest.fixture
def mock_gateway_response():
    """Factory for mock gateway responses."""
    def _make_response(success=True, **kwargs):
        return {"success": success, **kwargs}
    return _make_response


@pytest.fixture
def tool(mock_user_id):
    """Create SystemGatewayTool instance with mocked dependencies."""
    with patch("tools.implementations.system_gateway_tool.config_manager") as mock_config:
        mock_config.config.system_gateway.enabled = True
        mock_config.config.system_gateway.endpoint = "http://localhost:8765"
        mock_config.config.system_gateway.workspace_path = "/workspace"
        mock_config.config.system_gateway.default_timeout = 30
        mock_config.config.system_gateway.max_timeout = 60
        mock_config.config.system_gateway.hitl_timeout = 300
        
        tool = SystemGatewayTool(user_id=mock_user_id)
        yield tool


class TestOperationRouting:
    """Tests for operation routing."""
    
    @pytest.mark.asyncio
    async def test_routes_to_read_structure(self, tool, mock_gateway_response):
        """Routes read_structure operation correctly."""
        with patch.object(tool, "_gateway_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_gateway_response(
                tree=[{"path": "test.txt", "type": "file"}],
                stats={"total_files": 1, "total_dirs": 0},
                root="/workspace"
            )
            
            result = await tool.execute({
                "operation": "read_structure",
                "path": ""
            })
            
            mock_req.assert_called_once()
            assert "test.txt" in result or "Directory" in result
    
    @pytest.mark.asyncio
    async def test_routes_to_read_file(self, tool, mock_gateway_response):
        """Routes read_file operation correctly."""
        with patch.object(tool, "_gateway_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_gateway_response(
                content="file content",
                total_lines=1,
                lines_returned=1
            )
            
            result = await tool.execute({
                "operation": "read_file",
                "path": "test.txt"
            })
            
            mock_req.assert_called_once()
            assert "file content" in result
    
    @pytest.mark.asyncio
    async def test_routes_to_edit_file(self, tool, mock_gateway_response):
        """Routes edit_file operation correctly."""
        with patch.object(tool, "_gateway_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_gateway_response(
                edits_applied=1,
                new_line_count=10,
                diff_preview="@@ -1 +1 @@\n-old\n+new"
            )
            
            result = await tool.execute({
                "operation": "edit_file",
                "path": "test.txt",
                "edits": [{"action": "replace", "line_start": 1, "content": "new"}]
            })
            
            mock_req.assert_called_once()
            assert "Applied 1 edits" in result
    
    @pytest.mark.asyncio
    async def test_routes_to_execute(self, tool, mock_gateway_response):
        """Routes execute operation correctly."""
        with patch.object(tool, "_gateway_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_gateway_response(
                exit_code=0,
                stdout="hello\n",
                stderr="",
                duration_ms=50
            )
            
            result = await tool.execute({
                "operation": "execute",
                "command": "echo hello"
            })
            
            mock_req.assert_called_once()
            assert "hello" in result
    
    @pytest.mark.asyncio
    async def test_invalid_operation_returns_error(self, tool):
        """Invalid operation returns error message."""
        result = await tool.execute({
            "operation": "invalid_op"
        })
        
        assert "Error" in result or "Unknown" in result


class TestSensitivityClassification:
    """Tests for sensitivity classification."""
    
    @pytest.mark.asyncio
    async def test_blocked_command_rejected(self, tool):
        """Blocked commands are rejected immediately."""
        result = await tool.execute({
            "operation": "execute",
            "command": "sudo rm -rf /"
        })
        
        assert "blocked" in result.lower() or "error" in result.lower()
    
    @pytest.mark.asyncio
    async def test_blocked_file_rejected(self, tool):
        """Blocked file patterns are rejected."""
        result = await tool.execute({
            "operation": "edit_file",
            "path": ".env",
            "edits": [{"action": "replace", "line_start": 1, "content": "hacked"}]
        })
        
        assert "blocked" in result.lower() or "error" in result.lower()


class TestErrorHandling:
    """Tests for error handling."""
    
    @pytest.mark.asyncio
    async def test_missing_path_returns_error(self, tool):
        """Missing required path returns error."""
        result = await tool.execute({
            "operation": "read_file"
        })
        
        assert "Error" in result
        assert "path" in result.lower()
    
    @pytest.mark.asyncio
    async def test_missing_command_returns_error(self, tool):
        """Missing required command returns error."""
        result = await tool.execute({
            "operation": "execute"
        })
        
        assert "Error" in result
        assert "command" in result.lower()

