import json
import logging
from typing import Any

import aio_pika
from aio_pika import Message as RabbitMessage
from aio_pika.abc import AbstractIncomingMessage

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    DeleteTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    MessageSendParams,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
)

logger = logging.getLogger(__name__)


class RabbitMQHandler:
    """RabbitMQ request handler that processes incoming RPC requests."""

    def __init__(self, request_handler: RequestHandler):
        """
        Initialize the RabbitMQ handler.
        
        Args:
            request_handler: The request handler to delegate business logic to.
        """
        self._request_handler = request_handler
        self._streaming_routes: dict[str, str] = {}  # Maps task_id to routing_key
        self._channel = None  # Will be set by the server app
        self._streaming_exchange = None  # Will be set by the server app
        self._push_notification_exchange = None  # Will be set by the server app

    async def handle_rpc_request(self, message: AbstractIncomingMessage) -> None:
        """Handle an incoming RPC request message."""
        async with message.process():
            try:
                # Parse request
                request_data = json.loads(message.body.decode())
                method = request_data.get("method")
                params = request_data.get("params", {})
                request_id = request_data.get("id")
                
                # Create server context
                context = ServerCallContext()
                
                # Route to appropriate handler
                response_data = await self._route_request(method, params, context)
                
                # Send response
                await self._send_response(message, request_id, response_data)
                
            except Exception as e:
                logger.exception("Error handling RPC request")
                request_id = None
                try:
                    request_data = json.loads(message.body.decode())
                    request_id = request_data.get("id")
                except Exception:
                    pass
                    
                error_response = {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": str(e)
                    },
                    "id": request_id
                }
                await self._send_response(message, None, error_response)

    async def _route_request(self, method: str, params: dict[str, Any], context: ServerCallContext) -> dict[str, Any]:
        """Route the request to the appropriate handler method."""
        if method == "message/send":
            message_params = MessageSendParams.model_validate(params)
            result = await self._request_handler.on_message_send(message_params, context)
            return {"jsonrpc": "2.0", "result": result.model_dump()}
            
        elif method == "message/stream":
            message_params = MessageSendParams.model_validate(params)
            
            # Extract routing key from metadata
            routing_key = params.get("metadata", {}).get("routing_key")
            if not routing_key:
                raise ValueError("Streaming request missing routing_key in metadata")
            
            # Start streaming
            initial_result = await self._handle_streaming_request(message_params, routing_key, context)
            return {"jsonrpc": "2.0", "result": initial_result.model_dump() if initial_result else None}
            
        elif method == "tasks/get":
            task_params = TaskQueryParams.model_validate(params)
            result = await self._request_handler.on_get_task(task_params, context)
            return {"jsonrpc": "2.0", "result": result.model_dump() if result else None}
            
        elif method == "tasks/cancel":
            task_params = TaskIdParams.model_validate(params)
            result = await self._request_handler.on_cancel_task(task_params, context)
            return {"jsonrpc": "2.0", "result": result.model_dump() if result else None}
            
        elif method == "tasks/pushNotificationConfig/set":
            config_params = TaskPushNotificationConfig.model_validate(params)
            result = await self._request_handler.on_set_task_push_notification_config(config_params, context)
            return {"jsonrpc": "2.0", "result": result.model_dump() if result else None}
            
        elif method == "tasks/pushNotificationConfig/get":
            get_params = GetTaskPushNotificationConfigParams.model_validate(params)
            result = await self._request_handler.on_get_task_push_notification_config(get_params, context)
            return {"jsonrpc": "2.0", "result": result.model_dump() if result else None}
            
        elif method == "tasks/pushNotificationConfig/list":
            list_params = ListTaskPushNotificationConfigParams.model_validate(params)
            result = await self._request_handler.on_list_task_push_notification_config(list_params, context)
            return {"jsonrpc": "2.0", "result": [config.model_dump() for config in result] if result else []}
            
        elif method == "tasks/pushNotificationConfig/delete":
            delete_params = DeleteTaskPushNotificationConfigParams.model_validate(params)
            result = await self._request_handler.on_delete_task_push_notification_config(delete_params, context)
            return {"jsonrpc": "2.0", "result": result.model_dump() if result else None}
            
        elif method == "tasks/resubscribe":
            task_params = TaskIdParams.model_validate(params)
            routing_key = params.get("routing_key")
            if not routing_key:
                raise ValueError("Resubscribe request missing routing_key")
            
            # Store routing key for task
            self._streaming_routes[task_params.id] = routing_key
            
            # Get current task state
            result = await self._request_handler.on_get_task(
                TaskQueryParams(id=task_params.id), context
            )
            return {"jsonrpc": "2.0", "result": result.model_dump() if result else None}
            
        elif method == "agent/getCard":
            # This method might not exist in the base RequestHandler
            # For now, return an error
            raise ValueError("agent/getCard method not implemented")
            
        else:
            raise ValueError(f"Unknown method: {method}")

    async def _handle_streaming_request(
        self, 
        params: MessageSendParams, 
        routing_key: str, 
        context: ServerCallContext
    ) -> Any:
        """Handle a streaming message request."""
        # Get the initial result
        async for event in self._request_handler.on_message_send_stream(params, context):
            # For the first event, store routing key association if it's a Task
            if hasattr(event, 'id') and hasattr(event, 'kind'):
                kind = getattr(event, 'kind', None)
                if kind == 'task':
                    # This is a Task object, store the routing key
                    task_id = getattr(event, 'id')
                    self._streaming_routes[task_id] = routing_key
            
            # Handle the event through stream events
            await self.handle_stream_events(event)
            
            # Return first event as initial response
            if hasattr(event, 'model_dump'):
                return event
            else:
                return None
        
        return None

    async def handle_stream_events(self, event: Event) -> None:
        """Handle streaming events by publishing them to the appropriate routing key."""
        if not self._channel or not self._streaming_exchange:
            return
        
        import json
        
        # Determine the task_id from the event
        task_id = None
        if hasattr(event, 'id') and hasattr(event, 'kind') and getattr(event, 'kind') == 'task':
            task_id = getattr(event, 'id')
        elif hasattr(event, 'task_id'):
            task_id = getattr(event, 'task_id', None)
        
        if not task_id:
            return
        
        # Get routing key for this task
        routing_key = self._streaming_routes.get(task_id)
        if not routing_key:
            return
        
        # Serialize event
        event_data = event.model_dump() if hasattr(event, 'model_dump') else {}
        message_body = json.dumps(event_data).encode()
        
        # Create and publish message
        message = aio_pika.Message(message_body)
        await self._streaming_exchange.publish(message, routing_key=routing_key)

    async def _send_response(
        self, 
        incoming_message: AbstractIncomingMessage, 
        request_id: Any, 
        response_data: dict[str, Any]
    ) -> None:
        """Send a response back to the client."""
        if not incoming_message.reply_to:
            return
        
        # Add request ID to response
        if request_id is not None:
            response_data["id"] = request_id
        
        # Serialize response
        response_body = json.dumps(response_data).encode()
        
        # Create response message
        response_message = RabbitMessage(
            response_body,
            correlation_id=incoming_message.correlation_id,
        )
        
        # Get channel from incoming message and publish response
        # Note: This requires access to the channel, which should be passed
        # or stored in the handler during initialization
        channel = incoming_message.channel
        await channel.default_exchange.publish(
            response_message,
            routing_key=incoming_message.reply_to
        )
