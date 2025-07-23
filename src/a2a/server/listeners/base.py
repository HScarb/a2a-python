"""Base listener interface for A2A server communication."""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.tasks.push_notification_sender import BasePushNotificationSender


class ServerListener(ABC):
    """Abstract base class for server-side listeners.
    
    This interface defines the contract that all A2A server transport
    listeners must follow to handle incoming requests across different
    communication protocols (HTTP, RabbitMQ, gRPC, etc.).
    """

    @abstractmethod
    async def start(
        self, 
        handler: RequestHandler,
        push_notification_sender: Optional[BasePushNotificationSender] = None
    ) -> None:
        """Starts the listener and begins accepting requests.
        
        Args:
            handler: The request handler to process incoming requests
            push_notification_sender: Optional push notification sender for task updates
            
        Raises:
            ListenerError: If the listener fails to start
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stops the listener and cleans up resources.
        
        This method should gracefully shut down the listener, complete
        any pending requests, and clean up connections and resources.
        
        Raises:
            ListenerError: If the listener fails to stop cleanly
        """
        pass
        
    @abstractmethod
    def get_transport_info(self) -> Dict[str, Any]:
        """Returns transport-specific information.
        
        This information can be used to populate the AgentCard with
        the appropriate transport details (URLs, connection info, etc.)
        
        Returns:
            Dictionary containing transport-specific information such as:
            - transport: Transport type (e.g., "HTTP", "RabbitMQ", "gRPC")
            - url: Connection URL or endpoint
            - additional metadata specific to the transport
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Performs a health check on the listener.
        
        Returns:
            True if the listener is healthy and ready to accept requests,
            False otherwise
        """
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the listener is currently running.
        
        Returns:
            True if the listener is running, False otherwise
        """
        pass


class ListenerError(Exception):
    """Base exception for listener-related errors."""
    
    def __init__(self, message: str, transport: Optional[str] = None):
        super().__init__(message)
        self.transport = transport


class ListenerStartError(ListenerError):
    """Exception raised when a listener fails to start."""
    pass


class ListenerStopError(ListenerError):
    """Exception raised when a listener fails to stop cleanly."""
    pass


class ListenerConfigError(ListenerError):
    """Exception raised for listener configuration errors."""
    pass