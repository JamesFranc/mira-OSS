"""
Unit tests for HITLApprovalService.

Tests queue_approval, get_status, approve, reject, and TTL expiration
with mocked Valkey client.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import json

from services.hitl_approval_service import (
    HITLApprovalService,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalDecision,
)
from services.sensitivity_classifier import SensitivityLevel


@pytest.fixture
def mock_valkey():
    """Create mock Valkey client."""
    mock = MagicMock()
    mock.setex = MagicMock()
    mock.get = MagicMock()
    mock.delete = MagicMock()
    mock.keys = MagicMock(return_value=[])
    return mock


@pytest.fixture
def service(mock_valkey):
    """Create HITLApprovalService with mocked Valkey."""
    with patch("services.hitl_approval_service.get_valkey_client") as mock_get:
        mock_get.return_value = mock_valkey
        svc = HITLApprovalService()
        svc._valkey = mock_valkey
        yield svc


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return str(uuid4())


class TestQueueApproval:
    """Tests for queue_approval method."""
    
    def test_queue_approval_creates_request(self, service, user_id, mock_valkey):
        """queue_approval creates and stores approval request."""
        approval_id = service.queue_approval(
            user_id=user_id,
            operation="execute",
            target="rm -rf /tmp/test",
            description="Delete test directory",
            sensitivity=SensitivityLevel.HIGH
        )
        
        assert approval_id is not None
        mock_valkey.setex.assert_called_once()
        
        # Verify stored data
        call_args = mock_valkey.setex.call_args
        key = call_args[0][0]
        ttl = call_args[0][1]
        data = json.loads(call_args[0][2])
        
        assert f"hitl:approval:{approval_id}" == key
        assert ttl == 300  # Default TTL
        assert data["user_id"] == user_id
        assert data["operation"] == "execute"
        assert data["status"] == "pending"
    
    def test_queue_approval_with_custom_ttl(self, service, user_id, mock_valkey):
        """queue_approval respects custom TTL."""
        approval_id = service.queue_approval(
            user_id=user_id,
            operation="edit_file",
            target="/etc/config",
            description="Edit config",
            sensitivity=SensitivityLevel.PROMPT,
            ttl_seconds=600
        )
        
        call_args = mock_valkey.setex.call_args
        ttl = call_args[0][1]
        assert ttl == 600


class TestGetStatus:
    """Tests for get_status method."""
    
    def test_get_status_returns_pending(self, service, user_id, mock_valkey):
        """get_status returns pending status for queued request."""
        approval_id = str(uuid4())
        mock_valkey.get.return_value = json.dumps({
            "approval_id": approval_id,
            "user_id": user_id,
            "operation": "execute",
            "target": "ls",
            "description": "List files",
            "sensitivity": "prompt",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z"
        })
        
        status = service.get_status(approval_id)
        
        assert status is not None
        assert status.status == ApprovalStatus.PENDING
    
    def test_get_status_returns_none_for_missing(self, service, mock_valkey):
        """get_status returns None for non-existent request."""
        mock_valkey.get.return_value = None
        
        status = service.get_status(str(uuid4()))
        
        assert status is None


class TestApprove:
    """Tests for approve method."""
    
    def test_approve_updates_status(self, service, user_id, mock_valkey):
        """approve updates request status to approved."""
        approval_id = str(uuid4())
        mock_valkey.get.return_value = json.dumps({
            "approval_id": approval_id,
            "user_id": user_id,
            "operation": "execute",
            "target": "npm install",
            "description": "Install dependencies",
            "sensitivity": "prompt",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z"
        })
        
        result = service.approve(approval_id)
        
        assert result is True
        mock_valkey.setex.assert_called()
        
        # Verify updated status
        call_args = mock_valkey.setex.call_args
        data = json.loads(call_args[0][2])
        assert data["status"] == "approved"
    
    def test_approve_returns_false_for_missing(self, service, mock_valkey):
        """approve returns False for non-existent request."""
        mock_valkey.get.return_value = None
        
        result = service.approve(str(uuid4()))
        
        assert result is False


class TestReject:
    """Tests for reject method."""
    
    def test_reject_updates_status(self, service, user_id, mock_valkey):
        """reject updates request status to rejected."""
        approval_id = str(uuid4())
        mock_valkey.get.return_value = json.dumps({
            "approval_id": approval_id,
            "user_id": user_id,
            "operation": "execute",
            "target": "rm -rf /",
            "description": "Dangerous command",
            "sensitivity": "high",
            "status": "pending",
            "created_at": "2024-01-01T00:00:00Z"
        })
        
        result = service.reject(approval_id, reason="Too dangerous")
        
        assert result is True
        mock_valkey.setex.assert_called()
        
        # Verify updated status and reason
        call_args = mock_valkey.setex.call_args
        data = json.loads(call_args[0][2])
        assert data["status"] == "rejected"
        assert data["rejection_reason"] == "Too dangerous"

