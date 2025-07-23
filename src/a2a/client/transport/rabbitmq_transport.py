"""RabbitMQ transport implementation for A2A client."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Dict, Optional
from uuid import uuid4

try:
    import aio_pika
    from aio_pika import Connection, Channel, Queue, Exchange, Message
    from aio_pika.abc import AbstractIncomingMessage
except ImportError:
    raise ImportError(
        "RabbitMQ transport requires 'aio-pika'. Install with: pip install 'a2a-sdk[rabbitmq]'"
    )

from .base import ClientTransport, TransportError, ProtocolError, TimeoutError, ConnectionError

logger = logging.getLogger(__name__)


class RabbitMQTransportConfig:
    """Configuration for RabbitMQ transport."""
    
    def __init__(
        self,
        connection_url: str = "amqp://localhost",
        agent_id: str = "default",
        max_retries: int = 3,
        retry_delay: float = 1.0,
        request_timeout: float = 30.0,
        connection_pool_size: int = 10,
    ):
        self.connection_url = connection_url
        self.agent_id = agent_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.request_timeout = request_timeout
        self.connection_pool_size = connection_pool_size


class RabbitMQTransport(ClientTransport):
    """RabbitMQ-based transport implementation for A2A client.
    
    This transport uses RabbitMQ message queues for communication:
    - Request/Response: Direct reply-to pattern
    - Streaming: Fanout exchange with temporary queues
    - Push Notifications: Dedicated notification queues
    """

    def __init__(self, config: RabbitMQTransportConfig):
        """Initialize RabbitMQ transport.
        
        Args:
            config: RabbitMQ transport configuration
        """
        self.config = config
        self._connection: Optional[Connection] = None
        self._channel: Optional[Channel] = None
        self._request_queue_name = f"agent.requests.{config.agent_id}"
        self._client_id = str(uuid4())
        self._push_notification_queue: Optional[str] = None
        self._active_streams: Dict[str, asyncio.Task] = {}

    async def _ensure_connection(self) -> None:
        """Ensure we have a valid connection and channel."""
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(
                self.config.connection_url,
                client_properties={"connection_name": f"a2a-client-{self._client_id}"}
            )
            self._channel = await self._connection.channel()
            # Set QoS for fair dispatch
            await self._channel.set_qos(prefetch_count=1)

    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a request using RabbitMQ direct reply-to pattern."""
        await self._ensure_connection()
        
        request_id = str(uuid4())
        correlation_id = str(uuid4())
        
        # Prepare JSON-RPC request
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id
        }

        try:
            # Create response future
            response_future = asyncio.Future()
            
            # Set up reply-to consumer
            reply_queue = await self._channel.declare_queue(exclusive=True)
            
            async def on_response(message: AbstractIncomingMessage):
                async with message.process():
                    if message.correlation_id == correlation_id:
                        try:
                            response_data = json.loads(message.body.decode())
                            if not response_future.done():
                                response_future.set_result(response_data)
                        except json.JSONDecodeError as e:
                            if not response_future.done():
                                response_future.set_exception(ProtocolError(f"Invalid JSON response: {e}"))

            # Start consuming responses
            await reply_queue.consume(on_response)
            
            # Send request
            message = Message(
                json.dumps(payload).encode(),
                correlation_id=correlation_id,
                reply_to=reply_queue.name,
                content_type="application/json"
            )
            
            await self._channel.default_exchange.publish(
                message,
                routing_key=self._request_queue_name
            )
            
            # Wait for response with timeout
            try:
                response_data = await asyncio.wait_for(
                    response_future,
                    timeout=self.config.request_timeout
                )
            except asyncio.TimeoutError:
                raise TimeoutError(f"Request timeout after {self.config.request_timeout}s")
            
            # Handle JSON-RPC error responses
            if "error" in response_data:
                error = response_data["error"]
                raise ProtocolError(
                    f"JSON-RPC error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}"
                )
            
            return response_data.get("result", {})
            
        except aio_pika.exceptions.AMQPException as e:
            raise ConnectionError(f"AMQP error: {e}") from e

    async def stream_request(
        self, method: str, params: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Send a streaming request using RabbitMQ fanout exchange."""
        await self._ensure_connection()
        
        request_id = str(uuid4())
        task_id = params.get("task_id") or str(uuid4())
        
        # First, send initial request to establish stream
        initial_response = await self.send_request(method, params)
        
        # Create temporary queue for stream events
        stream_queue_name = f"client.stream.{self._client_id}.{task_id}"
        stream_queue = await self._channel.declare_queue(
            stream_queue_name,
            exclusive=True,
            auto_delete=True,
            arguments={"x-message-ttl": 60000}  # 1 minute TTL
        )
        
        # Bind to stream events exchange
        stream_exchange_name = f"stream.events.{task_id}"
        stream_exchange = await self._channel.declare_exchange(
            stream_exchange_name,
            aio_pika.ExchangeType.FANOUT,
            auto_delete=True
        )
        
        await stream_queue.bind(stream_exchange)
        
        # Yield initial response
        yield initial_response
        
        try:
            # Stream events
            async for message in stream_queue:
                async with message.process():
                    try:
                        event_data = json.loads(message.body.decode())
                        
                        # Handle JSON-RPC error in stream
                        if "error" in event_data:
                            error = event_data["error"]
                            raise ProtocolError(
                                f"JSON-RPC stream error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}"
                            )
                        
                        result = event_data.get("result", event_data)
                        yield result
                        
                        # Check if this is the final event
                        if result.get("final") is True:
                            break
                            
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON in stream event: {e}")
                        continue
                        
        except aio_pika.exceptions.AMQPException as e:
            raise ConnectionError(f"AMQP error during streaming: {e}") from e

    async def setup_push_notifications(
        self, callback_url: str, auth_token: Optional[str] = None
    ) -> bool:
        """Set up push notification queue.
        
        For RabbitMQ transport, callback_url should be a queue name.
        """
        await self._ensure_connection()
        
        try:
            # Use callback_url as queue name, or generate one
            if callback_url.startswith("amqp://") or callback_url.startswith("rabbitmq://"):
                # Extract queue name from URL if provided
                self._push_notification_queue = f"notifications.{self._client_id}"
            else:
                # Use callback_url as queue name directly
                self._push_notification_queue = callback_url
            
            # Declare the notification queue
            notification_queue = await self._channel.declare_queue(
                self._push_notification_queue,
                durable=True,
                arguments={"x-message-ttl": 300000}  # 5 minutes TTL
            )
            
            logger.info(f"Push notification queue setup: {self._push_notification_queue}")
            return True
            
        except aio_pika.exceptions.AMQPException as e:
            logger.error(f"Failed to setup push notifications: {e}")
            return False

    async def close(self) -> None:
        """Close RabbitMQ connection and cleanup resources."""
        # Cancel active streams
        for task in self._active_streams.values():
            task.cancel()
        self._active_streams.clear()
        
        # Close channel and connection
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
            self._channel = None
            
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            self._connection = None

    async def health_check(self) -> bool:
        """Check if RabbitMQ connection is healthy."""
        try:
            await self._ensure_connection()
            return (
                self._connection is not None and 
                not self._connection.is_closed and
                self._channel is not None and
                not self._channel.is_closed
            )
        except Exception as e:
            logger.debug(f"RabbitMQ health check failed: {e}")
            return False

    def get_push_notification_queue(self) -> Optional[str]:
        """Get the configured push notification queue name."""
        return self._push_notification_queue

    async def consume_push_notifications(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Consume push notifications from the configured queue."""
        if not self._push_notification_queue:
            raise ValueError("Push notifications not configured. Call setup_push_notifications() first.")
        
        await self._ensure_connection()
        
        try:
            notification_queue = await self._channel.declare_queue(
                self._push_notification_queue,
                durable=True
            )
            
            async for message in notification_queue:
                async with message.process():
                    try:
                        notification_data = json.loads(message.body.decode())
                        yield notification_data
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON in push notification: {e}")
                        continue
                        
        except aio_pika.exceptions.AMQPException as e:
            raise ConnectionError(f"AMQP error consuming push notifications: {e}") from e