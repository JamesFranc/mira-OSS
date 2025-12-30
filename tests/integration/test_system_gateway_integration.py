"""
Integration tests for System Gateway with real container.

End-to-end tests with running gateway container testing file operations,
command execution, and approval flow. Requires docker-compose for test environment.
"""

import os
import pytest
import httpx
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Skip all tests if gateway container is not running
GATEWAY_URL = os.environ.get("GATEWAY_TEST_URL", "http://localhost:8765")


def gateway_available():
    """Check if gateway container is available."""
    try:
        response = httpx.get(f"{GATEWAY_URL}/health", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not gateway_available(),
    reason="Gateway container not available. Start with: docker-compose up system-gateway"
)


@pytest.fixture
def client():
    """Create HTTP client for gateway."""
    with httpx.Client(base_url=GATEWAY_URL, timeout=30.0) as c:
        yield c


class TestHealthEndpoint:
    """Integration tests for health endpoint."""
    
    def test_health_check(self, client):
        """Health endpoint returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestStructureEndpoint:
    """Integration tests for structure endpoint."""
    
    def test_list_workspace_root(self, client):
        """Can list workspace root directory."""
        response = client.post("/structure", json={"path": "", "depth": 1})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "tree" in data
        assert "stats" in data
    
    def test_list_with_depth(self, client):
        """Can list with specified depth."""
        response = client.post("/structure", json={"path": "", "depth": 3})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestReadEndpoint:
    """Integration tests for read endpoint."""
    
    def test_read_existing_file(self, client):
        """Can read an existing file."""
        # This assumes there's at least one readable file in workspace
        # First get structure to find a file
        struct_response = client.post("/structure", json={"path": "", "depth": 1})
        if struct_response.status_code != 200:
            pytest.skip("Cannot get structure")
        
        tree = struct_response.json().get("tree", [])
        files = [e for e in tree if e.get("type") == "file"]
        
        if not files:
            pytest.skip("No files in workspace root")
        
        file_path = files[0]["path"]
        response = client.post("/read", json={"path": file_path})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "content" in data
    
    def test_read_nonexistent_file(self, client):
        """Reading nonexistent file returns 404."""
        response = client.post("/read", json={"path": "nonexistent_file_12345.txt"})
        assert response.status_code == 404


class TestEditEndpoint:
    """Integration tests for edit endpoint."""
    
    def test_edit_creates_and_modifies_file(self, client):
        """Can create and modify a test file."""
        test_file = f"test_integration_{os.getpid()}.txt"
        
        try:
            # Create file with initial content
            response = client.post("/edit", json={
                "path": test_file,
                "edits": [{"action": "insert", "line_start": 0, "content": "line 1\nline 2\nline 3"}],
                "create_if_missing": True
            })
            assert response.status_code == 200
            
            # Read back
            read_response = client.post("/read", json={"path": test_file})
            assert read_response.status_code == 200
            assert "line 1" in read_response.json()["content"]
            
            # Modify
            edit_response = client.post("/edit", json={
                "path": test_file,
                "edits": [{"action": "replace", "line_start": 2, "content": "modified line 2"}]
            })
            assert edit_response.status_code == 200
            
            # Verify modification
            verify_response = client.post("/read", json={"path": test_file})
            assert "modified line 2" in verify_response.json()["content"]
            
        finally:
            # Cleanup - delete test file
            client.post("/execute", json={"command": f"rm -f {test_file}"})


class TestExecuteEndpoint:
    """Integration tests for execute endpoint."""
    
    def test_execute_simple_command(self, client):
        """Can execute simple commands."""
        response = client.post("/execute", json={"command": "echo 'hello world'"})
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert "hello world" in data["stdout"]
    
    def test_execute_with_exit_code(self, client):
        """Captures non-zero exit codes."""
        response = client.post("/execute", json={"command": "exit 42"})
        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 42
    
    def test_execute_captures_stderr(self, client):
        """Captures stderr output."""
        response = client.post("/execute", json={"command": "echo 'error' >&2"})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data["stderr"]
    
    def test_execute_respects_timeout(self, client):
        """Commands timeout correctly."""
        response = client.post("/execute", json={
            "command": "sleep 10",
            "timeout": 1
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("timed_out") is True or data["exit_code"] != 0

