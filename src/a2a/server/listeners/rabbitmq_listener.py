"""RabbitMQ listener implementation for A2A server."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Optional, Dict, Any
from uuid import uuid4

try:
    import aio_pika
    from aio_pika import Connection, Channel, Queue, Exchange, Message
    from aio_pika.abc import AbstractIncomingMessage
except ImportError:
    raise ImportError(
        "RabbitMQ listener requires 'aio-pika'. Install with: pip install 'a2a-sdk[rabbitmq]'"
    )

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.tasks.push_notification_sender import BasePushNotificationSender
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.types import (
    MessageSendParams,
    TaskQueryParams,
    TaskIdParams,
    TaskPushNotificationConfig,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    DeleteTaskPushNotificationConfigParams,
)

from .base import ServerListener, ListenerError, ListenerStartError, ListenerStopError

logger = logging.getLogger(__name__)


class RabbitMQListenerConfig:
    """Configuration for RabbitMQ listener."""
    
    def __init__(
        self,
        connection_url: str = "amqp://localhost",
        agent_id: str = "default",
        max_workers: int = 10,
        prefetch_count: int = 1,
        queue_arguments: Optional[Dict[str, Any]] = None,
    ):
        self.connection_url = connection_url
        self.agent_id = agent_id
        self.max_workers = max_workers
        self.prefetch_count = prefetch_count
        self.queue_arguments = queue_arguments or {
            "x-message-ttl": 300000,  # 5 minutes TTL
            "x-dead-letter-exchange": f"dlx.requests.{agent_id}"
        }


class RabbitMQListener(ServerListener):
    """RabbitMQ listener implementation for A2A server.
    
    This listener consumes requests from RabbitMQ queues and processes
    them using the provided RequestHandler.
    """

    def __init__(self, config: RabbitMQListenerConfig):
        """Initialize RabbitMQ listener.
        
        Args:
            config: RabbitMQ listener configuration
        """
        self.config = config
        self._connection: Optional[Connection] = None
        self._channel: Optional[Channel] = None
        self._request_queue: Optional[Queue] = None
        self._handler: Optional[RequestHandler] = None
        self._push_notification_sender: Optional[BasePushNotificationSender] = None
        self._is_running = False
        self._consumer_tasks: list[asyncio.Task] = []
        self._stream_exchanges: Dict[str, Exchange] = {}

    async def _ensure_connection(self) -> None:
        """Ensure we have a valid connection and channel."""
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(
                self.config.connection_url,
                client_properties={"connection_name": f"a2a-server-{self.config.agent_id}"}
            )
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=self.config.prefetch_count)

    async def start(
        self, 
        handler: RequestHandler,
        push_notification_sender: Optional[BasePushNotificationSender] = None
    ) -> None:
        """Start the RabbitMQ listener."""
        if self._is_running:
            raise ListenerStartError("RabbitMQ listener is already running", transport="RabbitMQ")

        try:
            self._handler = handler
            self._push_notification_sender = push_notification_sender
            
            await self._ensure_connection()
            
            # Declare request queue
            request_queue_name = f"agent.requests.{self.config.agent_id}"
            self._request_queue = await self._channel.declare_queue(
                request_queue_name,
                durable=True,
                arguments=self.config.queue_arguments
            )
            
            # Declare dead letter exchange
            dlx_name = f"dlx.requests.{self.config.agent_id}"
            await self._channel.declare_exchange(
                dlx_name,
                aio_pika.ExchangeType.DIRECT,
                durable=True
            )
            
            # Start consuming requests
            await self._request_queue.consume(self._handle_request)
            self._is_running = True
            
            logger.info(f"RabbitMQ listener started for agent {self.config.agent_id}")

        except Exception as e:
            self._is_running = False
            raise ListenerStartError(f"Failed to start RabbitMQ listener: {e}", transport="RabbitMQ") from e

    async def stop(self) -> None:
        """Stop the RabbitMQ listener."""
        if not self._is_running:
            return

        try:
            self._is_running = False
            
            # Cancel consumer tasks
            for task in self._consumer_tasks:
                task.cancel()
            
            if self._consumer_tasks:
                await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
            self._consumer_tasks.clear()
            
            # Close channel and connection
            if self._channel and not self._channel.is_closed:
                await self._channel.close()
                self._channel = None
                
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
                self._connection = None

            self._request_queue = None
            self._handler = None
            self._push_notification_sender = None
            self._stream_exchanges.clear()
            
            logger.info("RabbitMQ listener stopped")

        except Exception as e:
            raise ListenerStopError(f"Failed to stop RabbitMQ listener: {e}", transport="RabbitMQ") from e

    async def _handle_request(self, message: AbstractIncomingMessage) -> None:
        """Handle incoming RabbitMQ request message."""
        async with message.process():
            try:
                # Parse JSON-RPC request
                request_data = json.loads(message.body.decode())
                method = request_data.get("method")
                params = request_data.get("params", {})
                request_id = request_data.get("id")
                
                if not method:
                    await self._send_error_response(message, -32600, "Invalid Request", request_id)
                    return
                
                # Create server context
                context = ServerCallContext(
                    transport="RabbitMQ",
                    metadata={"correlation_id": message.correlation_id}
                )
                
                # Route request to appropriate handler method
                try:
                    if method == "message/send":
                        result = await self._handle_message_send(params, context)
                        await self._send_response(message, result, request_id)
                        
                    elif method == "message/stream":
                        await self._handle_message_stream(message, params, context, request_id)
                        
                    elif method == "tasks/get":
                        result = await self._handle_get_task(params, context)
                        await self._send_response(message, result, request_id)
                        
                    elif method == "tasks/cancel":
                        result = await self._handle_cancel_task(params, context)
                        await self._send_response(message, result, request_id)
                        
                    elif method == "tasks/pushNotificationConfig/set":
                        result = await self._handle_set_push_notification_config(params, context)
                        await self._send_response(message, result, request_id)
                        
                    elif method == "tasks/pushNotificationConfig/get":
                        result = await self._handle_get_push_notification_config(params, context)
                        await self._send_response(message, result, request_id)
                        
                    else:
                        await self._send_error_response(message, -32601, "Method not found", request_id)
                        
                except Exception as e:
                    logger.error(f"Error handling request {method}: {e}")
                    await self._send_error_response(message, -32603, f"Internal error: {e}", request_id)
                    
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in request: {e}")
                await self._send_error_response(message, -32700, "Parse error", None)
            except Exception as e:
                logger.error(f"Unexpected error handling request: {e}")

    async def _handle_message_send(self, params: Dict[str, Any], context: ServerCallContext):
        """Handle message/send request."""
        message_params = MessageSendParams.model_validate(params)
        result = await self._handler.on_message_send(message_params, context)
        return result.model_dump(mode='json', exclude_none=True)

    async def _handle_message_stream(
        self, 
        message: AbstractIncomingMessage, 
        params: Dict[str, Any], 
        context: ServerCallContext,
        request_id: str
    ):
        """Handle message/stream request with fanout exchange."""
        message_params = MessageSendParams.model_validate(params)
        
        # Send initial response to acknowledge stream start
        task_id = str(uuid4())  # Generate task ID if not provided
        initial_response = {"task_id": task_id, "status": "started"}
        await self._send_response(message, initial_response, request_id)
        
        # Create fanout exchange for this task
        exchange_name = f"stream.events.{task_id}"
        stream_exchange = await self._channel.declare_exchange(
            exchange_name,
            aio_pika.ExchangeType.FANOUT,
            auto_delete=True
        )
        self._stream_exchanges[task_id] = stream_exchange
        
        # Start streaming in background task
        stream_task = asyncio.create_task(
            self._stream_events(stream_exchange, message_params, context)
        )
        self._consumer_tasks.append(stream_task)

    async def _stream_events(
        self, 
        exchange: Exchange, 
        params: MessageSendParams, 
        context: ServerCallContext
    ):
        """Stream events to fanout exchange."""
        try:
            async for event in self._handler.on_message_send_stream(params, context):
                event_data = self._serialize_event(event)
                
                stream_message = Message(
                    json.dumps(event_data).encode(),
                    content_type="application/json"
                )
                
                await exchange.publish(stream_message, routing_key="")
                
                # Check if this is the final event
                if event_data.get("final") is True:
                    break
                    
        except Exception as e:
            logger.error(f"Error streaming events: {e}")

    async def _handle_get_task(self, params: Dict[str, Any], context: ServerCallContext):
        """Handle tasks/get request."""
        task_params = TaskQueryParams.model_validate(params)
        result = await self._handler.on_get_task(task_params, context)
        return result.model_dump(mode='json', exclude_none=True) if result else None

    async def _handle_cancel_task(self, params: Dict[str, Any], context: ServerCallContext):
        """Handle tasks/cancel request."""
        task_params = TaskIdParams.model_validate(params)
        result = await self._handler.on_cancel_task(task_params, context)
        return result.model_dump(mode='json', exclude_none=True) if result else None

    async def _handle_set_push_notification_config(self, params: Dict[str, Any], context: ServerCallContext):
        """Handle tasks/pushNotificationConfig/set request."""
        config_params = TaskPushNotificationConfig.model_validate(params)
        result = await self._handler.on_set_task_push_notification_config(config_params, context)
        return result.model_dump(mode='json', exclude_none=True)

    async def _handle_get_push_notification_config(self, params: Dict[str, Any], context: ServerCallContext):
        """Handle tasks/pushNotificationConfig/get request."""
        config_params = GetTaskPushNotificationConfigParams.model_validate(params)
        result = await self._handler.on_get_task_push_notification_config(config_params, context)
        return result.model_dump(mode='json', exclude_none=True)

    async def _send_response(self, request_message: AbstractIncomingMessage, result: Any, request_id: str):
        """Send JSON-RPC response."""
        if not request_message.reply_to:
            return
            
        response_data = {
            "jsonrpc": "2.0",
            "result": result,
            "id": request_id
        }
        
        response_message = Message(
            json.dumps(response_data).encode(),
            correlation_id=request_message.correlation_id,
            content_type="application/json"
        )
        
        await self._channel.default_exchange.publish(
            response_message,
            routing_key=request_message.reply_to
        )

    async def _send_error_response(
        self, 
        request_message: AbstractIncomingMessage, 
        error_code: int, 
        error_message: str, 
        request_id: Optional[str]
    ):
        """Send JSON-RPC error response."""
        if not request_message.reply_to:
            return
            
        error_data = {
            "jsonrpc": "2.0",
            "error": {
                "code": error_code,
                "message": error_message
            },
            "id": request_id
        }
        
        response_message = Message(
            json.dumps(error_data).encode(),
            correlation_id=request_message.correlation_id,
            content_type="application/json"
        )
        
        await self._channel.default_exchange.publish(
            response_message,
            routing_key=request_message.reply_to
        )

    def _serialize_event(self, event: Event) -> Dict[str, Any]:
        """Serialize event for transmission."""
        if hasattr(event, 'model_dump'):
            return event.model_dump(mode='json', exclude_none=True)
        else:
            return {"type": type(event).__name__, "data": str(event)}

    def get_transport_info(self) -> Dict[str, Any]:
        """Get RabbitMQ transport information."""
        return {
            "transport": "RabbitMQ",
            "connection_url": self.config.connection_url,
            "agent_id": self.config.agent_id,
            "request_queue": f"agent.requests.{self.config.agent_id}",
        }

    async def health_check(self) -> bool:
        """Check if RabbitMQ listener is healthy."""
        try:
            return (
                self._is_running and
                self._connection is not None and 
                not self._connection.is_closed and
                self._channel is not None and
                not self._channel.is_closed
            )
        except Exception as e:
            logger.debug(f"RabbitMQ health check failed: {e}")
            return False

    def is_running(self) -> bool:
        """Check if RabbitMQ listener is running."""
        return self._is_running