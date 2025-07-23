"""HTTP transport implementation for A2A client."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from httpx_sse import SSEError, aconnect_sse

from .base import ClientTransport, TransportError, ProtocolError, TimeoutError, ConnectionError

logger = logging.getLogger(__name__)


class HttpTransport(ClientTransport):
    """HTTP-based transport implementation for A2A client.
    
    This transport uses HTTP/HTTPS with JSON-RPC 2.0 for request/response
    and Server-Sent Events (SSE) for streaming responses.
    """

    def __init__(
        self,
        base_url: str,
        httpx_client: Optional[httpx.AsyncClient] = None,
        default_timeout: float = 30.0,
    ):
        """Initialize HTTP transport.
        
        Args:
            base_url: The base URL of the A2A agent endpoint
            httpx_client: Optional HTTPX client instance. If None, creates a new one.
            default_timeout: Default timeout for requests in seconds
        """
        self.base_url = base_url.rstrip('/')
        self._httpx_client = httpx_client
        self._owns_client = httpx_client is None
        self.default_timeout = default_timeout
        self._push_notification_config: Optional[Dict[str, Any]] = None

    @property
    def httpx_client(self) -> httpx.AsyncClient:
        """Get or create the HTTPX client."""
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(timeout=self.default_timeout)
        return self._httpx_client

    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a single HTTP request and get response."""
        request_id = str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id
        }

        try:
            response = await self.httpx_client.post(
                self.base_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            
            response_data = response.json()
            
            # Handle JSON-RPC error responses
            if "error" in response_data:
                error = response_data["error"]
                raise ProtocolError(
                    f"JSON-RPC error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}",
                    status_code=response.status_code
                )
            
            return response_data.get("result", {})
            
        except httpx.ReadTimeout as e:
            raise TimeoutError(f"Request timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            raise TransportError(
                f"HTTP error {e.response.status_code}: {e}",
                status_code=e.response.status_code
            ) from e
        except json.JSONDecodeError as e:
            raise ProtocolError(f"Invalid JSON response: {e}") from e
        except httpx.RequestError as e:
            raise ConnectionError(f"Connection error: {e}") from e

    async def stream_request(
        self, method: str, params: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Send a streaming HTTP request using SSE."""
        request_id = str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id
        }

        try:
            async with aconnect_sse(
                self.httpx_client,
                "POST",
                self.base_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=None  # No timeout for streaming
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    try:
                        event_data = json.loads(sse.data)
                        
                        # Handle JSON-RPC error in stream
                        if "error" in event_data:
                            error = event_data["error"]
                            raise ProtocolError(
                                f"JSON-RPC stream error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}"
                            )
                        
                        yield event_data.get("result", event_data)
                        
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON in SSE event: {e}")
                        continue
                        
        except SSEError as e:
            raise ProtocolError(f"SSE protocol error: {e}") from e
        except httpx.RequestError as e:
            raise ConnectionError(f"Connection error during streaming: {e}") from e

    async def setup_push_notifications(
        self, callback_url: str, auth_token: Optional[str] = None
    ) -> bool:
        """Set up push notifications configuration.
        
        For HTTP transport, this stores the callback configuration
        that will be used when setting up push notification configs
        via the tasks/pushNotificationConfig/set method.
        """
        self._push_notification_config = {
            "callback_url": callback_url,
            "auth_token": auth_token
        }
        logger.info(f"Push notification callback configured for: {callback_url}")
        return True

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None

    async def health_check(self) -> bool:
        """Perform a health check by trying to connect to the base URL."""
        try:
            response = await self.httpx_client.get(
                f"{self.base_url}/.well-known/agent.json",
                timeout=5.0
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    def get_push_notification_config(self) -> Optional[Dict[str, Any]]:
        """Get the current push notification configuration."""
        return self._push_notification_config