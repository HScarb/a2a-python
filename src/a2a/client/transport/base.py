"""Base transport interface for A2A client communication."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, Dict, Optional


class ClientTransport(ABC):
    """Abstract base class for client-side communication transports.
    
    This interface defines the contract that all A2A client transport
    implementations must follow to support the three interaction modes:
    1. Request/Response (message/send, tasks/get, tasks/cancel, etc.)
    2. Streaming (message/stream, tasks/resubscribe)
    3. Push Notifications (via callback configuration)
    """

    @abstractmethod
    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sends a single request and expects a single response.
        
        Used for: message/send, tasks/get, tasks/cancel, 
                 tasks/pushNotificationConfig/set|get
        
        Args:
            method: The JSON-RPC method name (e.g., "message/send")
            params: The method parameters as a dictionary
            
        Returns:
            The response payload as a dictionary
            
        Raises:
            TransportError: If the request fails due to transport issues
            ProtocolError: If the response format is invalid
        """
        pass

    @abstractmethod
    async def stream_request(
        self, method: str, params: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Sends a request and streams responses back.
        
        Used for: message/stream, tasks/resubscribe
        
        Args:
            method: The JSON-RPC method name (e.g., "message/stream")
            params: The method parameters as a dictionary
            
        Yields:
            Response payloads as dictionaries from the stream
            
        Raises:
            TransportError: If the request fails due to transport issues
            ProtocolError: If a response format is invalid
        """
        yield {}  # Required for async generator

    @abstractmethod
    async def setup_push_notifications(
        self, callback_url: str, auth_token: Optional[str] = None
    ) -> bool:
        """Sets up push notification configuration for the transport.
        
        Args:
            callback_url: The URL/queue/topic to receive push notifications.
                         Format depends on the transport (HTTP URL, RabbitMQ queue, etc.)
            auth_token: Optional authentication token for the callback
            
        Returns:
            True if setup successful, False otherwise
            
        Raises:
            TransportError: If setup fails due to transport issues
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Closes the transport connection and cleans up resources.
        
        This method should be called when the client is done using the transport
        to ensure proper cleanup of connections, queues, and other resources.
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Performs a health check on the transport connection.
        
        Returns:
            True if the transport is healthy and ready to use, False otherwise
        """
        pass


class TransportError(Exception):
    """Base exception for transport-related errors."""
    
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ProtocolError(TransportError):
    """Exception for protocol-level errors (invalid responses, etc.)."""
    pass


class TimeoutError(TransportError):
    """Exception for request timeout errors."""
    pass


class ConnectionError(TransportError):
    """Exception for connection-related errors."""
    pass