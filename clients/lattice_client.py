"""
Lattice client for federation messaging.

Thin HTTP client for communicating with the Lattice discovery service.
All federation logic (signing, queueing, retries) is handled by Lattice.
"""

import logging
from typing import Dict, Any, Optional

import httpx

from config.config_manager import config

logger = logging.getLogger(__name__)


class LatticeClient:
    """
    HTTP client for Lattice federation service.

    Provides methods to send federated messages and query server identity.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        """
        Initialize Lattice client.

        Args:
            base_url: Lattice service URL. Defaults to config.lattice.service_url
            timeout: Request timeout. Defaults to config.lattice.timeout
        """
        self.base_url = (base_url or config.lattice.service_url).rstrip("/")
        self.timeout = timeout or config.lattice.timeout

    def send_message(
        self,
        from_address: str,
        to_address: str,
        content: str,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send a federated message via Lattice.

        Args:
            from_address: Sender's federated address (user@domain)
            to_address: Recipient's federated address (user@domain)
            content: Message content
            priority: Message priority (0=normal, 1=high, 2=urgent)
            metadata: Optional metadata dict (location, etc.)

        Returns:
            Response with message_id and status

        Raises:
            httpx.HTTPError: If request fails
        """
        message_data = {
            "from_address": from_address,
            "to_address": to_address,
            "content": content,
            "priority": priority,
            "message_type": "pager"
        }

        if metadata:
            message_data["metadata"] = metadata

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/api/v1/messages/send",
                json=message_data
            )
            response.raise_for_status()
            return response.json()

    def get_identity(self) -> Dict[str, Any]:
        """
        Get this server's federation identity from Lattice.

        Returns:
            Dict with server_id, server_uuid, fingerprint, public_key

        Raises:
            httpx.HTTPError: If Lattice service unavailable
            ValueError: If federation not configured
        """
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/api/v1/identity")

            if response.status_code == 404:
                raise ValueError("Federation not configured - run lattice identity setup first")

            response.raise_for_status()
            return response.json()

    def get_status(self) -> Dict[str, Any]:
        """
        Get Lattice service health status.

        Returns:
            Status dict with health information

        Raises:
            httpx.HTTPError: If service unreachable
        """
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/api/v1/health")
            response.raise_for_status()
            return response.json()


# Global singleton instance
_lattice_client: Optional[LatticeClient] = None


def get_lattice_client() -> LatticeClient:
    """Get or create the global Lattice client instance."""
    global _lattice_client
    if _lattice_client is None:
        _lattice_client = LatticeClient()
    return _lattice_client
