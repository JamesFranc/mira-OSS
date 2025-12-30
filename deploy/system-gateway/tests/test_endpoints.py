"""
Unit tests for System Gateway endpoints.

Tests /structure, /read, /edit, /execute endpoints including
path traversal prevention, timeout handling, and error responses.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Mock settings before importing app
@pytest.fixture(autouse=True)
def mock_settings():
    """Mock settings for all tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_config = MagicMock()
        mock_config.workspace_root = tmpdir
        mock_config.blocked_patterns = ["*.key", "*.env", ".git/config"]
        mock_config.max_file_size_bytes = 10 * 1024 * 1024
        mock_config.max_output_lines = 1000
        mock_config.max_timeout = 60
        mock_config.index_db_path = os.path.join(tmpdir, "index.db")
        mock_config.index_update_debounce_ms = 100
        mock_config.log_level = "INFO"
        mock_config.workspace_path = Path(tmpdir)

        with patch("config.settings", mock_config):
            yield tmpdir, mock_config


@pytest.fixture
def test_workspace(mock_settings):
    """Create test workspace with sample files."""
    tmpdir, _ = mock_settings

    # Create test files
    (Path(tmpdir) / "test.txt").write_text("line 1\nline 2\nline 3\n")
    (Path(tmpdir) / "subdir").mkdir()
    (Path(tmpdir) / "subdir" / "nested.py").write_text("print('hello')\n")
    (Path(tmpdir) / "secret.key").write_text("secret content")

    return tmpdir


@pytest.fixture
def client(test_workspace):
    """Create test client with mocked tree indexer."""
    with patch("services.tree_indexer.TreeIndexer") as MockIndexer:
        mock_indexer = MagicMock()
        mock_indexer.get_structure.return_value = {
            "root": test_workspace,
            "tree": [
                {"path": "test.txt", "name": "test.txt", "type": "file", "size": 20},
                {"path": "subdir", "name": "subdir", "type": "dir"},
            ],
            "stats": {"total_files": 2, "total_dirs": 1, "returned": 2}
        }
        MockIndexer.return_value = mock_indexer

        from main import app
        app.state.tree_indexer = mock_indexer

        with TestClient(app) as c:
            yield c


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_healthy(self, client):
        """Health check returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestStructureEndpoint:
    """Tests for POST /structure endpoint."""

    def test_structure_returns_tree(self, client):
        """Structure endpoint returns directory tree."""
        response = client.post("/structure", json={"path": "", "depth": 2})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "tree" in data
        assert "stats" in data

    def test_structure_with_depth_limit(self, client):
        """Structure respects depth limit."""
        response = client.post("/structure", json={"path": "", "depth": 1})
        assert response.status_code == 200

    def test_structure_invalid_depth(self, client):
        """Structure rejects invalid depth values."""
        response = client.post("/structure", json={"path": "", "depth": 10})
        assert response.status_code == 422  # Validation error


class TestReadEndpoint:
    """Tests for POST /read endpoint."""

    def test_read_file_success(self, client, test_workspace):
        """Read file returns content."""
        response = client.post("/read", json={"path": "test.txt"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "line 1" in data["content"]

    def test_read_file_with_line_range(self, client, test_workspace):
        """Read file respects line range."""
        response = client.post("/read", json={
            "path": "test.txt",
            "line_start": 1,
            "line_end": 2
        })
        assert response.status_code == 200
        data = response.json()
        assert data["lines_returned"] == 2

    def test_read_file_not_found(self, client):
        """Read returns 404 for missing file."""
        response = client.post("/read", json={"path": "nonexistent.txt"})
        assert response.status_code == 404

    def test_read_blocked_file(self, client, test_workspace):
        """Read blocks access to sensitive files."""
        response = client.post("/read", json={"path": "secret.key"})
        assert response.status_code == 400
        assert "blocked" in response.json()["detail"].lower()


class TestEditEndpoint:
    """Tests for POST /edit endpoint."""

    def test_edit_replace_line(self, client, test_workspace):
        """Edit can replace a line."""
        response = client.post("/edit", json={
            "path": "test.txt",
            "edits": [{"action": "replace", "line_start": 1, "content": "new line 1"}]
        })

    def test_edit_blocked_file(self, client, test_workspace):
        """Edit blocks access to sensitive files."""
        response = client.post("/edit", json={
            "path": "secret.key",
            "edits": [{"action": "replace", "line_start": 1, "content": "hacked"}]
        })
        assert response.status_code == 400


class TestExecuteEndpoint:
    """Tests for POST /execute endpoint."""

    def test_execute_simple_command(self, client):
        """Execute runs simple commands."""
        response = client.post("/execute", json={"command": "echo hello"})
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert "hello" in data["stdout"]

    def test_execute_with_timeout(self, client):
        """Execute respects timeout."""
        response = client.post("/execute", json={
            "command": "sleep 0.1",
            "timeout": 5
        })
        assert response.status_code == 200

    def test_execute_timeout_exceeded(self, client):
        """Execute returns error on timeout."""
        response = client.post("/execute", json={
            "command": "sleep 10",
            "timeout": 1
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("timed_out") is True or data.get("exit_code") != 0

    def test_execute_blocked_command(self, client):
        """Execute blocks dangerous commands."""
        response = client.post("/execute", json={"command": "sudo rm -rf /"})
        assert response.status_code == 400
        assert "blocked" in response.json()["detail"].lower()

    def test_execute_with_cwd(self, client, test_workspace):
        """Execute respects working directory."""
        response = client.post("/execute", json={
            "command": "pwd",
            "cwd": "subdir"
        })
        assert response.status_code == 200
        data = response.json()
        assert "subdir" in data["stdout"]


class TestPathTraversalPrevention:
    """Tests for path traversal attack prevention."""

    def test_read_path_traversal_blocked(self, client):
        """Read blocks path traversal attempts."""
        response = client.post("/read", json={"path": "../../../etc/passwd"})
        assert response.status_code == 400
        assert "traversal" in response.json()["detail"].lower() or "outside" in response.json()["detail"].lower()

    def test_edit_path_traversal_blocked(self, client):
        """Edit blocks path traversal attempts."""
        response = client.post("/edit", json={
            "path": "../../../etc/passwd",
            "edits": [{"action": "replace", "line_start": 1, "content": "hacked"}]
        })
        assert response.status_code == 400

    def test_structure_path_traversal_blocked(self, client):
        """Structure blocks path traversal attempts."""
        response = client.post("/structure", json={"path": "../../../"})
        assert response.status_code == 400

    def test_execute_cwd_traversal_blocked(self, client):
        """Execute blocks cwd traversal attempts."""
        response = client.post("/execute", json={
            "command": "ls",
            "cwd": "../../../"
        })
        assert response.status_code == 400
