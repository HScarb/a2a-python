"""Transport-based A2A Client implementation."""

import logging
from collections.abc import AsyncGenerator
from typing import Any, Optional
from uuid import uuid4

from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transport.base import ClientTransport, TransportError, ProtocolError
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    CancelTaskResponse,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskRequest,
    GetTaskResponse,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
)
from a2a.utils.telemetry import SpanKind, trace_class

logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.CLIENT)
class TransportA2AClient:
    """Transport-based A2A Client for interacting with an A2A agent.
    
    This client uses pluggable transport implementations to communicate
    with A2A agents over different protocols (HTTP, RabbitMQ, gRPC, etc.).
    """

    def __init__(
        self,
        transport: ClientTransport,
        agent_card: Optional[AgentCard] = None,
        interceptors: Optional[list[ClientCallInterceptor]] = None,
    ):
        """Initialize the transport-based A2A client.

        Args:
            transport: The transport implementation to use for communication
            agent_card: Optional agent card object for metadata
            interceptors: Optional list of client call interceptors
        """
        self.transport = transport
        self.agent_card = agent_card
        self.interceptors = interceptors or []

    async def _apply_interceptors(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        context: Optional[ClientCallContext],
    ) -> dict[str, Any]:
        """Apply all registered interceptors to the request."""
        final_request_payload = request_payload

        for interceptor in self.interceptors:
            # Note: HTTP-specific kwargs are handled by the transport layer
            final_request_payload, _ = await interceptor.intercept(
                method_name,
                final_request_payload,
                {},  # Empty http_kwargs since transport handles this
                self.agent_card,
                context,
            )
        return final_request_payload

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: Optional[ClientCallContext] = None,
    ) -> SendMessageResponse:
        """Send a non-streaming message request to the agent.

        Args:
            request: The SendMessageRequest object containing the message and configuration
            context: Optional client call context

        Returns:
            A SendMessageResponse object containing the agent's response

        Raises:
            TransportError: If a transport-level error occurs
            ProtocolError: If the response format is invalid
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors
        payload = await self._apply_interceptors(
            'message/send',
            request.model_dump(mode='json', exclude_none=True),
            context,
        )

        try:
            response_data = await self.transport.send_request('message/send', payload)
            return SendMessageResponse.model_validate(response_data)
        except (TransportError, ProtocolError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in send_message: {e}")
            raise TransportError(f"Unexpected error: {e}") from e

    async def send_message_streaming(
        self,
        request: SendStreamingMessageRequest,
        *,
        context: Optional[ClientCallContext] = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
        """Send a streaming message request to the agent.

        Args:
            request: The SendStreamingMessageRequest object
            context: Optional client call context

        Yields:
            SendStreamingMessageResponse objects as they are received

        Raises:
            TransportError: If a transport-level error occurs
            ProtocolError: If a response format is invalid
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors
        payload = await self._apply_interceptors(
            'message/stream',
            request.model_dump(mode='json', exclude_none=True),
            context,
        )

        try:
            async for response_data in self.transport.stream_request('message/stream', payload):
                yield SendStreamingMessageResponse.model_validate(response_data)
        except (TransportError, ProtocolError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in send_message_streaming: {e}")
            raise TransportError(f"Unexpected error: {e}") from e

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: Optional[ClientCallContext] = None,
    ) -> GetTaskResponse:
        """Retrieve the current state and history of a specific task.

        Args:
            request: The GetTaskRequest object specifying the task ID
            context: Optional client call context

        Returns:
            A GetTaskResponse object containing the Task

        Raises:
            TransportError: If a transport-level error occurs
            ProtocolError: If the response format is invalid
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors
        payload = await self._apply_interceptors(
            'tasks/get',
            request.model_dump(mode='json', exclude_none=True),
            context,
        )

        try:
            response_data = await self.transport.send_request('tasks/get', payload)
            return GetTaskResponse.model_validate(response_data)
        except (TransportError, ProtocolError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in get_task: {e}")
            raise TransportError(f"Unexpected error: {e}") from e

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: Optional[ClientCallContext] = None,
    ) -> CancelTaskResponse:
        """Request the agent to cancel a specific task.

        Args:
            request: The CancelTaskRequest object specifying the task ID
            context: Optional client call context

        Returns:
            A CancelTaskResponse object containing the updated Task

        Raises:
            TransportError: If a transport-level error occurs
            ProtocolError: If the response format is invalid
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors
        payload = await self._apply_interceptors(
            'tasks/cancel',
            request.model_dump(mode='json', exclude_none=True),
            context,
        )

        try:
            response_data = await self.transport.send_request('tasks/cancel', payload)
            return CancelTaskResponse.model_validate(response_data)
        except (TransportError, ProtocolError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in cancel_task: {e}")
            raise TransportError(f"Unexpected error: {e}") from e

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        context: Optional[ClientCallContext] = None,
    ) -> SetTaskPushNotificationConfigResponse:
        """Set or update the push notification configuration for a task.

        Args:
            request: The SetTaskPushNotificationConfigRequest object
            context: Optional client call context

        Returns:
            A SetTaskPushNotificationConfigResponse object

        Raises:
            TransportError: If a transport-level error occurs
            ProtocolError: If the response format is invalid
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors
        payload = await self._apply_interceptors(
            'tasks/pushNotificationConfig/set',
            request.model_dump(mode='json', exclude_none=True),
            context,
        )

        try:
            response_data = await self.transport.send_request(
                'tasks/pushNotificationConfig/set', 
                payload
            )
            return SetTaskPushNotificationConfigResponse.model_validate(response_data)
        except (TransportError, ProtocolError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in set_task_callback: {e}")
            raise TransportError(f"Unexpected error: {e}") from e

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: Optional[ClientCallContext] = None,
    ) -> GetTaskPushNotificationConfigResponse:
        """Retrieve the push notification configuration for a task.

        Args:
            request: The GetTaskPushNotificationConfigRequest object
            context: Optional client call context

        Returns:
            A GetTaskPushNotificationConfigResponse object

        Raises:
            TransportError: If a transport-level error occurs
            ProtocolError: If the response format is invalid
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors
        payload = await self._apply_interceptors(
            'tasks/pushNotificationConfig/get',
            request.model_dump(mode='json', exclude_none=True),
            context,
        )

        try:
            response_data = await self.transport.send_request(
                'tasks/pushNotificationConfig/get', 
                payload
            )
            return GetTaskPushNotificationConfigResponse.model_validate(response_data)
        except (TransportError, ProtocolError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in get_task_callback: {e}")
            raise TransportError(f"Unexpected error: {e}") from e

    async def setup_push_notifications(
        self, 
        callback_url: str, 
        auth_token: Optional[str] = None
    ) -> bool:
        """Set up push notifications for this client.

        Args:
            callback_url: The callback URL/queue/topic for notifications
            auth_token: Optional authentication token

        Returns:
            True if setup was successful, False otherwise

        Raises:
            TransportError: If setup fails due to transport issues
        """
        try:
            return await self.transport.setup_push_notifications(callback_url, auth_token)
        except Exception as e:
            logger.error(f"Failed to setup push notifications: {e}")
            raise TransportError(f"Push notification setup failed: {e}") from e

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        try:
            await self.transport.close()
        except Exception as e:
            logger.error(f"Error closing transport: {e}")

    async def health_check(self) -> bool:
        """Perform a health check on the client connection.

        Returns:
            True if the client is healthy, False otherwise
        """
        try:
            return await self.transport.health_check()
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    def get_transport_info(self) -> dict[str, Any]:
        """Get information about the current transport.

        Returns:
            Dictionary containing transport information
        """
        transport_type = type(self.transport).__name__
        info = {"transport_type": transport_type}
        
        # Add transport-specific info if available
        if hasattr(self.transport, 'get_transport_info'):
            info.update(self.transport.get_transport_info())
            
        return info